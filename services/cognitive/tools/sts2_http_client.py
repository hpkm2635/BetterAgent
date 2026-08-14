import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

from shared.config_loader import get_config_val

logger = logging.getLogger("sts2_http_client")

# Trimmed to the fields that actually change turn-to-turn and matter for the
# LLM's next decision (hand indices, energy, hp, which screen it's on) --
# not the full state blob (relics/potions/piles/etc.), to keep the
# per-action follow-up fetch cheap in tokens.
_TRIM_KEYS = ("state_type", "battle")


def _trim_state(state: Dict[str, Any]) -> Dict[str, Any]:
    trimmed = {k: state[k] for k in _TRIM_KEYS if k in state}
    player = state.get("player")
    if isinstance(player, dict):
        trimmed["player"] = {
            k: player[k] for k in ("hp", "max_hp", "energy", "hand") if k in player
        }
    return trimmed


class Sts2HttpClient:
    """Thin async HTTP client for the STS2MCP mod's local REST API
    (https://github.com/Gennadiyev/STS2MCP, unauthenticated, loopback-only
    by design -- see McpMod.cs's HttpListener.Prefixes). Shared by every
    sts2_* tool instance (one aiohttp session, connection reuse).

    Never raises -- every method returns a plain "status"-keyed dict, same
    contract as every other tool in this codebase (see ImageGenTool)."""

    def __init__(self, api_url: Optional[str] = None, timeout_seconds: float = 10.0):
        # Same config key services/game_watcher/sts2_poller.py already uses
        # -- one source of truth for "where is the mod's API".
        self._base_url = (api_url or get_config_val(
            "game_watcher.sts2.api_url", "http://127.0.0.1:15526/api/v1/singleplayer"
        )).rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        # Constructed lazily on first use inside a running event loop --
        # ToolRegistry.__init__ runs before any loop is guaranteed running,
        # so an aiohttp.ClientSession can't be safely built in __init__.
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def get_state(self) -> Dict[str, Any]:
        try:
            session = self._get_session()
            async with session.get(self._base_url, params={"format": "json"}) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return {"status": "error", "message": f"HTTP {resp.status}: {data}"}
                return {"status": "ok", **data}
        except asyncio.TimeoutError:
            return {"status": "error", "message": "STS2 API request timed out"}
        except Exception as e:
            return {"status": "error", "message": f"STS2 API unreachable: {e}"}

    async def post_action(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        body = {"action": action, **{k: v for k, v in args.items() if v is not None}}
        try:
            session = self._get_session()
            async with session.post(self._base_url, json=body) as resp:
                result = await resp.json()
        except asyncio.TimeoutError:
            return {"status": "error", "message": "STS2 API request timed out"}
        except Exception as e:
            return {"status": "error", "message": f"STS2 API unreachable: {e}"}

        # Short animation-safe pacing delay to allow Godot's UI node tree to complete
        # card play / end turn animations before fetching state and returning to LLM.
        action_delay = float(get_config_val("game_watcher.sts2.action_delay_seconds", 0.6))
        if action == "end_turn":
            # Godot Boss Intent and end-turn animations can take up to ~1.0s.
            action_delay = max(action_delay, 1.0)
        if action_delay > 0:
            await asyncio.sleep(action_delay)

        state = await self.get_state()

        # Robust retry guard: if end_turn was called while Godot's UI button was temporarily
        # locked by an in-flight animation, turn will still show 'player' with 0 energy.
        # Send a defensive retry once the UI node tree has settled.
        if action == "end_turn" and state.get("status") == "ok":
            # get_state() spreads the mod's fields directly into the top
            # level (return {"status": "ok", **data}), same as _trim_state
            # reads it above -- there is no "data" sub-key.
            battle = state.get("battle") or {}
            player = state.get("player") or {}
            if battle.get("turn") == "player" and battle.get("is_play_phase") and player.get("energy", 0) == 0:
                logger.warning("🔄 Godot EndTurn button was locked during initial call; retrying end_turn...")
                try:
                    async with session.post(self._base_url, json=body) as resp:
                        result = await resp.json()
                    await asyncio.sleep(0.8)
                    state = await self.get_state()
                except Exception as e:
                    logger.warning(f"Retry end_turn failed: {e}")

        if state.get("status") == "ok":
            result["state"] = _trim_state(state)
        return result

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
