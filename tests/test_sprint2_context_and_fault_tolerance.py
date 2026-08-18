import pytest
import asyncio
import re
from unittest.mock import AsyncMock, patch, MagicMock

from services.memory.token_budget import TokenBudgetManager
from services.memory.memory_hub import MemoryHub
from services.cognitive.cognitive_engine import SentenceSegmenter, CognitiveEngine
from shared.schema.payloads import EnrichContextReqPayload, ReasoningRequestPayload, InboundMessagePayload


@pytest.mark.asyncio
async def test_token_budget_manager_trimming_and_memory_hub_integration():
    """Verify TokenBudgetManager trims history and rag_facts when context exceeds budget."""
    budget_mgr = TokenBudgetManager(max_budget=200)

    system_prompt = "你是二次元猫娘助手"
    profile = {"preferred_name": "主人", "likes": ["摸头"], "dislikes": ["吵闹"]}
    
    # 20 oversized history messages (~100 chars each = ~25 tokens each, total ~500 tokens)
    history = [
        {"role": "user", "content": f"这长一条历史消息 {i} " + "A" * 80}
        for i in range(20)
    ]
    rag_facts = [f"RAG 事实条目 {i}: " + "B" * 50 for i in range(10)]

    _, trimmed_history, trimmed_facts, _ = budget_mgr.fit_into_budget(
        system_prompt=system_prompt,
        history=history,
        profile=profile,
        rag_facts=rag_facts,
    )

    # Assert trimmed context strictly respects budget
    total_tokens = (
        len(system_prompt) // 4 +
        len(str(profile)) // 4 +
        sum(len(f) // 4 for f in trimmed_facts) +
        sum(len(m["content"]) // 4 for m in trimmed_history)
    )
    assert total_tokens <= budget_mgr.max_budget
    assert len(trimmed_history) < len(history)

    # Test integration inside MemoryHub
    hub = MemoryHub()
    hub.token_budget = TokenBudgetManager(max_budget=150)
    
    # Add messages to short_term_buffer
    user_id = 112233
    for i in range(15):
        await hub.short_term_buffer.add_message(user_id=user_id, role="user", content=f"超长记忆历史 {i} " + "X" * 60)

    req_payload = EnrichContextReqPayload(
        event_id="evt_tb_1",
        source_component="csm",
        chat_id=user_id,
        user_id=user_id,
        current_state="IDLE",
        trigger_type="chat_message",
        inbound_message=InboundMessagePayload(
            event_id="evt_tb_in",
            source_component="gotd",
            chat_id=user_id,
            user_id=user_id,
            message_id=101,
            raw_text="最新用户提问",
        )
    )

    reasoning_req = await hub.handle_enrich_context_req(req_payload)
    assert isinstance(reasoning_req, ReasoningRequestPayload)
    assert len(reasoning_req.short_term_history) < 15  # Successfully trimmed by token budget
    await hub.vector_store.close()


def test_sentence_segmenter_curly_brace_regex_narrowing():
    """Verify SentenceSegmenter plain text containing '{' does NOT trigger JSON barrier desync."""
    segmenter = SentenceSegmenter()

    # Plain text containing brace (e.g. math set or sentence notation) should be emitted as normal sentence
    deltas = ["主人的集合表示为 {x | x > 0}。", " 这是一个普通的句子。"]
    results = []
    for delta in deltas:
        sentences = segmenter.push(delta)
        results.extend(sentences)

    sentences_flush = segmenter.flush()
    results.extend(sentences_flush)

    full_output = " ".join(results)
    assert "集合表示为" in full_output

    # Structured JSON tool payload should be stripped
    segmenter_json = SentenceSegmenter()
    json_delta = '好的喵！{"action": "send_photo", "photo_path": "/tmp/cat.jpg"} 这是后续文本。'
    out_json = segmenter_json.push(json_delta) + segmenter_json.flush()
    assert '{"action":' not in " ".join(out_json)


@pytest.mark.asyncio
async def test_stream_reasoning_loop_exception_yields_final_payload():
    """Verify stream_reasoning_loop yields is_final=True payload on exception to prevent 45s CSM watchdog deadlock."""
    engine = CognitiveEngine()

    req = ReasoningRequestPayload(
        event_id="evt_err_1",
        source_component="csm",
        chat_id=8888,
        user_id=8888,
        inbound_message=InboundMessagePayload(
            event_id="evt_err_inbound",
            source_component="gotd",
            chat_id=8888,
            user_id=8888,
            message_id=1,
            raw_text="会触发异常的提问",
        )
    )

    # Mock LLM provider raising RuntimeError
    mock_provider = AsyncMock()
    mock_provider.generate_stream.side_effect = RuntimeError("LLM Provider Timeout Error")

    with patch.object(engine.default_provider, "generate_stream", side_effect=RuntimeError("LLM Provider Timeout Error")):
        decisions = []
        async for decision in engine.stream_reasoning_loop(req):
            decisions.append(decision)

        # Must yield at least one decision with is_final=True so Go Core state machine resets
        assert len(decisions) >= 1
        assert decisions[-1].is_final is True
        assert decisions[-1].text_content == ""
