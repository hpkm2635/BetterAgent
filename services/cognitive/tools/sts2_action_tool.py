import logging
import re
from typing import Any, Dict, List, Optional

from services.cognitive.tools.base_tool import BaseTool
from services.cognitive.tools.sts2_http_client import Sts2HttpClient

logger = logging.getLogger("sts2_action_tool")

# Matches the mod's documented entity_id format, e.g. "KIN_PRIEST_0".
# This is defensive input hygiene, NOT a security boundary -- the STS2 HTTP
# API is unauthenticated-by-design and loopback-only (see Sts2HttpClient's
# docstring), so the real trust boundary is "who can reach 127.0.0.1:15526,"
# not the string content of this field. A mismatch just gets logged and the
# target dropped rather than the call rejected.
_ENTITY_ID_PATTERN = re.compile(r"^[A-Z0-9_]{1,64}$")

_INDEX_SHIFT_WARNING = (
    " CRITICAL: playing/claiming shifts all higher indices left. Play/claim "
    "RIGHT TO LEFT (highest index first), or re-check the returned state "
    "before your next call."
)

_INDEX_PARAM = {"type": "integer", "description": "Zero-based index into the current list shown by sts2_get_game_state."}
_TARGET_PARAM = {
    "type": "string",
    "description": "Target entity_id for single-target effects, e.g. 'KIN_PRIEST_0'. Omit for non-targeted effects.",
}

# One entry per STS2 mod action this MVP exposes. Deliberately excludes
# menu_select (the one genuinely irreversible action in the whole table --
# could abandon an active run) and bundle_select/crystal_sphere (deferred,
# smaller initial surface -- see the plan doc's Safety section).
STS2_ACTION_SPECS: List[Dict[str, Any]] = [
    {
        "action": "play_card",
        "name": "sts2_play_card",
        "description": "Play a card from hand during combat." + _INDEX_SHIFT_WARNING,
        "properties": {"card_index": _INDEX_PARAM, "target": _TARGET_PARAM},
        "required": ["card_index"],
    },
    {
        "action": "use_potion",
        "name": "sts2_use_potion",
        "description": "Use a potion. slot is the potion-slot index, not a card index. Potions don't cost energy or count as a card play -- use buff potions before playing cards.",
        "properties": {
            "slot": {"type": "integer", "description": "Potion slot index."},
            "target": _TARGET_PARAM,
        },
        "required": ["slot"],
    },
    {
        "action": "discard_potion",
        "name": "sts2_discard_potion",
        "description": "Discard a potion to free up its slot.",
        "properties": {"slot": {"type": "integer", "description": "Potion slot index to discard."}},
        "required": ["slot"],
    },
    {
        "action": "end_turn",
        "name": "sts2_end_turn",
        "description": "End the current combat turn. After this, call sts2_get_game_state again (sometimes twice) to see enemy-turn results and your new hand.",
        "properties": {},
        "required": [],
    },
    {
        "action": "combat_select_card",
        "name": "sts2_combat_select_card",
        "description": "Select/deselect a card during an in-combat exhaust/discard/upgrade prompt (hand_select screen).",
        "properties": {"card_index": _INDEX_PARAM},
        "required": ["card_index"],
    },
    {
        "action": "combat_confirm_selection",
        "name": "sts2_combat_confirm_selection",
        "description": "Confirm the current in-combat hand card selection (hand_select screen).",
        "properties": {},
        "required": [],
    },
    {
        "action": "claim_reward",
        "name": "sts2_claim_reward",
        "description": "Claim a reward (gold/potion/relic) from the post-combat rewards screen. Claiming a card reward opens the card_reward screen." + _INDEX_SHIFT_WARNING,
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "select_card_reward",
        "name": "sts2_select_card_reward",
        "description": "Pick a card to add to the deck from the card_reward screen.",
        "properties": {"card_index": _INDEX_PARAM},
        "required": ["card_index"],
    },
    {
        "action": "skip_card_reward",
        "name": "sts2_skip_card_reward",
        "description": "Skip the card reward, if skipping is allowed. Prefer skipping mediocre cards -- deck quality matters more than quantity.",
        "properties": {},
        "required": [],
    },
    {
        "action": "proceed",
        "name": "sts2_proceed",
        "description": "Leave the current rewards/rest_site/shop/treasure screen and return to the map. NOT valid for the event screen -- use sts2_choose_event_option there instead.",
        "properties": {},
        "required": [],
    },
    {
        "action": "choose_event_option",
        "name": "sts2_choose_event_option",
        "description": "Choose an option on an event screen. After choosing, there's often a further 'Proceed' option at index 0.",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "advance_dialogue",
        "name": "sts2_advance_dialogue",
        "description": "Advance an Ancient event's dialogue. Call repeatedly until the dialogue ends.",
        "properties": {},
        "required": [],
    },
    {
        "action": "choose_rest_option",
        "name": "sts2_choose_rest_option",
        "description": "Choose a rest site option (rest, smith, etc.). Follow with sts2_proceed. Rest before a boss if below 80% HP -- boss fights are long and punishing.",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "shop_purchase",
        "name": "sts2_shop_purchase",
        "description": "Purchase an item from the shop by its flat index in the item list.",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "choose_map_node",
        "name": "sts2_choose_map_node",
        "description": "Travel to a map node. index must be one of the currently offered next_options. Elites give relics (fight when >70% HP); unknown nodes are safer at medium HP; visit shops with 100+ gold.",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "select_card",
        "name": "sts2_select_card",
        "description": "Pick/toggle a card on the card_select overlay (transform/upgrade/remove/choose-a-card screens).",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "confirm_selection",
        "name": "sts2_confirm_selection",
        "description": "Confirm the current card_select overlay selection (grid-style screens only).",
        "properties": {},
        "required": [],
    },
    {
        "action": "cancel_selection",
        "name": "sts2_cancel_selection",
        "description": "Cancel the current card_select overlay preview/selection.",
        "properties": {},
        "required": [],
    },
    {
        "action": "select_relic",
        "name": "sts2_select_relic",
        "description": "Pick a relic from a relic_select screen (immediate, no confirm step).",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
    {
        "action": "skip_relic_selection",
        "name": "sts2_skip_relic_selection",
        "description": "Skip the current relic choice.",
        "properties": {},
        "required": [],
    },
    {
        "action": "claim_treasure_relic",
        "name": "sts2_claim_treasure_relic",
        "description": "Claim a relic from an opened treasure chest.",
        "properties": {"index": _INDEX_PARAM},
        "required": ["index"],
    },
]


def _is_valid_entity_id(target: Optional[str]) -> bool:
    if target is None:
        return True
    return bool(_ENTITY_ID_PATTERN.match(target))


class Sts2ActionTool(BaseTool):
    """Generic tool for one STS2 mod action, parameterized over a spec from
    STS2_ACTION_SPECS. One instance per spec (registered via
    build_sts2_tools) rather than N hand-written subclasses -- mirrors
    STS2MCP's own MCP server design of many small, specific tools (which
    its README documents as already working well with real LLM agents),
    while keeping the implementation DRY on our side."""

    def __init__(self, spec: Dict[str, Any], http_client: Sts2HttpClient):
        self._spec = spec
        self._http = http_client

    @property
    def name(self) -> str:
        return self._spec["name"]

    @property
    def description(self) -> str:
        return self._spec["description"]

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": self._spec["properties"],
            "required": self._spec["required"],
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        target = kwargs.get("target")
        if target is not None and not _is_valid_entity_id(target):
            logger.warning(f"Dropping malformed target {target!r} for {self.name}")
            kwargs["target"] = None
        return await self._http.post_action(self._spec["action"], kwargs)


class Sts2GetStateTool(BaseTool):
    """Fetches live STS2 game state. Kept separate from Sts2ActionTool since
    its no-args/raw-GET shape genuinely differs from the parameterized POST
    action tools. The LLM should call this first in a game turn (see
    prompt_builder.py's game_turn system-prompt branch) rather than relying
    on any state pushed into the prompt, since a poller-derived snapshot
    could be several seconds stale by the time this tool actually runs."""

    def __init__(self, http_client: Sts2HttpClient):
        self._http = http_client

    @property
    def name(self) -> str:
        return "sts2_get_game_state"

    @property
    def description(self) -> str:
        return (
            "Fetches the current Slay the Spire 2 game state (state_type, run/floor, "
            "player hp/energy/hand/relics/potions, and combat details when in battle). "
            "Call this first in a game turn before deciding an action."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> Dict[str, Any]:
        return await self._http.get_state()


def build_sts2_tools(http_client: Sts2HttpClient) -> List[BaseTool]:
    return [Sts2GetStateTool(http_client)] + [
        Sts2ActionTool(spec, http_client) for spec in STS2_ACTION_SPECS
    ]
