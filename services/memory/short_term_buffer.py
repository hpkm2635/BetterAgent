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
        self.redis_client = None

        redis_url = get_config_val("infrastructure.redis_url", os.getenv("REDIS_URL", "redis://localhost:6379"))
        try:
            import redis
            self.redis_client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
            self.redis_client.ping()
            logger.info(f"ShortTermMemoryBuffer connected to Redis persistent storage at {redis_url}")
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}), ShortTermMemoryBuffer falling back to in-memory dict storage")
            self.redis_client = None

    def _get_key(self, user_id: int) -> str:
        return f"betteragent:short_term:{user_id}"

    def add_message(self,
                    user_id: int,
                    role: str,
                    content: str,
                    metadata: Optional[Dict[str, Any]] = None) -> None:
        msg = {
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }

        # Always update local buffer for fast in-memory access
        self.buffers[user_id].append(msg)
        if len(self.buffers[user_id]) > self.max_capacity:
            self.buffers[user_id].pop(0)

        # Persist to Redis if connected
        if self.redis_client:
            try:
                key = self._get_key(user_id)
                self.redis_client.rpush(key, json.dumps(msg))
                self.redis_client.ltrim(key, -self.max_capacity, -1)
                self.redis_client.expire(key, 86400)  # 24h TTL
            except Exception as e:
                logger.warning(f"Failed to write message to Redis for user {user_id}: {e}")

    def get_recent_messages(self,
                            user_id: int,
                            limit: int = 20) -> List[Dict[str, Any]]:
        if self.redis_client:
            try:
                key = self._get_key(user_id)
                items = self.redis_client.lrange(key, -limit, -1)
                if items:
                    return [json.loads(it) for it in items]
            except Exception as e:
                logger.warning(f"Failed to read messages from Redis for user {user_id}: {e}")

        # In-memory fallback
        return self.buffers[user_id][-limit:]

    def get_unconsolidated_messages(self,
                                    user_id: int) -> List[Dict[str, Any]]:
        return self.get_recent_messages(user_id, limit=self.max_capacity)

    def clear_buffer(self, user_id: int) -> None:
        self.buffers[user_id].clear()
        if self.redis_client:
            try:
                key = self._get_key(user_id)
                self.redis_client.delete(key)
            except Exception as e:
                logger.warning(f"Failed to clear Redis buffer for user {user_id}: {e}")
