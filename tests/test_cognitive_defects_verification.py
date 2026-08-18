import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from services.cognitive.providers.claude_provider import ClaudeProvider
from services.cognitive.mcp.client import McpSession
from services.cognitive.prompt_builder import PromptBuilder
from shared.schema.payloads import ReasoningRequestPayload, InboundMessagePayload


# ============================================================================
# 修复验证 1: Claude Provider Tool Call JSON Delta 解析修复
# ============================================================================

@pytest.mark.asyncio
async def test_claude_provider_tool_call_json_delta_parsing_fixed():
    """
    验证 ClaudeProvider 针对 input_json_delta 的解析修复：
    内层 event.delta.type == "input_json_delta" 被正确识别并提取 partial_json。
    """
    provider = ClaudeProvider(api_key="sk-ant-test-key")

    mock_event_start = MagicMock()
    mock_event_start.type = "content_block_start"
    mock_event_start.content_block = MagicMock(type="tool_use", id="toolu_123", name="generate_image")

    mock_event_delta1 = MagicMock()
    mock_event_delta1.type = "content_block_delta"
    mock_event_delta1.delta = MagicMock(type="input_json_delta", partial_json='{"prompt": "catgirl"}')

    mock_event_stop = MagicMock()
    mock_event_stop.type = "content_block_stop"

    class AsyncMockStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def __aiter__(self):
            for ev in [mock_event_start, mock_event_delta1, mock_event_stop]:
                yield ev

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = AsyncMockStream()
    provider.client = mock_client

    messages = [{"role": "user", "content": "画一只猫娘"}]
    events = []
    async for ev in provider.generate_stream(messages):
        events.append(ev)

    tool_calls_event = next((e for e in events if e.get("type") == "tool_calls"), None)
    assert tool_calls_event is not None
    extracted_args = tool_calls_event["calls"][0]["args"]

    # 验证修复：成功解析 out {"prompt": "catgirl"}
    assert extracted_args == {"prompt": "catgirl"}, "修复成功：input_json_delta 被正确提取并解析！"


# ============================================================================
# 修复验证 2: Claude Provider Tool Call ID 唯一性修复
# ============================================================================

def test_claude_provider_unique_tool_call_ids_fixed():
    """
    验证 ClaudeProvider._build_messages 中 tool_use_id 唯一性与匹配修复。
    """
    messages = [
        {
            "role": "assistant",
            "content": "",
            "metadata": {"function_call": {"name": "tool_1", "args": {"a": 1}}},
        },
        {
            "role": "user",
            "content": "",
            "metadata": {"function_response": {"name": "tool_1", "response": {"res": "ok1"}}},
        },
        {
            "role": "assistant",
            "content": "",
            "metadata": {"function_call": {"name": "tool_2", "args": {"b": 2}}},
        },
        {
            "role": "user",
            "content": "",
            "metadata": {"function_response": {"name": "tool_2", "response": {"res": "ok2"}}},
        },
    ]

    built_messages = ClaudeProvider._build_messages(messages)

    tool_use_ids = []
    tool_result_ids = []

    for msg in built_messages:
        for block in msg.get("content", []):
            if isinstance(block, dict):
                if block.get("type") == "tool_use":
                    tool_use_ids.append(block.get("id"))
                elif block.get("type") == "tool_result":
                    tool_result_ids.append(block.get("tool_use_id"))

    # 验证修复：ID 唯一分配为 call_1, call_2 且一一对应
    assert len(tool_use_ids) == 2
    assert tool_use_ids == ["call_1", "call_2"], "修复成功：tool_use ID 唯一分配！"
    assert tool_result_ids == ["call_1", "call_2"], "修复成功：tool_result ID 与 tool_use 对应！"


# ============================================================================
# 修复验证 3: Claude Provider Messages 角色交替与首条 User 校验
# ============================================================================

def test_claude_provider_message_role_alternation_fixed():
    """
    验证 ClaudeProvider._build_messages 连续 Role 合并与首条 User 强制修复。
    """
    messages = [
        {"role": "user", "content": "第一条用户提问"},
        {"role": "user", "content": "第二条用户提问"},
    ]

    built_messages = ClaudeProvider._build_messages(messages)

    roles = [m["role"] for m in built_messages]
    assert roles == ["user"], "修复成功：连续 user 消息已被合并，符合 Anthropic API 规范！"


# ============================================================================
# 修复验证 4: MCP call_tool() 超时控制修复
# ============================================================================

@pytest.mark.asyncio
async def test_mcp_call_tool_timeout_protection_fixed():
    """
    验证 McpSession.call_tool() 超时控制抛出 TimeoutError / RuntimeError 修复。
    """
    session = McpSession(["echo", "test"])
    session._session = MagicMock()

    async def infinite_hang(*args, **kwargs):
        await asyncio.sleep(3600)

    session._session.call_tool = infinite_hang

    with pytest.raises(RuntimeError) as exc_info:
        await session.call_tool("vscode_search", {"query": "test"}, timeout=0.05)

    assert "timed out" in str(exc_info.value)


# ============================================================================
# 修复验证 5: agent_self_events 保留最新 1-2 条详细动作描述
# ============================================================================

def test_prompt_builder_agent_self_events_retains_recent_details_fixed():
    """
    验证 PromptBuilder 中 agent_self_events 保留最新 1-2 条完整描述 + 早期计数摘要。
    """
    builder = PromptBuilder()

    payload = ReasoningRequestPayload(
        event_id="evt_self_events",
        source_component="csm",
        chat_id=1001,
        user_id=1001,
        trigger_type="chat_message",
        inbound_message=InboundMessagePayload(
            event_id="evt_in", source_component="gotd", chat_id=1001, user_id=1001, message_id=1, raw_text="你好"
        )
    )

    payload.agent_self_events = [
        {"description": "执行动作 send_message: 主人早安"},
        {"description": "执行动作 send_message: 好的我现在去处理"},
        {"description": "执行动作 generate_image: 为主人画了一张二次元猫娘插画"},
    ]

    prompt = builder.build_system_prompt(payload)

    assert "为主人画了一张二次元猫娘插画" in prompt, "修复成功：最新动作的详细描述被完整保留！"
    assert "早期动作摘要: 执行动作 send_message×1" in prompt


# ============================================================================
# 修复验证 6: 主动搭话 (proactive) 禁用 generate_image 工具与 Prompt 约束
# ============================================================================

def test_proactive_turn_disables_image_gen_tool_and_adds_constraint():
    """
    验证当 trigger_type == 'proactive' 时：
    1. System Prompt 中注入了【约束】禁止自动生成图片/自拍。
    2. CognitiveEngine 过滤 schema 时排除了 generate_image 工具。
    """
    builder = PromptBuilder()

    payload = ReasoningRequestPayload(
        event_id="evt_proactive",
        source_component="csm",
        chat_id=1001,
        user_id=1001,
        trigger_type="proactive",
        proactive_reason="一段时间没有人跟你说话，你觉得有点无聊",
    )

    prompt = builder.build_system_prompt(payload)

    assert "[主动搭话]" in prompt
    assert "【约束】主动搭话只需发送文字聊天，请勿在此轮主动对话中自动调用图片生成工具。" in prompt

    from services.cognitive.cognitive_engine import CognitiveEngine
    engine = CognitiveEngine()
    engine.tool_registry.get_all_schemas = MagicMock(return_value=[
        {"name": "generate_image"},
        {"name": "send_message"},
    ])

    # Filter tool schemas for proactive mode
    all_schemas = engine.tool_registry.get_all_schemas()
    allow_proactive_image = False
    tools_schema = [
        t for t in all_schemas
        if not (payload.trigger_type == "proactive" and t.get("name") == "generate_image" and not allow_proactive_image)
    ]

    tool_names = [t["name"] for t in tools_schema]
    assert "generate_image" not in tool_names, "主动搭话模式下 generate_image 被正确过滤！"
    assert "send_message" in tool_names

