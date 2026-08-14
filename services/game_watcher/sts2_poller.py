"""
Polls the STS2MCP mod's local REST API (https://github.com/Gennadiyev/STS2MCP,
localhost:15526, no auth -- see docs/raw-simplified.md in that repo) for
Slay the Spire 2 game state, diffs it against the previous poll, and reports
detected events to Go Core's game-event ingestion endpoint
(core/internal/webgateway/game_event_handler.go), which feeds
UrgeEngine.RecordGameEvent.

STS2MCP is a pull-only API (agents query it to play the game) -- it never
pushes events, so this poller exists purely to turn "state changed" into
"event happened" for our purposes. It does not control the game in any way.

Detected event types (see config.yaml's game_events.games.slay_the_spire_2
for the weight each maps to in UrgeEngine):
  - rare_relic_pickup: a newly-seen relic with rarity in RARE_RELIC_RARITIES.
  - near_death: HP/max_hp drops to or below near_death_hp_ratio (edge-triggered
    once per dip -- re-arms once HP recovers comfortably above the threshold).
  - victory / death: state_type transitions to "game_over". The mod's
    game_over payload carries no explicit win/loss flag (see
    McpMod.StateBuilder.cs), so this uses HP as a heuristic: HP > 0 at the
    moment of game_over implies the run was won (final boss defeated), HP <= 0
    implies death. McpMod.StateBuilder.cs sets result["player"] unconditionally
    at the end of BuildState regardless of state_type, so the game_over
    response itself already carries the HP at that exact moment -- this reads
    that value directly rather than the previous poll's (up to
    poll_interval_seconds stale) HP, which would otherwise misclassify a
    one-shot kill landing between polls as a victory.

Note: the user's original ask also mentioned "残血神抽" (a clutch card draw
while at critically low HP) as a trigger. Distinguishing a "great" draw from
an ordinary one is a subjective judgment this poller cannot make from state
alone, so only the near-death HP dip itself is detected here (event_type
"near_death", not "near_death_draw").

Autonomous play: this poller ALSO detects when the game has reached an
actionable decision point (see ACTIONABLE_STATE_TYPES) and, if a
target_chat_id is configured, POSTs to Go Core's POST /api/game-turn
(core/internal/webgateway/game_turn_handler.go) to trigger a reasoning turn
with tool access to the STS2 action API
(services/cognitive/tools/sts2_action_tool.py). This poller stays a pure
HTTP poller/notifier -- it never touches NATS directly, deliberately, so Go
remains the sole originator of reasoning turns (see this session's plan doc
for why). Go's handler itself checks whether autonomous play is active
(default off, toggled by /game_start //game_stop) and whether a turn is
already in flight, so this poller can fire eagerly without needing to track
either of those itself.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from shared.logger import setup_logger
from shared.config_loader import get_config_val

logger = setup_logger("sts2_poller")

GAME_ID = "slay_the_spire_2"

# Combat states require checking whose turn it is before treating them as
# actionable. Verified directly in McpMod.StateBuilder.cs: BuildBattleState's
# result is attached as `result["battle"] = ...` -- a TOP-LEVEL sibling key
# of "run"/"player", NOT nested under "run". `battle["turn"]` is "player" or
# "enemy", `battle["is_play_phase"]` is bool. AGENTS.md itself warns you
# sometimes need an extra poll to see whose turn it became.
COMBAT_STATE_TYPES = {"monster", "elite", "boss"}

# Decision-point states this poller triggers a game turn for. Excludes
# menu/unknown/overlay/game_over (handled separately above) and the
# deferred bundle_select/crystal_sphere (smaller initial tool surface, see
# this session's plan doc's Safety section).
ACTIONABLE_STATE_TYPES = COMBAT_STATE_TYPES | {
    "hand_select", "rewards", "card_reward", "map", "event", "rest_site",
    "shop", "fake_merchant", "treasure", "card_select", "relic_select",
}


@dataclass
class RunTracker:
    """Per-run state carried between polls so events fire exactly once at
    the moment they happen, not on every poll while the condition holds.

    `reachable` and `last_logged_state_type` are deliberately NOT touched by
    reset() -- they track polling/logging continuity across runs, not game
    state within one run, and exist so poll_once can log state *transitions*
    (unreachable<->reachable, state_type changes) at INFO/WARNING instead of
    every single poll being silent-unless-something-fires. Without this, a
    quiet log file is ambiguous between "can't reach the game" and "reachable
    but nothing detected yet" -- exactly the failure mode that made this hard
    to debug the first time around.
    """
    in_run: bool = False
    known_relic_ids: set = field(default_factory=set)
    last_hp: Optional[float] = None
    last_floor: Optional[int] = None
    near_death_armed: bool = True
    reachable: Optional[bool] = None
    last_logged_state_type: Optional[str] = None
    # Debounces game-turn triggering: only fires POST /api/game-turn when
    # this changes, so sitting on an unchanged actionable screen (e.g. the
    # map) doesn't re-fire every poll_interval_seconds.
    last_turn_signature: Optional[tuple] = None
    last_turn_time: float = 0.0

    def reset(self):
        self.in_run = False
        self.known_relic_ids = set()
        self.last_hp = None
        self.last_floor = None
        self.near_death_armed = True
        self.last_turn_signature = None
        self.last_turn_time = 0.0


async def post_game_event(
    session: aiohttp.ClientSession,
    game_event_url: str,
    game_event_token: str,
    event_type: str,
    detail: str,
    metadata: Optional[dict] = None,
) -> None:
    if not game_event_token:
        logger.debug(f"GAME_EVENT_TOKEN not configured, skipping report of {event_type}")
        return
    payload = {
        "game": GAME_ID,
        "event_type": event_type,
        "detail": detail,
        "metadata": metadata or {},
    }
    try:
        async with session.post(
            game_event_url,
            json=payload,
            headers={"X-Game-Event-Token": game_event_token},
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            if resp.status == 200:
                logger.info(f"🎮 Reported game event: {event_type} -- {detail}")
            else:
                body = await resp.text()
                logger.warning(f"game-event POST for {event_type} returned {resp.status}: {body}")
    except Exception as e:
        logger.warning(f"Failed to POST game event {event_type}: {e}")


async def post_game_turn(
    session: aiohttp.ClientSession,
    game_turn_url: str,
    game_event_token: str,
    chat_id: int,
    reason: str,
) -> None:
    if not game_event_token:
        logger.debug(f"GAME_EVENT_TOKEN not configured, skipping game turn trigger ({reason})")
        return
    payload = {"chat_id": chat_id, "reason": reason}
    try:
        async with session.post(
            game_turn_url,
            json=payload,
            headers={"X-Game-Event-Token": game_event_token},
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            body = await resp.json()
            status = body.get("status")
            if status == "ok":
                logger.info(f"🎮 Game turn triggered ({reason})")
            elif status in ("inactive", "busy"):
                # Expected/quiet: autonomous play isn't on, or a turn is
                # already in flight for this chat -- Go's handler already
                # decided this, nothing more for the poller to do.
                logger.debug(f"Game turn trigger skipped ({reason}): {status}")
            else:
                logger.warning(f"game-turn POST ({reason}) returned unexpected body: {body}")
    except Exception as e:
        logger.warning(f"Failed to POST game turn ({reason}): {e}")


async def poll_once(
    session: aiohttp.ClientSession,
    sts2_api_url: str,
    game_event_url: str,
    game_turn_url: str,
    game_event_token: str,
    near_death_hp_ratio: float,
    rare_relic_rarities: set,
    target_chat_id: int,
    tracker: RunTracker,
) -> None:
    try:
        async with session.get(
            sts2_api_url, params={"format": "json"}, timeout=aiohttp.ClientTimeout(total=3)
        ) as resp:
            if resp.status != 200:
                if tracker.reachable is not False:
                    logger.warning(f"STS2 API at {sts2_api_url} returned HTTP {resp.status}")
                    tracker.reachable = False
                return
            data = await resp.json()
    except Exception as e:
        # Game not running / mod not loaded -- expected and harmless most of
        # the time this poller runs, but log the transition ONCE (not every
        # poll_interval_seconds) so a genuinely stuck connection is visible
        # instead of indistinguishable from "nothing to detect yet".
        if tracker.reachable is not False:
            logger.warning(f"STS2 API at {sts2_api_url} unreachable ({e}) -- is the game running with the mod loaded?")
            tracker.reachable = False
        if tracker.in_run:
            tracker.reset()
        return

    if tracker.reachable is not True:
        logger.info(f"STS2 API reachable at {sts2_api_url}")
        tracker.reachable = True

    state_type = data.get("state_type")
    if state_type != tracker.last_logged_state_type:
        logger.info(f"STS2 state_type -> {state_type}")
        tracker.last_logged_state_type = state_type

    if state_type == "game_over":
        if tracker.in_run:
            # McpMod.StateBuilder.cs sets result["run"]/result["player"]
            # unconditionally at the end of BuildState, regardless of which
            # state_type branch fired above -- so this same game_over
            # response already carries the HP at the exact moment the run
            # ended. Prefer that over tracker.last_hp (the previous poll,
            # up to poll_interval_seconds stale): a one-shot kill between
            # polls would otherwise still show the pre-death HP and get
            # misclassified as a victory. Only fall back to the stale value
            # if this response is missing player data for some reason.
            current_hp = (data.get("player") or {}).get("hp")
            final_hp = current_hp if current_hp is not None else tracker.last_hp
            outcome = "victory" if (final_hp or 0) > 0 else "death"
            await post_game_event(
                session, game_event_url, game_event_token, outcome,
                f"run ended ({outcome}) at floor {tracker.last_floor}",
                {"floor": tracker.last_floor, "final_hp": final_hp},
            )
        tracker.reset()
        return

    run = data.get("run")
    player = data.get("player")
    if run is None or player is None:
        # Main menu / unknown / no active run visible this poll. Do NOT
        # reset tracker state here -- empirically, STS2's game_over
        # transition can transiently report state_type=="menu" for exactly
        # one poll before the mod's topOverlay settles on NGameOverScreen
        # (observed: "menu" then "game_over" one poll_interval_seconds
        # apart). Resetting eagerly on "menu" clears tracker.in_run before
        # the game_over branch above ever runs, silently swallowing the
        # victory/death event every time. Only game_over itself (handled
        # above) and a detected fresh-run start (the floor-decrease check
        # below) reset state.
        return

    new_floor = run.get("floor")
    if (
        tracker.in_run
        and tracker.last_floor is not None
        and new_floor is not None
        and new_floor < tracker.last_floor
    ):
        # Floor went backwards -- only possible if a new run started (e.g.
        # the previous one was abandoned without ever reaching game_over).
        # Reset before adopting this run's data so stale relic IDs/HP from
        # the abandoned run don't suppress this run's events.
        tracker.reset()

    tracker.in_run = True
    tracker.last_floor = new_floor

    hp = player.get("hp")
    max_hp = player.get("max_hp")
    if hp is not None and max_hp:
        tracker.last_hp = hp
        ratio = hp / max_hp
        if ratio <= near_death_hp_ratio and tracker.near_death_armed:
            tracker.near_death_armed = False
            await post_game_event(
                session, game_event_url, game_event_token, "near_death",
                f"HP dropped to {hp}/{max_hp} on floor {tracker.last_floor}",
                {"hp": hp, "max_hp": max_hp, "floor": tracker.last_floor},
            )
        elif ratio > near_death_hp_ratio * 1.5:
            # Recovered comfortably above the danger zone (e.g. rest site
            # heal) -- re-arm so the next dip can fire again.
            tracker.near_death_armed = True

    relics = player.get("relics") or []
    current_ids = set()
    for relic in relics:
        rid = relic.get("id") or relic.get("name")
        if rid is None:
            continue
        current_ids.add(rid)
        if rid not in tracker.known_relic_ids:
            rarity = str(relic.get("rarity", "")).lower()
            if rarity in rare_relic_rarities:
                await post_game_event(
                    session, game_event_url, game_event_token, "rare_relic_pickup",
                    f"obtained {relic.get('name', rid)} ({rarity})",
                    {"relic_id": rid, "rarity": rarity, "floor": tracker.last_floor},
                )
    tracker.known_relic_ids = current_ids

    if target_chat_id is not None and state_type in ACTIONABLE_STATE_TYPES:
        actionable = True
        reason = state_type
        if state_type in COMBAT_STATE_TYPES:
            battle = data.get("battle") or {}
            actionable = battle.get("turn") == "player" and bool(battle.get("is_play_phase"))
            reason = f"{state_type}_player_turn"

        if actionable:
            energy = player.get("energy")
            hand = player.get("hand")
            hand_len = len(hand) if isinstance(hand, list) else None
            gold = player.get("gold")
            signature = (state_type, new_floor, hp, energy, hand_len, gold)
            now = time.time()
            if signature != tracker.last_turn_signature or (now - tracker.last_turn_time > 8.0):
                tracker.last_turn_signature = signature
                tracker.last_turn_time = now
                await post_game_turn(session, game_turn_url, game_event_token, target_chat_id, reason)


async def main():
    sts2_api_url = get_config_val(
        "game_watcher.sts2.api_url", "http://127.0.0.1:15526/api/v1/singleplayer"
    )
    poll_interval_seconds = float(get_config_val("game_watcher.sts2.poll_interval_seconds", 5))
    near_death_hp_ratio = float(get_config_val("game_watcher.sts2.near_death_hp_ratio", 0.15))
    rare_relic_rarities = {
        str(r).lower() for r in get_config_val("game_watcher.sts2.rare_relic_rarities", ["rare", "boss"])
    }

    game_event_bind_addr = get_config_val("core_engine.game_event_bind_addr", "127.0.0.1:8090")
    game_event_url = f"http://{game_event_bind_addr}/api/game-event"
    game_turn_url = f"http://{game_event_bind_addr}/api/game-turn"

    # Go's /api/game-turn handler now resolves the real target chat itself
    # from AutonomousPlayState.TargetChatID() (whichever chat sent
    # /game_start) and ignores whatever chat_id this poller sends whenever
    # that's set -- so this config value no longer picks "which chat," it's
    # purely a local on/off switch: unset/0 skips the POST call entirely
    # (avoids a wasted request + Go round trip on every actionable poll for
    # users who've never set up autonomous play at all); any nonzero value
    # just needs to be a syntactically valid chat_id placeholder, since Go
    # overrides it once /game_start has actually been used.
    raw_target_chat_id = get_config_val("game_watcher.sts2.target_chat_id", 0)
    target_chat_id = int(raw_target_chat_id) if raw_target_chat_id else None

    import os
    from dotenv import load_dotenv

    load_dotenv()
    game_event_token = os.getenv("GAME_EVENT_TOKEN", "")
    if not game_event_token:
        logger.warning(
            "GAME_EVENT_TOKEN is not set -- sts2_poller will keep polling but every "
            "reported event/turn will be silently dropped by Go Core (see .env.example)."
        )
    if target_chat_id is None:
        logger.info(
            "game_watcher.sts2.target_chat_id is not set -- event reporting (relic/near-death/"
            "victory/death) still works, but autonomous game-turn triggering is disabled."
        )

    logger.info(
        f"STS2 Game Watcher started (api={sts2_api_url}, "
        f"poll_interval={poll_interval_seconds}s, near_death_hp_ratio={near_death_hp_ratio}, "
        f"autonomous_play_target_chat_id={target_chat_id or 'disabled'})"
    )

    tracker = RunTracker()
    async with aiohttp.ClientSession() as session:
        while True:
            await poll_once(
                session, sts2_api_url, game_event_url, game_turn_url, game_event_token,
                near_death_hp_ratio, rare_relic_rarities, target_chat_id, tracker,
            )
            await asyncio.sleep(poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
