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


class MemoryHub:

    def __init__(self):
        self.short_term_buffer = ShortTermMemoryBuffer()
        self.vector_store = VectorMemoryStore()
        self.profile_mgr = UserProfileManager()
        self.consolidator = MemoryConsolidator()
        self.token_budget = TokenBudgetManager()
        self.self_memory = AgentSelfMemory()

    async def handle_inbound_message(
            self, payload: InboundMessagePayload) -> None:
        content_text = payload.raw_text or ""
        if payload.media_type == "photo" and payload.file_path:
            content_text = f"[主人发送了一张照片: {payload.file_path}] {content_text}".strip()

        if content_text:
            self.short_term_buffer.add_message(
                user_id=payload.user_id,
                role="user",
                content=content_text,
                metadata={
                    "message_id": payload.message_id,
                    "media_type": payload.media_type,
                    "photo_path": payload.file_path,
                },
            )

    async def handle_action_completed(
            self, payload: ActionCompletedPayload) -> None:
        decision = payload.action_decision
        content_parts = []
        if decision.action_type == "send_photo" and decision.photo_path:
            content_parts.append(f"[猫娘已发送照片: {decision.photo_path}]")

        if decision.text_content:
            content_parts.append(decision.text_content)

        full_content = " ".join(content_parts)
        if full_content:
            self.short_term_buffer.add_message(
                user_id=payload.chat_id,
                role="assistant",
                content=full_content,
                metadata={
                    "status": payload.status,
                    "action_type": decision.action_type,
                    "photo_path": decision.photo_path,
                },
            )

    async def handle_enrich_context_req(
            self, payload: EnrichContextReqPayload) -> ReasoningRequestPayload:
        # Record inbound text into short term buffer if provided
        if payload.inbound_message and payload.inbound_message.raw_text:
            await self.handle_inbound_message(payload.inbound_message)

        user_id = payload.user_id
        chat_id = payload.chat_id

        # 1. Fetch User Profile
        profile = self.profile_mgr.get_profile(user_id)

        # 2. RAG retrieval
        query_text = (payload.inbound_message.raw_text
                      if payload.inbound_message and
                      payload.inbound_message.raw_text else "")
        rag_facts = await self.vector_store.search_relevant_memories(
            user_id, query_text)

        # 3. Fetch recent short-term history
        history = self.short_term_buffer.get_recent_messages(user_id)

        # Build ReasoningRequestPayload
        return ReasoningRequestPayload(
            event_id=payload.event_id,
            source_component="memory_hub",
            chat_id=chat_id,
            user_id=user_id,
            short_term_history=history,
            user_profile=profile,
            rag_facts=rag_facts,
            current_emotion=payload.emotion_description,
            inbound_message=payload.inbound_message,
            trigger_type=payload.trigger_type,
        )
