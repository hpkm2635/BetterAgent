"""Regression coverage for stream_reasoning_loop's citation accumulation
(see cognitive_engine.py's collected_citations, added alongside the existing
segmenter = SentenceSegmenter() cross-round state).

Unlike test_cognitive_engine.py's test_cognitive_engine_selfie_request (a
real end-to-end call against a live LLM provider, part of the sandbox's known
pre-existing failures with no network), these tests mock out
default_provider.generate_stream and tool_registry.get_tool so they run
fully offline.
"""

import pytest

from services.cognitive.cognitive_engine import CognitiveEngine
from shared.schema.payloads import ReasoningRequestPayload, InboundMessagePayload


class _FakeCampusKBTool:
    def __init__(self, facts):
        self._facts = facts

    async def execute(self, **kwargs):
        return {"status": "success", "facts": self._facts}


def _make_payload():
    inbound = InboundMessagePayload(
        event_id="evt_test_citation",
        source_component="test",
        chat_id=1001,
        user_id=1001,
        raw_text="图书馆几点关门喵",
        message_id=1,
        timestamp=1785800000.0,
    )
    return ReasoningRequestPayload(
        event_id="evt_test_citation",
        source_component="test",
        chat_id=1001,
        user_id=1001,
        short_term_history=[],
        user_profile={"preferred_name": "主人"},
        rag_facts=[],
        current_emotion="[猫娘内心状态] 当前心情: HAPPY (愉悦度: 0.9)",
        inbound_message=inbound,
        trigger_type="user_message",
    )


def _make_engine_with_fake_tool_call(facts, reply_text="图书馆周一至周五开放至22点喵~"):
    engine = CognitiveEngine()
    call_count = {"n": 0}

    async def fake_generate_stream(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield {"type": "tool_calls", "calls": [{"name": "search_campus_kb", "args": {"query": "图书馆"}}]}
        else:
            yield {"type": "text", "delta": reply_text}

    engine.default_provider.generate_stream = fake_generate_stream
    engine.tool_registry.get_tool = lambda name: _FakeCampusKBTool(facts)
    return engine


@pytest.mark.asyncio
async def test_final_action_carries_citations_from_campus_kb_call():
    facts = [{"content": "图书馆周一至周五开放至22:00", "source": "faq.md", "relevance_score": 0.9}]
    engine = _make_engine_with_fake_tool_call(facts)

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload())]

    final_actions = [a for a in actions if a.is_final]
    assert len(final_actions) == 1, f"expected exactly one is_final action, got {len(final_actions)}"
    assert final_actions[0].citations == facts

    non_final_with_citations = [a for a in actions if not a.is_final and a.citations]
    assert not non_final_with_citations, "citations must only appear on the turn's final action"


@pytest.mark.asyncio
async def test_duplicate_facts_across_calls_are_deduplicated_by_content():
    facts = [
        {"content": "图书馆周一至周五开放至22:00", "source": "faq.md", "relevance_score": 0.9},
        {"content": "图书馆周一至周五开放至22:00", "source": "faq.md", "relevance_score": 0.9},
        {"content": "食堂营业时间07:00-21:00", "source": "canteen.md", "relevance_score": 0.8},
    ]
    engine = _make_engine_with_fake_tool_call(facts)

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload())]

    final_actions = [a for a in actions if a.is_final]
    assert len(final_actions) == 1
    contents = [c["content"] for c in final_actions[0].citations]
    assert contents.count("图书馆周一至周五开放至22:00") == 1
    assert "食堂营业时间07:00-21:00" in contents
    assert len(final_actions[0].citations) == 2


@pytest.mark.asyncio
async def test_no_citations_when_campus_kb_never_called():
    engine = CognitiveEngine()

    async def fake_generate_stream(**kwargs):
        yield {"type": "text", "delta": "主人好呀喵~"}

    engine.default_provider.generate_stream = fake_generate_stream

    actions = [a async for a in engine.stream_reasoning_loop(_make_payload())]

    final_actions = [a for a in actions if a.is_final]
    assert len(final_actions) == 1
    assert final_actions[0].citations is None
