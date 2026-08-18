import logging
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from services.cognitive.providers.base import BaseLLMProvider
from services.cognitive.providers.factory import ProviderFactory
from services.cognitive.providers.openai_provider import OpenAIProvider
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.providers.claude_provider import ClaudeProvider


# ============================================================================
# 1. ProviderFactory 工厂模式与注册表测试
# ============================================================================

def test_provider_factory_registration_and_get():
    """验证 ProviderFactory 的查找、别名解析及单例缓存机制"""
    ProviderFactory.invalidate_cache()

    p_gemini = ProviderFactory.get_provider("gemini")
    assert isinstance(p_gemini, GeminiProvider)

    p_claude = ProviderFactory.get_provider("claude")
    assert isinstance(p_claude, ClaudeProvider)

    p_openai = ProviderFactory.get_provider("openai")
    assert isinstance(p_openai, OpenAIProvider)
    assert p_openai.provider_name == "openai"

    p_deepseek = ProviderFactory.get_provider("deepseek")
    assert isinstance(p_deepseek, OpenAIProvider)
    assert p_deepseek.provider_name == "deepseek"

    p_qwen = ProviderFactory.get_provider("qwen")
    assert isinstance(p_qwen, OpenAIProvider)
    assert p_qwen.provider_name == "qwen"
    assert p_qwen.supports_vision() is True
    assert "aliyuncs.com" in p_qwen.base_url

    # 未知 Provider 兜底回落至 Gemini
    p_unknown = ProviderFactory.get_provider("unknown_xyz_provider")
    assert isinstance(p_unknown, GeminiProvider)


# ============================================================================
# 2. OpenAIProvider 消息格式化与 Tool Call ID 严格匹配测试
# ============================================================================

def test_openai_provider_build_messages_and_tool_call_ids():
    """验证 OpenAIProvider._build_messages 成功转换 vision_frame 及保持 tool_call_id 匹配"""
    provider = OpenAIProvider(api_key="sk-test", provider_name="openai")

    messages = [
        {"role": "system", "content": "你是 Camelia"},
        {
            "role": "user",
            "content": "看这张图",
            "metadata": {
                "vision_frame": {
                    "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                    "format": "png",
                }
            },
        },
        {
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_calls": [{"name": "generate_image", "args": {"prompt": "catgirl"}}]
            },
        },
        {
            "role": "tool",
            "content": '{"status": "success", "photo_path": "./temp/photo.jpg"}',
        },
    ]

    built = provider._build_messages(messages, system_prompt="系统层 Prompt")

    assert built[0]["role"] == "system"
    assert built[0]["content"] == "系统层 Prompt"

    user_msg = next(m for m in built if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][1]["type"] == "image_url"
    assert "data:image/png;base64," in user_msg["content"][1]["image_url"]["url"]

    assistant_msg = next(m for m in built if m["role"] == "assistant")
    tool_call_id = assistant_msg["tool_calls"][0]["id"]
    assert tool_call_id.startswith("call_")

    tool_msg = next(m for m in built if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == tool_call_id, "验证成功：tool 消息的 tool_call_id 与 assistant 严格匹配！"


# ============================================================================
# 3. DeepSeek-R1 思考链 (thinking_delta) 提取测试
# ============================================================================

@pytest.mark.asyncio
async def test_openai_provider_deepseek_r1_reasoning_content_extraction():
    """验证 DeepSeek-R1 的 delta.reasoning_content 被正确提取为 thinking_delta"""
    provider = OpenAIProvider(api_key="sk-test", provider_name="deepseek")

    chunk1 = MagicMock()
    chunk1.choices = [MagicMock(delta=MagicMock(reasoning_content="用户似乎很开心，我应该用温柔的语气回应。"))]
    chunk1.choices[0].delta.content = None
    chunk1.choices[0].delta.tool_calls = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock(delta=MagicMock(content="主人，今天过得开心吗喵~"))]
    chunk2.choices[0].delta.reasoning_content = None
    chunk2.choices[0].delta.tool_calls = None

    class MockAsyncStream:
        async def __aiter__(self):
            for c in [chunk1, chunk2]:
                yield c

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MockAsyncStream())
    provider._client = mock_client

    events = []
    async for ev in provider.generate_stream([{"role": "user", "content": "你好"}]):
        events.append(ev)

    thinking_ev = next((e for e in events if e.get("type") == "thinking_delta"), None)
    text_ev = next((e for e in events if e.get("type") == "text"), None)

    assert thinking_ev is not None
    assert thinking_ev["text"] == "用户似乎很开心，我应该用温柔的语气回应。"
    assert text_ev is not None
    assert text_ev["delta"] == "主人，今天过得开心吗喵~"


# ============================================================================
# 4. 非官方中转站安全日志告警与 502 HTML 异常保护测试
# ============================================================================

def test_openai_provider_security_notice_warning_log(caplog):
    """验证配置非官方中转站 base_url 时触发高亮 SECURITY & PRIVACY WARNING"""
    with caplog.at_level(logging.WARNING):
        OpenAIProvider(
            api_key="sk-test",
            base_url="https://my-third-party-proxy.com/v1",
            provider_name="custom_proxy",
        )

    assert "SECURITY & PRIVACY NOTICE" in caplog.text
    assert "https://my-third-party-proxy.com/v1" in caplog.text


@pytest.mark.asyncio
async def test_openai_provider_non_json_502_proxy_error_handling():
    """验证第三方中转站掉线返回 502 Bad Gateway HTML 时不崩溃并输出友好提示"""
    provider = OpenAIProvider(
        api_key="sk-test",
        base_url="https://unstable-proxy.com/v1",
        provider_name="deepseek",
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("502 Bad Gateway: <html>Nginx Error</html>"))
    provider._client = mock_client

    events = []
    async for ev in provider.generate_stream([{"role": "user", "content": "hi"}]):
        events.append(ev)

    assert len(events) > 0
    assert "502" in events[0].get("delta", "") or "中转站" in events[0].get("delta", "")


@pytest.mark.asyncio
async def test_openai_provider_qwen_text_based_tool_call_interception():
    """验证 Qwen/Ollama 在 content 文本流中吐出 <tool_call>{...}</tool_call> 时被拦截、从文本抹除并解析为原生 tool_calls"""
    provider = OpenAIProvider(api_key="sk-test", provider_name="qwen")

    # 模拟 Qwen 在 content 中吐出文本和 <tool_call> 标签
    raw_content = (
        "喵~看来我们进入了一个新的游戏决策点呢！我将调用 'get_game_state' 获取状态。"
        '<tool_call>{"name": "get_game_state", "arguments": {"format": "json"}}</tool_call>'
    )

    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=MagicMock(content=raw_content))]
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = None

    class MockAsyncStream:
        async def __aiter__(self):
            yield chunk

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MockAsyncStream())
    provider._client = mock_client

    events = []
    async for ev in provider.generate_stream([{"role": "user", "content": "hi"}]):
        events.append(ev)

    # 校验 1: 文本流中不包含 <tool_call> 标签
    text_events = [e for e in events if e.get("type") == "text"]
    full_text = "".join(e.get("delta", "") for e in text_events)
    assert "<tool_call>" not in full_text
    assert "喵~看来我们进入了一个新的游戏决策点呢！" in full_text

    # 校验 2: 提取并转化为原生的 tool_calls 事件
    tool_events = [e for e in events if e.get("type") == "tool_calls"]
    assert len(tool_events) == 1
    calls = tool_events[0]["calls"]
    assert len(calls) == 1
    assert calls[0]["name"] == "get_game_state"
    assert calls[0]["args"] == {"format": "json"}


@pytest.mark.asyncio
async def test_openai_provider_qwen_codeblock_tool_call_interception():
    """验证 Qwen 吐出 ```json{ "tool": "get_game_state","format": "json" }``` 代码块时被干净拦截解析"""
    provider = OpenAIProvider(api_key="sk-test", provider_name="qwen")

    raw_content = (
        "喵~看来我们到了一个新的决策点，让我先查看一下当前的游戏状态。"
        ' ```json{ "tool": "get_game_state","format": "json" }'
    )

    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=MagicMock(content=raw_content))]
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = None

    class MockAsyncStream:
        async def __aiter__(self):
            yield chunk

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=MockAsyncStream())
    provider._client = mock_client

    events = []
    async for ev in provider.generate_stream([{"role": "user", "content": "hi"}]):
        events.append(ev)

    text_events = [e for e in events if e.get("type") == "text"]
    full_text = "".join(e.get("delta", "") for e in text_events)
    assert "```json" not in full_text
    assert "喵~看来我们到了一个新的决策点" in full_text

    tool_events = [e for e in events if e.get("type") == "tool_calls"]
    assert len(tool_events) == 1
    calls = tool_events[0]["calls"]
    assert len(calls) == 1
    assert calls[0]["name"] == "get_game_state"
    assert calls[0]["args"] == {"format": "json"}
