import pytest
import inspect
from unittest.mock import AsyncMock, patch, MagicMock

from services.cognitive.providers.base import BaseLLMProvider
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.providers.claude_provider import ClaudeProvider


def test_provider_abstract_compliance():
    """Verify GeminiProvider and ClaudeProvider implement all abstract methods of BaseLLMProvider."""
    assert not inspect.isabstract(GeminiProvider)
    assert not inspect.isabstract(ClaudeProvider)

    for provider_cls in (GeminiProvider, ClaudeProvider):
        assert issubclass(provider_cls, BaseLLMProvider)
        instance = provider_cls(api_key="placeholder")
        assert hasattr(instance, "generate")
        assert hasattr(instance, "generate_stream")
        assert hasattr(instance, "supports_vision")


@pytest.mark.asyncio
async def test_claude_provider_missing_key_behavior():
    """Verify ClaudeProvider sets client to None when key is missing or placeholder."""
    provider = ClaudeProvider(api_key="your_claude_api_key")
    assert provider.client is None

    events = []
    async for ev in provider.generate_stream(messages=[{"role": "user", "content": "hello"}]):
        events.append(ev)

    assert len(events) == 1
    assert events[0] == {"type": "text", "delta": ""}


@pytest.mark.asyncio
async def test_claude_provider_streaming_and_delegation():
    """Verify ClaudeProvider stream parsing and generate() delegation."""
    provider = ClaudeProvider(api_key="sk-ant-test-key-12345")

    # Mock anthropic streaming response
    mock_event1 = MagicMock()
    mock_event1.type = "content_block_delta"
    mock_event1.delta = MagicMock(type="text_delta", text="Hello ")

    mock_event2 = MagicMock()
    mock_event2.type = "content_block_delta"
    mock_event2.delta = MagicMock(type="text_delta", text="Master!")

    async def mock_stream_gen():
        yield mock_event1
        yield mock_event2

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(side_effect=lambda: mock_stream_gen())
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.messages.stream = MagicMock(return_value=mock_stream_ctx)
    provider.client = mock_client

    # Test generate_stream
    events = []
    async for ev in provider.generate_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    assert len(events) == 2
    assert events[0]["delta"] == "Hello "
    assert events[1]["delta"] == "Master!"

    # Test generate delegation
    res = await provider.generate(messages=[{"role": "user", "content": "hi"}])
    assert res["text"] == "Hello Master!"
    assert res["finish_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_gemini_provider_generate_delegation():
    """Verify GeminiProvider.generate() delegates to generate_stream()."""
    provider = GeminiProvider(api_key="placeholder")

    async def mock_stream(messages, tools_schema=None, system_prompt=None, cancel_event=None):
        yield {"type": "text", "delta": "Gemini "}
        yield {"type": "text", "delta": "Response喵~"}

    with patch.object(provider, "generate_stream", side_effect=mock_stream):
        res = await provider.generate(messages=[{"role": "user", "content": "hello"}])
        assert res["text"] == "Gemini Response喵~"
        assert res["finish_reason"] == "STOP"
