import pytest
import asyncio
from services.memory.vector_store import VectorMemoryStore
from services.memory.consolidator import MemoryConsolidator
from services.memory.user_profile import UserProfileManager
from services.memory.short_term_buffer import ShortTermMemoryBuffer
from services.memory.memory_hub import MemoryHub
from shared.schema.payloads import InboundMessagePayload, EnrichContextReqPayload


@pytest.mark.asyncio
async def test_vector_memory_store_hashed_fallback():
    store = VectorMemoryStore(collection_name="test_memories", decay_lambda=0.01)
    try:
        doc_id = await store.add_memory_segment(user_id=123456789012345, text="用户最喜欢的食物是烤鱼", metadata={"tag": "food"})
        assert doc_id != ""

        results = await store.search_relevant_memories(user_id=123456789012345, query_text="烤鱼", top_k=3, score_threshold=0.1)
        assert len(results) >= 1
        assert "烤鱼" in results[0]

        # Verify user_id isolation
        other_results = await store.search_relevant_memories(user_id=999999999999999, query_text="烤鱼", top_k=3)
        assert len(other_results) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_memory_consolidator_and_cursor():
    buffer = ShortTermMemoryBuffer(max_capacity=20)
    user_id = 987654321012345
    await buffer.clear_buffer(user_id)

    # Add messages
    for i in range(16):
        await buffer.add_message(user_id=user_id, role="user", content=f"这是第 {i+1} 条具体的聊天事实消息内容，我的专业是计算机科学")

    unconsolidated = await buffer.get_unconsolidated_messages(user_id)
    assert len(unconsolidated) == 16

    consolidator = MemoryConsolidator(consolidation_threshold=15)
    vector_store = VectorMemoryStore(collection_name="test_memories")
    try:
        res = await consolidator.consolidate(user_id=user_id, messages=unconsolidated, vector_store=vector_store)

        assert res["user_id"] == user_id
        assert res["consolidated_count"] == 16
        assert len(res["facts"]) > 0

        await buffer.mark_consolidated(user_id, len(unconsolidated))
        after_cursor = await buffer.get_unconsolidated_messages(user_id)
        assert len(after_cursor) == 0
    finally:
        await vector_store.close()


@pytest.mark.asyncio
async def test_user_profile_manager_defaults():
    mgr = UserProfileManager()
    user_id = 9988776655443322
    try:
        if mgr.redis_client:
            await mgr.redis_client.delete(f"betteragent:profile:{user_id}", f"user_profile:{user_id}")
    except Exception:
        pass

    profile = await mgr.get_profile(user_id)
    assert profile["preferred_name"] == "主人"
    assert profile["likes"] == []
    assert profile["dislikes"] == []

    await mgr.update_fact(user_id, "preferred_name", "小明")
    updated = await mgr.get_profile(user_id)
    assert updated["preferred_name"] == "小明"

    prompt = await mgr.get_formatted_profile_prompt(user_id)
    assert "小明" in prompt



@pytest.mark.asyncio
async def test_memory_hub_enrich_and_auto_consolidate():
    hub = MemoryHub()
    user_id = 555444333222111
    chat_id = 555444333222111

    inbound = InboundMessagePayload(
        event_id="evt_test_1",
        source_component="gotd",
        chat_id=chat_id,
        user_id=user_id,
        message_id=101,
        raw_text="我明天要去图书馆参加高数复习研讨会",
    )

    enrich_req = EnrichContextReqPayload(
        event_id="evt_test_1",
        source_component="csm",
        chat_id=chat_id,
        user_id=user_id,
        current_state="IDLE",
        emotion_description="当前情绪: 愉快",
        inbound_message=inbound,
        trigger_type="user_message",
        source_channel="telegram",
    )

    try:
        await hub.handle_inbound_message(inbound)
        reasoning_req = await hub.handle_enrich_context_req(enrich_req)
        assert reasoning_req.chat_id == chat_id
        assert reasoning_req.user_id == user_id
        assert len(reasoning_req.short_term_history) >= 1
        assert reasoning_req.short_term_history[-1]["content"] == "我明天要去图书馆参加高数复习研讨会"
    finally:
        await hub.vector_store.close()


@pytest.mark.asyncio
async def test_memory_hub_enrich_forwards_generation_id():
    """handle_enrich_context_req must forward the caller's generation_id, not
    silently reset it to the ReasoningRequestPayload default (1) -- otherwise
    the stale/interrupted-generation discard mechanism breaks at this hop.
    """
    hub = MemoryHub()
    user_id = 555444333222112
    chat_id = 555444333222112

    inbound = InboundMessagePayload(
        event_id="evt_test_genid",
        source_component="gotd",
        chat_id=chat_id,
        user_id=user_id,
        message_id=101,
        generation_id=7,
        raw_text="随便说点什么",
    )

    enrich_req = EnrichContextReqPayload(
        event_id="evt_test_genid",
        source_component="csm",
        chat_id=chat_id,
        user_id=user_id,
        generation_id=7,
        current_state="IDLE",
        emotion_description="当前情绪: 愉快",
        inbound_message=inbound,
        trigger_type="user_message",
        source_channel="telegram",
    )

    try:
        await hub.handle_inbound_message(inbound)
        reasoning_req = await hub.handle_enrich_context_req(enrich_req)
        assert reasoning_req.generation_id == 7
    finally:
        await hub.vector_store.close()
