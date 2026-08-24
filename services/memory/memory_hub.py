import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from shared.schema.payloads import (
    EnrichContextReqPayload,
    ReasoningRequestPayload,
    InboundMessagePayload,
    ActionCompletedPayload,
)
from services.memory.short_term_buffer import ShortTermMemoryBuffer
from services.memory.vector_store import VectorMemoryStore
from services.memory.user_profile import UserProfileManager
from services.memory.consolidator import MemoryConsolidator
from services.memory.token_budget import TokenBudgetManager
from services.memory.agent_self_memory import AgentSelfMemory
from shared.config_loader import get_config_val

logger = logging.getLogger("memory_hub")


class MemoryHub:

    def __init__(self):
        self.short_term_buffer = ShortTermMemoryBuffer()
        self.vector_store = VectorMemoryStore()
        self.profile_mgr = UserProfileManager()
        self.consolidator = MemoryConsolidator()
        self.token_budget = TokenBudgetManager()
        self.self_memory = AgentSelfMemory()

    async def consolidate_user_memory(self, user_id: int) -> Dict[str, Any]:
        """Consolidate unconsolidated short-term buffer messages into long-term vector facts."""
        user_id_int = int(user_id)
        unconsolidated = await self.short_term_buffer.get_unconsolidated_messages(user_id_int)
        if not unconsolidated:
            return {"user_id": user_id_int, "consolidated_count": 0, "facts": []}

        res = await self.consolidator.consolidate(
            user_id=user_id_int,
            messages=unconsolidated,
            vector_store=self.vector_store,
        )

        await self.short_term_buffer.mark_consolidated(user_id_int, len(unconsolidated))
        return res

    async def handle_inbound_message(self, payload: InboundMessagePayload) -> None:
        user_id = int(payload.user_id)
        content_text = payload.raw_text or ""
        if payload.media_type == "photo" and payload.file_path:
            content_text = f"[主人发送了一张照片: {payload.file_path}] {content_text}".strip()

        if content_text:
            await self.short_term_buffer.add_message(
                user_id=user_id,
                role="user",
                content=content_text,
                metadata={
                    "message_id": payload.message_id,
                    "media_type": payload.media_type,
                    "photo_path": payload.file_path,
                },
            )

        # Message Counter Trigger: Consolidate when unconsolidated buffer reaches threshold
        unconsolidated = await self.short_term_buffer.get_unconsolidated_messages(user_id)
        if len(unconsolidated) >= self.consolidator.consolidation_threshold:
            try:
                await self.consolidate_user_memory(user_id)
            except Exception as e:
                logger.warning(f"Auto consolidation failed for user_id={user_id}: {e}")

    async def handle_action_completed(self, payload: ActionCompletedPayload) -> None:
        chat_id = int(payload.chat_id)
        decision = payload.action_decision
        content_parts = []
        if decision.action_type == "send_photo" and decision.photo_path:
            content_parts.append(f"[助手已发送照片: {decision.photo_path}]")

        if decision.text_content:
            content_parts.append(decision.text_content)

        full_content = " ".join(content_parts)
        if full_content:
            await self.short_term_buffer.add_message(
                user_id=chat_id,
                role="assistant",
                content=full_content,
                metadata={
                    "status": payload.status,
                    "action_type": decision.action_type,
                    "photo_path": decision.photo_path,
                },
            )

        # Record event in agent self memory
        if decision.action_type:
            self.self_memory.add_self_event(
                chat_id=chat_id,
                description=f"执行动作 {decision.action_type}: {full_content[:30]}",
                emotion_tag="normal",
            )

        # 写入陪伴统计（Companion /api/companion/stat）：对话次数 + 话题日志
        await self._write_companion_stat(chat_id, full_content)

    @staticmethod
    def _detect_topic(text: str) -> Optional[str]:
        """从助手回复里用简单关键词规则提取话题标签（供 topic_log / 推荐使用）。"""
        if not text:
            return None
        study_kw = ("学习", "复习", "考试", "作业", "上课", "课程", "选课", "论文")
        game_kw = ("游戏", "杀戮尖塔", "打游戏", "开黑")
        for kw in study_kw:
            if kw in text:
                return "study"
        for kw in game_kw:
            if kw in text:
                return "game"
        return None

    async def _write_companion_stat(self, chat_id: int, text: str) -> None:
        """把每轮对话的统计（消息数 + 话题）写入 Companion 服务，供 NL2SQL / 推荐使用。"""
        companion_url = get_config_val(
            "infrastructure.companion_url",
            os.getenv("COMPANION_URL", "http://127.0.0.1:8096"),
        )
        topic = self._detect_topic(text)
        payload = {
            "chat_id": chat_id,
            "date": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            "is_proactive": False,
            "topic": topic,
        }
        try:
            session = await self.vector_store._get_http_session()
            async with session.post(
                f"{companion_url.rstrip('/')}/api/companion/stat",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Companion stat write failed ({resp.status})")
        except Exception as e:
            logger.warning(f"Companion stat write error ({e}), degrading gracefully.")

    async def search_campus_kb(self, query_text: str, top_k: int = 3) -> List[str]:
        if not query_text or not query_text.strip():
            return []
        kb_url = get_config_val("infrastructure.campus_kb_url", os.getenv("CAMPUS_KB_URL", "http://127.0.0.1:8093"))
        try:
            session = await self.vector_store._get_http_session()
            async with session.post(
                f"{kb_url.rstrip('/')}/api/kb/search",
                json={"query": query_text, "top_k": top_k},
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    facts = []
                    for r in results:
                        content = r.get("content", "")
                        source = r.get("source", "")
                        if content:
                            facts.append(f"{content} (来源: {source})" if source else content)
                    return facts
        except Exception as e:
            logger.warning(f"Campus KB search endpoint failure ({e}), falling back gracefully.")
        return []

    async def handle_enrich_context_req(
        self, payload: EnrichContextReqPayload
    ) -> ReasoningRequestPayload:
        user_id = int(payload.user_id)
        chat_id = int(payload.chat_id)

        # Note: Do NOT call handle_inbound_message here -- inbound messages are handled
        # exclusively via NATS agent.inbound_message to eliminate duplicate buffer writes.

        query_text = (
            payload.inbound_message.raw_text
            if payload.inbound_message and payload.inbound_message.raw_text
            else ""
        )

        # Concurrent retrieval: User Profile, History, RAG Memories, and Campus KB
        profile_task = self.profile_mgr.get_profile(user_id)
        history_task = self.short_term_buffer.get_recent_messages(user_id)
        rag_task = self.vector_store.search_relevant_memories(user_id, query_text)
        kb_task = self.search_campus_kb(query_text)

        profile_res, history_res, rag_res, kb_res = await asyncio.gather(
            profile_task, history_task, rag_task, kb_task, return_exceptions=True
        )

        profile = profile_res if isinstance(profile_res, dict) else {}
        history = history_res if isinstance(history_res, list) else []
        rag_facts = rag_res if isinstance(rag_res, list) else []
        kb_facts = kb_res if isinstance(kb_res, list) else []

        # Apply Token Budget Trimming
        _, trimmed_history, trimmed_facts, trimmed_kb_facts = self.token_budget.fit_into_budget(
            system_prompt="",
            history=history,
            profile=profile,
            rag_facts=rag_facts,
            kb_facts=kb_facts,
        )

        # Retrieve Agent Self Events
        self_events = self.self_memory.get_recent_self_events(chat_id, limit=3)

        # Build ReasoningRequestPayload
        return ReasoningRequestPayload(
            event_id=payload.event_id,
            source_component="memory_hub",
            chat_id=chat_id,
            user_id=user_id,
            generation_id=payload.generation_id,
            short_term_history=trimmed_history,
            user_profile=profile,
            rag_facts=trimmed_facts,
            kb_facts=trimmed_kb_facts,
            agent_self_events=self_events,
            current_emotion=payload.emotion_description,
            personality_description=payload.personality_description,
            circadian_description=payload.circadian_description,
            inbound_message=payload.inbound_message,
            trigger_type=payload.trigger_type,
            source_channel=payload.source_channel,
            proactive_reason=payload.proactive_reason,
            is_proactive_opportunity=payload.is_proactive_opportunity,
        )
