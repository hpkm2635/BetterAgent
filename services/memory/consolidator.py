import asyncio
from typing import List, Dict, Any


class MemoryConsolidator:

    def __init__(self, consolidation_threshold: int = 15):
        self.consolidation_threshold = consolidation_threshold
        self._user_locks: Dict[int, asyncio.Lock] = {}

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def consolidate(self, user_id: int,
                          messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        lock = self.get_user_lock(user_id)
        async with lock:
            # Consolidate short term messages into long term vector facts
            extracted_facts = [
                msg["content"] for msg in messages if len(msg.get("content", "")) > 10
            ]
            return {
                "user_id": user_id,
                "consolidated_count": len(messages),
                "facts": extracted_facts,
            }
