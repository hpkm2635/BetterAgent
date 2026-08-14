import pytest

from services.cognitive.cognitive_engine import CognitiveEngine
from services.cognitive.providers.base import BaseLLMProvider
from shared.schema.payloads import ReasoningRequestPayload


class ScriptedProvider(BaseLLMProvider):
    """Same test double as tests/test_stream_reasoning_loop.py -- see that
    file's docstring for the rationale ("exercise real components, don't
    mock", only the LLM boundary itself is a double)."""

    def __init__(self, rounds):
        self._rounds = rounds
        self.calls = 0
        self.seen_tools_schema_names = []

    async def generate(self, messages, tools_schema=None, system_prompt=None):
        return {"text": "", "tool_calls": [], "finish_reason": "STOP"}

    async def generate_stream(self, messages, tools_schema=None, system_prompt=None, cancel_event=None):
        self.seen_tools_schema_names.append([t["name"] for t in (tools_schema or [])])
        events = self._rounds[self.calls]
        self.calls += 1
        for event in events:
            yield event


def _game_turn_payload(chat_id: int) -> ReasoningRequestPayload:
    # No inbound_message -- a game turn never has one, same as a proactive
    # turn (see prompt_builder.py's build_messages elif chain).
    return ReasoningRequestPayload(
        event_id="evt1", source_component="test", chat_id=chat_id, user_id=1,
        short_term_history=[], user_profile={}, rag_facts=[],
        current_emotion="HAPPY", inbound_message=None, trigger_type="game_turn",
        source_channel="web",
    )


def _chat_payload(chat_id: int) -> ReasoningRequestPayload:
    return ReasoningRequestPayload(
        event_id="evt1", source_component="test", chat_id=chat_id, user_id=1,
        short_term_history=[], user_profile={}, rag_facts=[],
        current_emotion="HAPPY", inbound_message=None, trigger_type="user_message",
        source_channel="web",
    )


@pytest.mark.asyncio
async def test_game_turn_exposes_sts2_tools():
    engine = CognitiveEngine()
    engine.default_provider = ScriptedProvider([
        [{"type": "text", "delta": "看了一眼局面喵"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_game_turn_payload(201))]

    assert actions  # sanity: the turn actually produced something
    schema_names = engine.default_provider.seen_tools_schema_names[0]
    assert "sts2_get_game_state" in schema_names
    assert "sts2_play_card" in schema_names
    assert "sts2_end_turn" in schema_names


@pytest.mark.asyncio
async def test_ordinary_chat_turn_never_sees_sts2_tools():
    engine = CognitiveEngine()
    engine.default_provider = ScriptedProvider([
        [{"type": "text", "delta": "喵~"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_chat_payload(202))]

    assert actions
    schema_names = engine.default_provider.seen_tools_schema_names[0]
    assert not any(name.startswith("sts2_") for name in schema_names)


@pytest.mark.asyncio
async def test_game_turn_excludes_irreversible_menu_select():
    # Safety boundary: menu_select is never registered at all for this MVP
    # (see sts2_action_tool.py's STS2_ACTION_SPECS) -- the one genuinely
    # irreversible action in the whole STS2 action table.
    engine = CognitiveEngine()
    engine.default_provider = ScriptedProvider([
        [{"type": "text", "delta": "喵~"}],
    ])

    await (engine.stream_reasoning_loop(_game_turn_payload(203)).__anext__())
    schema_names = engine.default_provider.seen_tools_schema_names[0]
    assert "sts2_menu_select" not in schema_names
    assert not any("bundle" in name or "crystal_sphere" in name for name in schema_names)


@pytest.mark.asyncio
async def test_game_turn_uses_higher_round_budget_than_chat():
    # A game turn must get MAX_GAME_TOOL_ROUNDS (20 by default), not the
    # chat-turn MAX_TOOL_ROUNDS (4) -- combat can legitimately need many
    # sts2_play_card round trips in a row.
    engine = CognitiveEngine()
    assert engine.MAX_GAME_TOOL_ROUNDS > engine.MAX_TOOL_ROUNDS

    rounds = [[{"type": "tool_calls", "calls": [{"name": "totally_made_up_tool", "args": {}}]}]] * (engine.MAX_GAME_TOOL_ROUNDS + 2)
    engine.default_provider = ScriptedProvider(rounds)

    actions = [a async for a in engine.stream_reasoning_loop(_game_turn_payload(204))]

    # MAX_GAME_TOOL_ROUNDS regular rounds + one forced text-only wrap-up round.
    assert engine.default_provider.calls == engine.MAX_GAME_TOOL_ROUNDS + 1
    assert actions
    assert actions[-1].is_final is True


def test_reorder_index_shifting_calls_sorts_descending_within_each_tool():
    calls = [
        {"name": "sts2_play_card", "args": {"card_index": 1}},
        {"name": "sts2_use_potion", "args": {"slot": 0}},
        {"name": "sts2_play_card", "args": {"card_index": 4}},
        {"name": "sts2_play_card", "args": {"card_index": 0}},
        {"name": "sts2_end_turn", "args": {}},
    ]

    reordered = CognitiveEngine._reorder_index_shifting_calls(calls)

    # sts2_use_potion/sts2_end_turn keep their original slots untouched...
    assert reordered[1]["name"] == "sts2_use_potion"
    assert reordered[4]["name"] == "sts2_end_turn"
    # ...while the three sts2_play_card slots (0, 2, 3) get refilled with
    # the play_card calls sorted highest-card_index-first, regardless of
    # the order the model originally listed them in.
    play_card_indices = [
        c["args"]["card_index"] for c in reordered if c["name"] == "sts2_play_card"
    ]
    assert play_card_indices == [4, 1, 0]


def test_reorder_index_shifting_calls_leaves_single_or_absent_groups_alone():
    calls = [{"name": "sts2_end_turn", "args": {}}]
    assert CognitiveEngine._reorder_index_shifting_calls(calls) == calls

    single = [{"name": "sts2_play_card", "args": {"card_index": 2}}]
    assert CognitiveEngine._reorder_index_shifting_calls(single) == single


def test_reorder_index_shifting_calls_handles_multiple_index_fields_independently():
    calls = [
        {"name": "sts2_claim_reward", "args": {"index": 0}},
        {"name": "sts2_claim_reward", "args": {"index": 2}},
        {"name": "sts2_play_card", "args": {"card_index": 1}},
        {"name": "sts2_play_card", "args": {"card_index": 3}},
    ]
    reordered = CognitiveEngine._reorder_index_shifting_calls(calls)

    claim_indices = [c["args"]["index"] for c in reordered if c["name"] == "sts2_claim_reward"]
    play_indices = [c["args"]["card_index"] for c in reordered if c["name"] == "sts2_play_card"]
    assert claim_indices == [2, 0]
    assert play_indices == [3, 1]


@pytest.mark.asyncio
async def test_game_turn_batched_play_card_calls_execute_in_descending_order():
    # End-to-end: the LLM requests three sts2_play_card calls in ONE
    # response with indices out of order -- confirms the reordering is
    # actually wired into stream_reasoning_loop's execution path, not just
    # unit-tested in isolation.
    engine = CognitiveEngine()
    execution_order = []

    real_tool = engine.tool_registry.get_tool("sts2_play_card")

    async def recording_execute(**kwargs):
        execution_order.append(kwargs.get("card_index"))
        return {"status": "ok", "state": {"state_type": "monster"}}

    real_tool.execute = recording_execute  # monkeypatch instance method, avoids real HTTP

    engine.default_provider = ScriptedProvider([
        [{"type": "tool_calls", "calls": [
            {"name": "sts2_play_card", "args": {"card_index": 1}},
            {"name": "sts2_play_card", "args": {"card_index": 3}},
            {"name": "sts2_play_card", "args": {"card_index": 0}},
        ]}],
        [{"type": "text", "delta": "喵~"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_game_turn_payload(205))]

    assert execution_order == [3, 1, 0]
    assert actions
