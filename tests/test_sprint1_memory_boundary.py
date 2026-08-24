import pytest
import asyncio
import time
import math
from unittest.mock import AsyncMock, patch, MagicMock

from services.memory.vector_store import VectorMemoryStore, _hashed_embed
from services.memory.short_term_buffer import ShortTermMemoryBuffer
from services.memory.user_profile import UserProfileManager
from services.memory.consolidator import MemoryConsolidator
from services.memory.memory_hub import MemoryHub
from shared.schema.payloads import InboundMessagePayload, EnrichContextReqPayload, ActionCompletedPayload, ActionDecisionPayload


# ============================================================================
# 1. VectorMemoryStore Boundary & Failure Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_vector_store_empty_and_whitespace_inputs():
    """Verify handling of empty, whitespace, and extreme string inputs."""
    store = VectorMemoryStore(collection_name="test_boundary_memories")
    try:
        # Adding empty or whitespace text should return empty doc_id and not store anything
        doc_id1 = await store.add_memory_segment(user_id=1001, text="")
        doc_id2 = await store.add_memory_segment(user_id=1001, text="   \n\t  ")
        assert doc_id1 == ""
        assert doc_id2 == ""
        assert len(store.in_memory_docs) == 0

        # Query with empty/whitespace text should return []
        res1 = await store.search_relevant_memories(user_id=1001, query_text="")
        res2 = await store.search_relevant_memories(user_id=1001, query_text="    ")
        assert res1 == []
        assert res2 == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_vector_store_ebbinghaus_decay_future_and_past_timestamps():
    """Verify decay math with future timestamps, zero decay, and extreme age."""
    store = VectorMemoryStore(collection_name="test_decay", decay_lambda=0.01)
    store._qdrant_disabled = True
    store._embed_text = AsyncMock(side_effect=lambda text: _hashed_embed(text, store.dim))
    user_id = 2002


    try:
        # 1. Future timestamp (clock skew scenario) -> delta_t_hours clamped to >= 0
        future_doc = {
            "id": "future_doc",
            "user_id": user_id,
            "text": "未来发生的事",
            "vector": _hashed_embed("未来发生的事", store.dim),
            "metadata": {},
            "timestamp": time.time() + 3600.0  # 1 hour in future
        }
        store.in_memory_docs.append(future_doc)

        res = await store.search_relevant_memories(user_id=user_id, query_text="未来发生的事", score_threshold=0.1)
        assert len(res) >= 1
        assert res[0] == "未来发生的事"

        # 2. Extreme age memory (10 years old) -> decayed score should fall below standard threshold
        old_doc = {
            "id": "old_doc",
            "user_id": user_id,
            "text": "十年前的古老回忆",
            "vector": _hashed_embed("十年前的古老回忆", store.dim),
            "metadata": {},
            "timestamp": time.time() - (87600 * 3600.0)  # 10 years ago
        }
        store.in_memory_docs.append(old_doc)

        # Search with score_threshold=0.5 -> old memory should be filtered out by decay
        res_filtered = await store.search_relevant_memories(user_id=user_id, query_text="古老回忆", score_threshold=0.5)
        assert "十年前的古老回忆" not in res_filtered
    finally:
        await store.close()


def test_vector_store_cosine_similarity_dimension_mismatch_and_zeros():
    """Verify cosine similarity helper with zero vectors and dimension mismatch."""
    # Zero vector
    v_zero = [0.0, 0.0, 0.0]
    v1 = [1.0, 2.0, 3.0]
    sim_zero = VectorMemoryStore._cosine_similarity(v_zero, v1)
    assert sim_zero == 0.0

    # Dimension mismatch
    v_short = [1.0, 2.0]
    v_long = [1.0, 2.0, 3.0]
    sim_mismatch = VectorMemoryStore._cosine_similarity(v_short, v_long)
    assert sim_mismatch == 0.0

    # None / Empty inputs
    assert VectorMemoryStore._cosine_similarity([], v1) == 0.0
    assert VectorMemoryStore._cosine_similarity(v1, []) == 0.0


@pytest.mark.asyncio
async def test_vector_store_external_embedding_endpoint_failure_fallback():
    """Verify external embedding endpoint 500 error falls back smoothly to hashed embedder."""
    store = VectorMemoryStore(collection_name="test_embed_fallback")

    with patch.dict("os.environ", {
        "EMBEDDING_BASE_URL": "http://mock-api.local/v1",
        "EMBEDDING_API_KEY": "fake-key",
        "EMBEDDING_MODEL": "text-embedding-3-small"
    }):
        # Mock aiohttp failure (500 internal server error)
        mock_response = MagicMock()
        mock_response.status = 500

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.post.return_value = mock_cm

        async def get_mock_session():
            return mock_session

        with patch.object(store, "_get_http_session", side_effect=get_mock_session):
            vector = await store._embed_text("测试外部Embedding失败")
            assert len(vector) == store.dim
            assert isinstance(vector, list)
    await store.close()


# ============================================================================
# 2. ShortTermMemoryBuffer Boundary & Cursor Desync Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_short_term_buffer_user_id_string_coercion_and_clear():
    """Verify user_id string vs int coercion and complete clear operation."""
    buf = ShortTermMemoryBuffer(max_capacity=10)
    user_id_str = "9876543210"
    user_id_int = 9876543210

    # Add message with string user_id
    await buf.add_message(user_id=user_id_str, role="user", content="测试用户ID字符串转换")

    # Read back with int user_id
    recent = await buf.get_recent_messages(user_id=user_id_int)
    assert len(recent) == 1
    assert recent[0]["content"] == "测试用户ID字符串转换"

    # Mark consolidated
    await buf.mark_consolidated(user_id=user_id_str, count=1)
    unconsolidated = await buf.get_unconsolidated_messages(user_id=user_id_int)
    assert len(unconsolidated) == 0

    # Clear buffer
    await buf.clear_buffer(user_id=user_id_int)
    assert len(await buf.get_recent_messages(user_id=user_id_str)) == 0
    assert buf.cursors[user_id_int] == 0


@pytest.mark.asyncio
async def test_short_term_buffer_mark_consolidated_overflow_clamping():
    """Verify mark_consolidated clamps cursor when count exceeds buffer length."""
    buf = ShortTermMemoryBuffer(max_capacity=10)
    buf._redis_disabled = True
    user_id = 3003

    for i in range(5):
        await buf.add_message(user_id=user_id, role="user", content=f"消息 {i}")

    # Mark consolidated with count=999 (far exceeds total 5)
    await buf.mark_consolidated(user_id=user_id, count=999)
    assert buf.cursors[user_id] == 5


    unconsolidated = await buf.get_unconsolidated_messages(user_id=user_id)
    assert len(unconsolidated) == 0


@pytest.mark.asyncio
async def test_short_term_buffer_max_capacity_overflow():
    """Verify max_capacity trimming preserves newest messages."""
    capacity = 5
    buf = ShortTermMemoryBuffer(max_capacity=capacity)
    user_id = 4004

    for i in range(10):
        await buf.add_message(user_id=user_id, role="user", content=f"msg_{i}")

    recent = await buf.get_recent_messages(user_id=user_id, limit=10)
    assert len(recent) == capacity
    assert recent[0]["content"] == "msg_5"
    assert recent[-1]["content"] == "msg_9"


# ============================================================================
# 3. UserProfileManager Redis Fallback & Formatting Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_user_profile_manager_malformed_likes_fallback():
    """Verify UserProfileManager correctly handles comma-separated strings or missing profile fields."""
    mgr = UserProfileManager()
    user_id = 5005

    # Mock Redis client returning comma-separated string instead of JSON array
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        "preferred_name": "老张",
        "likes": "摸头, 喝茶, 看书",
        "dislikes": "吵闹"
    }
    mgr.redis_client = mock_redis

    profile = await mgr.get_profile(user_id)
    assert profile["preferred_name"] == "老张"
    assert profile["likes"] == ["摸头", "喝茶", "看书"]
    assert profile["dislikes"] == ["吵闹"]

    prompt = await mgr.get_formatted_profile_prompt(user_id)
    assert "老张" in prompt
    assert "摸头, 喝茶, 看书" in prompt


@pytest.mark.asyncio
async def test_user_profile_manager_empty_likes_dislikes_formatting():
    """Verify formatting when likes and dislikes are empty."""
    mgr = UserProfileManager()
    user_id = 6006
    mgr.redis_client = None

    prompt = await mgr.get_formatted_profile_prompt(user_id)
    assert "[用户画像] 称呼: 主人, 喜好: 无, 讨厌: 无" in prompt


# ============================================================================
# 4. MemoryHub Integration & Multi-User Concurrency
# ============================================================================

@pytest.mark.asyncio
async def test_memory_hub_handle_action_completed_photo():
    """Verify handle_action_completed handles photo action and records assistant history."""
    hub = MemoryHub()
    hub.short_term_buffer._redis_disabled = True
    chat_id = 7007


    decision = ActionDecisionPayload(
        event_id="act_evt_1",
        source_component="cognitive",
        chat_id=chat_id,
        action_type="send_photo",
        text_content="主人的猫娘自拍到了哦~",
        photo_path="/tmp/cat_photo.jpg",
        is_final=True
    )
    action_completed = ActionCompletedPayload(
        event_id="act_evt_1",
        source_component="webgateway",
        chat_id=chat_id,
        action_decision=decision,
        status="success"
    )

    try:
        await hub.handle_action_completed(action_completed)

        recent = await hub.short_term_buffer.get_recent_messages(chat_id)
        assert len(recent) == 1
        assert recent[0]["role"] == "assistant"
        assert "[助手已发送照片: /tmp/cat_photo.jpg]" in recent[0]["content"]
        assert "主人的猫娘自拍到了哦~" in recent[0]["content"]

    finally:
        await hub.vector_store.close()


@pytest.mark.asyncio
async def test_memory_consolidator_user_concurrency_locks():
    """Verify MemoryConsolidator handles rapid concurrent consolidation per user without race conditions."""
    consolidator = MemoryConsolidator()
    vector_store = VectorMemoryStore(collection_name="test_concurrency")
    user_id = 8008
    try:
        messages = [{"role": "user", "content": f"并发事实消息_{i}，我的专业是计算机科学"} for i in range(10)]
        
        # Concurrent consolidation tasks for the same user
        tasks = [consolidator.consolidate(user_id, messages, vector_store) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        for res in results:
            assert res["user_id"] == user_id
            assert res["consolidated_count"] == 10
    finally:
        await vector_store.close()
