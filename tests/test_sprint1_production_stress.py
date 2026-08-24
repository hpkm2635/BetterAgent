import pytest
import asyncio
import time
import sys
import gc
from unittest.mock import AsyncMock, patch, MagicMock

from services.memory.vector_store import VectorMemoryStore
from services.memory.short_term_buffer import ShortTermMemoryBuffer
from services.memory.consolidator import MemoryConsolidator
from services.memory.memory_hub import MemoryHub
from shared.schema.payloads import InboundMessagePayload


# ============================================================================
# 生产环境仿真测试 1: 高并发端口/Socket句柄耗尽 (TIME_WAIT Port Exhaustion)
# ============================================================================

@pytest.mark.asyncio
async def test_embedding_connection_pool_port_exhaustion_stress():
    """
    模拟生产环境高 QPS 下反复创建 aiohttp.ClientSession 导致的端口耗尽隐患。
    如果 _embed_text 每次调用都实例化一个新的 ClientSession，在高并发下会导致 TCP Socket 进入 TIME_WAIT 状态。
    """
    session_creation_count = 0

    class TrackingClientSession:
        def __init__(self, *args, **kwargs):
            nonlocal session_creation_count
            session_creation_count += 1
            self.closed = False

        async def close(self):
            self.closed = True

        def post(self, url, json=None, headers=None, timeout=None):
            mock_resp = MagicMock()
            mock_resp.status = 200

            async def mock_json():
                return {"data": [{"embedding": [0.1] * 1536}]}

            mock_resp.json = mock_json

            mock_cm = AsyncMock()
            mock_cm.__aenter__.return_value = mock_resp
            return mock_cm

    with patch.dict("os.environ", {
        "EMBEDDING_BASE_URL": "http://127.0.0.1:8000/v1",
        "EMBEDDING_API_KEY": "sk-test-key",
        "EMBEDDING_MODEL": "text-embedding-3-small"
    }):
        store = VectorMemoryStore(collection_name="stress_test_embed")
        try:
            with patch("aiohttp.ClientSession", TrackingClientSession):
                start_time = time.time()
                # 模拟 100 个并发高频请求
                tasks = [store._embed_text(f"压测文本_{i}") for i in range(100)]
                results = await asyncio.gather(*tasks)
                elapsed = time.time() - start_time

                # 断言：100 次请求成功返回 1536 维向量
                assert len(results) == 100
                for vec in results:
                    assert len(vec) == 1536

                print(f"\n[生产隐患检测] 100 次 Embedding 请求共创建了 {session_creation_count} 个 HTTP ClientSession (耗时: {elapsed:.3f}s)")
                if session_creation_count > 1:
                    pytest.fail(
                        f"检测到架构隐患：100 次 Embedding 请求重复创建了 {session_creation_count} 个 ClientSession！"
                        f"生产上线高并发时将引发 ephemeral port 耗尽 (TIME_WAIT 堆积)！建议使用全局共享连接池。"
                    )
        finally:
            await store.close()


# ============================================================================
# 生产环境仿真测试 2: 内存泄露压测 (Unbounded Memory Growth / Leaks)
# ============================================================================

@pytest.mark.asyncio
async def test_memory_leak_unbounded_growth_stress():
    """
    压测长期运行下的内存泄露隐患：
    1. in_memory_docs 无上限 append (无 FIFO/TTL 清理机制)
    2. MemoryConsolidator._user_locks 字典无上限增长 (按用户 ID 永久留存)
    """
    store = VectorMemoryStore(collection_name="stress_test_leak", max_in_memory_capacity=500)
    consolidator = MemoryConsolidator()

    gc.collect()

    # 1. 模拟 600 个不同用户并发写入消息 (降级模式下写入 in_memory_docs)
    locks = []
    async def add_worker(uid):
        await store.add_memory_segment(user_id=uid, text=f"用户 {uid} 的长期事实描述消息，专业计算机")
        lock = consolidator.get_user_lock(uid)
        locks.append(lock)

    await asyncio.gather(*[add_worker(uid) for uid in range(600)])

    # 审计检查 1: in_memory_docs 有容量上限 (max_in_memory_capacity=500)
    final_in_memory_docs_len = len(store.in_memory_docs)
    assert final_in_memory_docs_len <= store.max_in_memory_capacity
    assert hasattr(store, "max_in_memory_capacity")

    # 审计检查 2: WeakValueDictionary 垃圾回收机制
    final_locks_len = len(consolidator._user_locks)
    assert final_locks_len == 600
    del locks
    gc.collect()
    assert len(consolidator._user_locks) == 0  # 自动垃圾回收释放无引用 Lock
    await store.close()


# ============================================================================
# 生产环境仿真测试 3: 高并发死锁与锁竞争压测 (Lock Contention & Race Conditions)
# ============================================================================

@pytest.mark.asyncio
async def test_high_concurrency_consolidation_deadlock_stress():
    """
    模拟生产环境 50 个并发协程同时调用 MemoryHub 读写与自动归纳，
    验证锁竞争吞吐量与是否发生死锁 (Timeout 保护)。
    """
    hub = MemoryHub()
    user_id = 999999

    # 构造 30 条消息直接触发自动 Consolidation 阈值 (threshold=15)
    inbound_messages = [
        InboundMessagePayload(
            event_id=f"evt_stress_{i}",
            source_component="gotd",
            chat_id=user_id,
            user_id=user_id,
            message_id=2000 + i,
            raw_text=f"高并发测试事实消息第 {i} 条：今天学习了量子力学和向量数据库，我的专业是计算机",
        )
        for i in range(30)
    ]

    start_time = time.time()
    
    # 50 个协程同时注入消息
    async def worker(msg):
        await hub.handle_inbound_message(msg)

    try:
        # 设置 30 秒超时保护，若死锁则触发 TimeoutError
        await asyncio.wait_for(
            asyncio.gather(*[worker(msg) for msg in inbound_messages]),
            timeout=30.0
        )
    except asyncio.TimeoutError:
        pytest.fail("高并发 MemoryHub 消息处理发生死锁 (Deadlock Detected)！30秒内未完成。")


    elapsed = time.time() - start_time
    history = await hub.short_term_buffer.get_recent_messages(user_id)
    print(f"\n[并发性能检测] 30 个并发消息注入与 Consolidation 耗时: {elapsed:.3f}s，历史记录数: {len(history)}")
    assert len(history) == 20  # max_capacity=20 截断
    await hub.vector_store.close()


# ============================================================================
# 生产环境仿真测试 4: 数据库网络抖动与异常恢复 (Network Flapping Resilience)
# ============================================================================

@pytest.mark.asyncio
async def test_redis_qdrant_network_flapping_recovery():
    """
    模拟生产环境 Redis / Qdrant 网络抖动（连接断开 -> 恢复 -> 再断开），
    验证系统是否能持续正常服务，不向上层抛出未捕获 Crash。
    """
    hub = MemoryHub()
    user_id = 888888

    # 1. 模拟 Async Redis 第一次崩塌
    mock_redis = AsyncMock()
    mock_redis.rpush.side_effect = ConnectionError("Redis connection lost")
    mock_redis.get.side_effect = ConnectionError("Redis connection lost")
    mock_redis.lrange.side_effect = ConnectionError("Redis connection lost")
    hub.short_term_buffer.redis_client = mock_redis

    msg1 = InboundMessagePayload(
        event_id="evt_flap_1",
        source_component="gotd",
        chat_id=user_id,
        user_id=user_id,
        message_id=1,
        raw_text="网络故障时的消息",
    )

    # 不应 Crash，自动降级至内存存储
    await hub.handle_inbound_message(msg1)
    history1 = await hub.short_term_buffer.get_recent_messages(user_id)
    assert len(history1) == 1
    assert history1[0]["content"] == "网络故障时的消息"

    # 2. 网络恢复
    mock_redis.rpush.side_effect = None
    mock_redis.rpush.return_value = 2
    mock_redis.lrange.side_effect = None
    mock_redis.lrange.return_value = [
        '{"role": "user", "content": "网络故障时的消息"}',
        '{"role": "user", "content": "网络恢复后的消息"}'
    ]

    msg2 = InboundMessagePayload(
        event_id="evt_flap_2",
        source_component="gotd",
        chat_id=user_id,
        user_id=user_id,
        message_id=2,
        raw_text="网络恢复后的消息",
    )
    await hub.handle_inbound_message(msg2)
    history2 = await hub.short_term_buffer.get_recent_messages(user_id)
    assert len(history2) == 2
    await hub.vector_store.close()
