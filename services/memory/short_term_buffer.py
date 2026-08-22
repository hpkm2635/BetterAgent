import json
import logging
import os
import collections
from typing import List, Dict, Any, Optional
from shared.config_loader import get_config_val

logger = logging.getLogger("short_term_buffer")


class ShortTermMemoryBuffer:

    def __init__(self, max_capacity: int = 20):
        self.max_capacity = max_capacity
        self.buffers: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
        self.cursors: Dict[int, int] = collections.defaultdict(int)
        self.redis_client = None
        self._redis_disabled = False

        # 127.0.0.1, not "localhost" -- see docs/SECURITY.md §2.8.
        redis_url = get_config_val("infrastructure.redis_url", os.getenv("REDIS_URL", "redis://127.0.0.1:6379"))
        redis_password = os.getenv("REDIS_PASSWORD")
        if not redis_password:
            logger.warning("REDIS_PASSWORD is not set -- connecting to Redis without authentication (see .env.example)")

        try:
            import redis.asyncio as aioredis
            self.redis_client = aioredis.from_url(
                redis_url,
                password=redis_password,
                decode_responses=True,
                socket_connect_timeout=0.2,
                protocol=2,
            )
            logger.info(f"ShortTermMemoryBuffer configured Async Redis storage at {redis_url}")
        except Exception as e:
            logger.warning(f"Async Redis unavailable ({e}), ShortTermMemoryBuffer falling back to in-memory dict storage")
            self.redis_client = None
            self._redis_disabled = True

    def _get_key(self, user_id: int) -> str:
        return f"betteragent:short_term:{int(user_id)}"

    def _get_cursor_key(self, user_id: int) -> str:
        return f"betteragent:consolidate_cursor:{int(user_id)}"

    def _handle_redis_error(self, e: Exception) -> None:
        if not self._redis_disabled:
            logger.warning(f"Redis error in ShortTermMemoryBuffer ({e}), disabling Redis for current process.")
            self._redis_disabled = True
            self.redis_client = None

    async def add_message(
        self,
        user_id: int,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        user_id_int = int(user_id)
        msg = {
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }

        # Always update local buffer for fast in-memory access
        self.buffers[user_id_int].append(msg)
        if len(self.buffers[user_id_int]) > self.max_capacity:
            self.buffers[user_id_int].pop(0)
            if self.cursors[user_id_int] > 0:
                self.cursors[user_id_int] -= 1

        # Persist asynchronously to Redis if connected and active
        if self.redis_client and not self._redis_disabled:
            try:
                key = self._get_key(user_id_int)
                await self.redis_client.rpush(key, json.dumps(msg, ensure_ascii=False))
                await self.redis_client.ltrim(key, -self.max_capacity, -1)
                await self.redis_client.expire(key, 86400)  # 24h TTL
            except Exception as e:
                self._handle_redis_error(e)

    async def get_recent_messages(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        user_id_int = int(user_id)
        if self.redis_client and not self._redis_disabled:
            try:
                key = self._get_key(user_id_int)
                items = await self.redis_client.lrange(key, -limit, -1)
                if items:
                    return [json.loads(it) for it in items]
            except Exception as e:
                self._handle_redis_error(e)

        # In-memory fallback
        return self.buffers[user_id_int][-limit:]

    async def get_unconsolidated_messages(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_int = int(user_id)
        all_msgs = await self.get_recent_messages(user_id_int, limit=self.max_capacity)

        cursor = self.cursors[user_id_int]
        if self.redis_client and not self._redis_disabled:
            try:
                raw_cursor = await self.redis_client.get(self._get_cursor_key(user_id_int))
                if raw_cursor is not None:
                    cursor = int(raw_cursor)
            except Exception as e:
                self._handle_redis_error(e)

        if cursor >= len(all_msgs):
            return []
        return all_msgs[cursor:]

    async def mark_consolidated(self, user_id: int, count: int) -> None:
        user_id_int = int(user_id)
        all_msgs = await self.get_recent_messages(user_id_int, limit=self.max_capacity)
        new_cursor = min(len(all_msgs), self.cursors[user_id_int] + count)
        self.cursors[user_id_int] = new_cursor

        if self.redis_client and not self._redis_disabled:
            try:
                c_key = self._get_cursor_key(user_id_int)
                await self.redis_client.set(c_key, new_cursor, ex=86400)
            except Exception as e:
                self._handle_redis_error(e)

    async def clear_buffer(self, user_id: int) -> None:
        user_id_int = int(user_id)
        self.buffers[user_id_int].clear()
        self.cursors[user_id_int] = 0
        if self.redis_client and not self._redis_disabled:
            try:
                key = self._get_key(user_id_int)
                c_key = self._get_cursor_key(user_id_int)
                await self.redis_client.delete(key)
                await self.redis_client.delete(c_key)
            except Exception as e:
                self._handle_redis_error(e)
