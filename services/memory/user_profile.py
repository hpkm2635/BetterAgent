import json
import logging
import os
import time
from typing import Dict, Any, Optional, List, Tuple
from shared.config_loader import get_config_val

logger = logging.getLogger("user_profile")


class UserProfileManager:

    CACHE_TTL_SECONDS = 30.0

    def __init__(self):
        # Memory cache storing tuple: (timestamp, profile_dict)
        self.profile_cache: Dict[int, Tuple[float, Dict[str, Any]]] = {}
        self.redis_client = None
        self._redis_disabled = False

        redis_url = get_config_val("infrastructure.redis_url", os.getenv("REDIS_URL", "redis://127.0.0.1:6379"))
        redis_password = os.getenv("REDIS_PASSWORD")

        try:
            import redis.asyncio as aioredis
            self.redis_client = aioredis.from_url(
                redis_url,
                password=redis_password,
                decode_responses=True,
                socket_connect_timeout=0.2,
                protocol=2,
            )
            logger.info(f"UserProfileManager configured Async Redis at {redis_url}")
        except Exception as e:
            logger.warning(f"Async Redis unavailable for UserProfileManager ({e}), falling back to in-memory profile cache")
            self.redis_client = None
            self._redis_disabled = True

    def _get_key(self, user_id: int) -> str:
        return f"betteragent:profile:{int(user_id)}"

    def _handle_redis_error(self, e: Exception) -> None:
        if not self._redis_disabled:
            logger.warning(f"Redis error in UserProfileManager ({e}), disabling Redis for current process.")
            self._redis_disabled = True
            self.redis_client = None

    async def get_profile(self, user_id: int) -> Dict[str, Any]:
        user_id_int = int(user_id)
        now = time.time()

        # Check memory cache with TTL validation
        if user_id_int in self.profile_cache:
            cached_time, cached_profile = self.profile_cache[user_id_int]
            if now - cached_time < self.CACHE_TTL_SECONDS:
                return cached_profile

        default_name = get_config_val("persona.default_user_name", "主人")
        default_profile = {
            "preferred_name": default_name,
            "likes": [],
            "dislikes": [],
        }

        if self.redis_client and not self._redis_disabled:
            try:
                key = self._get_key(user_id_int)
                raw_hash = await self.redis_client.hgetall(key)
                if raw_hash:
                    profile = dict(default_profile)
                    for k, v in raw_hash.items():
                        if k in ("likes", "dislikes"):
                            try:
                                profile[k] = json.loads(v)
                            except Exception:
                                profile[k] = [item.strip() for item in v.split(",") if item.strip()]
                        else:
                            profile[k] = v
                    self.profile_cache[user_id_int] = (now, profile)
                    return profile
            except Exception as e:
                self._handle_redis_error(e)

        self.profile_cache[user_id_int] = (now, default_profile)
        return default_profile

    async def update_fact(self, user_id: int, key: str, value: Any) -> None:
        user_id_int = int(user_id)
        profile = await self.get_profile(user_id_int)
        profile[key] = value
        self.profile_cache[user_id_int] = (time.time(), profile)

        if self.redis_client and not self._redis_disabled:
            try:
                redis_key = self._get_key(user_id_int)
                str_val = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
                await self.redis_client.hset(redis_key, key, str_val)
            except Exception as e:
                self._handle_redis_error(e)

    async def get_formatted_profile_prompt(self, user_id: int) -> str:
        profile = await self.get_profile(user_id)
        pref_name = profile.get("preferred_name", "主人")
        likes = profile.get("likes", [])
        dislikes = profile.get("dislikes", [])

        likes_str = ", ".join(likes) if likes else "无"
        dislikes_str = ", ".join(dislikes) if dislikes else "无"

        return f"[用户画像] 称呼: {pref_name}, 喜好: {likes_str}, 讨厌: {dislikes_str}"
