import asyncio
import logging
import re
import time
import weakref
from typing import List, Dict, Any, Optional
from services.memory.vector_store import VectorMemoryStore

logger = logging.getLogger("consolidator")

# Noise patterns to filter out casual chat / greetings / reactions
_NOISE_PATTERNS = re.compile(
    r"^(?:喵|喵喵|哈哈|嘻嘻|呵呵|嗯嗯|好的|没问题|收到|在吗|你好|早上好|晚安|天气|再见|bye|hi|hello|ok|tql|awsl)+$",
    re.IGNORECASE,
)

# Substantive indicators for user facts / intentions / statements
_KEY_FACT_INDICATORS = (
    "我是", "我喜欢", "我讨厌", "我的", "准备", "计划", "打算", "专业", "学校",
    "名字", "工作", "爱好", "生日", "明天", "以后", "希望", "觉得", "认为",
)


class MemoryConsolidator:

    def __init__(self, consolidation_threshold: int = 15):
        self.consolidation_threshold = consolidation_threshold
        # WeakValueDictionary automatically garbage-collects Lock objects when no coroutine references them
        self._user_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        user_id_int = int(user_id)
        lock = self._user_locks.get(user_id_int)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[user_id_int] = lock
        return lock

    async def extract_facts_with_fallback(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Extract high-quality semantic facts from conversation messages, filtering out noise."""
        if not messages:
            return []

        facts = []
        for msg in messages:
            content = (msg.get("content") or "").strip()
            role = msg.get("role", "")
            if not content or len(content) < 8:
                continue

            # Strip action descriptions or system tags
            clean_content = re.sub(r"\[[^\]]+\]", "", content).strip()
            if not clean_content:
                continue

            # Reject noisy / casual greetings / reactions
            if _NOISE_PATTERNS.match(clean_content):
                continue

            # Prioritize substantive user statements
            if role == "user":
                is_key_fact = any(ind in clean_content for ind in _KEY_FACT_INDICATORS)
                if is_key_fact or len(clean_content) >= 15:
                    facts.append(f"用户曾说明: {clean_content}")
            elif role == "assistant" and any(ind in clean_content for ind in ("约定", "决定", "记住", "提醒", "复习", "计划")):
                facts.append(f"约定/回应: {clean_content}")

        # Return top 5 distinct facts per consolidation run
        unique_facts = []
        seen = set()
        for f in facts:
            if f not in seen:
                seen.add(f)
                unique_facts.append(f)

        return unique_facts[:5]

    async def consolidate(
        self,
        user_id: int,
        messages: List[Dict[str, Any]],
        vector_store: Optional[VectorMemoryStore] = None,
    ) -> Dict[str, Any]:
        user_id_int = int(user_id)
        lock = self.get_user_lock(user_id_int)
        async with lock:
            if not messages:
                return {
                    "user_id": user_id_int,
                    "consolidated_count": 0,
                    "facts": [],
                }

            extracted_facts = await self.extract_facts_with_fallback(messages)
            stored_count = 0

            if vector_store and extracted_facts:
                for fact in extracted_facts:
                    try:
                        await vector_store.add_memory_segment(
                            user_id=user_id_int,
                            text=fact,
                            metadata={
                                "source": "consolidation",
                                "consolidated_at": time.time(),
                            },
                        )
                        stored_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to save consolidated fact to vector_store for user {user_id_int}: {e}")

            logger.info(
                f"Consolidated {len(messages)} messages for user {user_id_int} into {stored_count} vector memory facts."
            )

            return {
                "user_id": user_id_int,
                "consolidated_count": len(messages),
                "facts": extracted_facts,
                "stored_count": stored_count,
            }
