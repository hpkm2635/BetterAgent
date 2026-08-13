import sys

import pytest

from services.cognitive.cognitive_engine import CognitiveEngine
from services.cognitive.providers.base import BaseLLMProvider
from shared.schema.payloads import ReasoningRequestPayload, InboundMessagePayload


class ScriptedProvider(BaseLLMProvider):
    """
    Test double for generate_stream(): yields a pre-scripted sequence of
    tagged-union events per round instead of calling a real LLM API, so the
    round-trip orchestration in CognitiveEngine.stream_reasoning_loop (tool
    call -> execute -> feed result back -> generate again) can be verified
    without network access. Matches this repo's existing "exercise real
    components, don't mock" test style (see tests/test_cognitive_tools.py) --
    only the LLM boundary itself is a double, everything downstream of it
    (ToolRegistry, PresenterSessionManager, the real vscode MCP server) is real.
    """

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


def _make_payload(chat_id: int) -> ReasoningRequestPayload:
    inbound = InboundMessagePayload(
        event_id="evt1", source_component="test", chat_id=chat_id, user_id=1,
        raw_text="讲讲这个项目喵", message_id=1, timestamp=0.0,
    )
    return ReasoningRequestPayload(
        event_id="evt1", source_component="test", chat_id=chat_id, user_id=1,
        short_term_history=[], user_profile={}, rag_facts=[],
        current_emotion="HAPPY", inbound_message=inbound, trigger_type="user_message",
    )


@pytest.mark.asyncio
async def test_fire_and_forget_tool_maps_to_action_without_round_trip():
    engine = CognitiveEngine()
    engine.default_provider = ScriptedProvider([
        [
            {"type": "text", "delta": "喵呜~主人听好啦"},
            {"type": "tool_calls", "calls": [{"name": "generate_tts_speech", "args": {"text": "喵呜~主人听好啦"}}]},
        ],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload(101))]

    assert engine.default_provider.calls == 1  # no round trip needed
    action_types = [a.action_type for a in actions]
    assert "send_voice" in action_types
    voice_action = next(a for a in actions if a.action_type == "send_voice")
    assert voice_action.voice_path


@pytest.mark.asyncio
async def test_presenter_activation_and_mcp_tool_are_grounded_via_round_trip(tmp_path):
    (tmp_path / "hello.py").write_text("def add(a, b):\n    return a + b\n")

    engine = CognitiveEngine()
    engine.presenter_manager._server_commands["vscode"] = [sys.executable, "-m", "services.mcp_vscode.server"]
    engine.default_provider = ScriptedProvider([
        # Round 1: activate presenter mode -- no ActionDecisionPayload, must trigger round 2.
        [{"type": "tool_calls", "calls": [
            {"name": "presenter_mode", "args": {"action": "activate", "target": "vscode", "root_path": str(tmp_path)}},
        ]}],
        # Round 2: call a now-visible MCP tool -- result must be grounded, not guessed.
        [{"type": "tool_calls", "calls": [
            {"name": "vscode_find_files", "args": {"pattern": "*.py"}},
        ]}],
        # Round 3: narrate using the real result.
        [{"type": "text", "delta": "找到 hello.py 啦喵~"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload(102))]

    assert engine.default_provider.calls == 3
    # presenter_mode's own tool call never becomes a structured action.
    assert all(a.action_type != "presenter_mode" for a in actions)
    texts = [a.text_content for a in actions if a.text_content]
    assert any("找到" in t for t in texts)

    # The critical regression check: round 2's schema list must already
    # contain the vscode tools activated in round 1, in the SAME turn.
    schema_names_by_round = engine.default_provider.seen_tools_schema_names
    assert "vscode_find_files" not in schema_names_by_round[0]
    assert "vscode_find_files" in schema_names_by_round[1]

    await engine.presenter_manager.deactivate(102)


@pytest.mark.asyncio
async def test_unknown_tool_call_does_not_crash_the_loop():
    engine = CognitiveEngine()
    engine.default_provider = ScriptedProvider([
        [{"type": "tool_calls", "calls": [{"name": "totally_made_up_tool", "args": {}}]}],
        [{"type": "text", "delta": "抱歉主人喵"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload(103))]

    assert engine.default_provider.calls == 2
    assert any(a.text_content and "抱歉" in a.text_content for a in actions)


@pytest.mark.asyncio
async def test_tool_round_trip_is_bounded_by_max_tool_rounds():
    engine = CognitiveEngine()
    # Every round calls a tool that needs a round trip -- must not loop
    # forever. Even the forced wrap-up round (tools_schema=[]) gets a
    # tool_calls response here; that's ignored (see the wrap-up handling),
    # so this also covers the wrap-up round degrading to an empty final
    # marker rather than hanging when the model won't cooperate.
    rounds = [[{"type": "tool_calls", "calls": [{"name": "totally_made_up_tool", "args": {}}]}]] * (engine.MAX_TOOL_ROUNDS + 2)
    engine.default_provider = ScriptedProvider(rounds)

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload(104))]

    # MAX_TOOL_ROUNDS regular rounds + one forced text-only wrap-up round.
    assert engine.default_provider.calls == engine.MAX_TOOL_ROUNDS + 1
    # Loop still terminates and flushes a final marker instead of hanging.
    assert actions
    assert actions[-1].is_final is True


@pytest.mark.asyncio
async def test_tool_only_rounds_emit_a_watchdog_heartbeat():
    # Regression test: a round that produces only tool_calls (no text) used to
    # publish nothing at all to NATS/Go core while it executed those tools and
    # started another LLM round trip. With a multi-round tool chain (activate
    # a presenter session, call an MCP tool, ...) that silence could run long
    # enough for Go's per-chat watchdog to force the state machine back to
    # IDLE mid-turn (see engine/state_machine.go's deadman switch) even though
    # this turn was still legitimately in progress. A non-final, empty-text
    # heartbeat action rides the existing "extend watchdog on any non-final
    # chunk" logic on both WebGateway and the Telegram adapter without ever
    # being shown to the user (both only forward/buffer non-empty text).
    engine = CognitiveEngine()
    engine.default_provider = ScriptedProvider([
        [{"type": "tool_calls", "calls": [{"name": "totally_made_up_tool", "args": {}}]}],
        [{"type": "text", "delta": "抱歉主人喵"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload(105))]

    heartbeats = [a for a in actions if a.text_content == "" and not a.is_final]
    assert len(heartbeats) == 1
    assert heartbeats[0].action_type == "send_message"


@pytest.mark.asyncio
async def test_max_tool_rounds_forces_a_text_only_wrapup_round():
    # Regression test for the "silent turn" gap: previously, exhausting
    # MAX_TOOL_ROUNDS while every round was tool_calls-only meant
    # segmenter.flush() had nothing to say and the user got zero reply. Now
    # one extra round runs with tools_schema=[] so the model must respond in
    # plain text instead of the turn just ending.
    engine = CognitiveEngine()
    tool_only_rounds = [
        [{"type": "tool_calls", "calls": [{"name": "totally_made_up_tool", "args": {}}]}]
        for _ in range(engine.MAX_TOOL_ROUNDS)
    ]
    engine.default_provider = ScriptedProvider([
        *tool_only_rounds,
        [{"type": "text", "delta": "抱歉主人，人家查了半天还是没搞定喵~"}],
    ])

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload(106))]

    assert engine.default_provider.calls == engine.MAX_TOOL_ROUNDS + 1
    # The wrap-up round must have been called with no tools declared.
    assert engine.default_provider.seen_tools_schema_names[-1] == []
    assert any(a.text_content and "抱歉" in a.text_content for a in actions)
