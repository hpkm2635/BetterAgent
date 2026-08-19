import pytest
from shared.text_utils import clean_tts_text
from services.cognitive.providers.openai_provider import OpenAIProvider


def test_clean_tts_text_filters_emojis():
    assert clean_tts_text("❤️") == ""
    assert clean_tts_text("😽") == ""
    assert clean_tts_text("在呢在呢，主人！喵呜～❤️") == "在呢在呢，主人！喵呜～"


def test_clean_tts_text_filters_code_artifacts_and_none():
    assert clean_tts_text("}\n```") == ""
    assert clean_tts_text("None") == ""
    assert clean_tts_text("undefined") == ""
    assert clean_tts_text("```json\n{\"tool\": \"test\"}\n```") == ""


def test_normalize_tool_name_preserves_sts2_prefix():
    from services.cognitive.tool_registry import ToolRegistry
    registry = ToolRegistry()

    text = '<tool_call>{"name": "sts2_play_card", "arguments": {"card_index": 0}}</tool_call>'
    cleaned, calls = OpenAIProvider._extract_text_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "play_card"
    assert calls[0]["args"]["card_index"] == 0
    assert registry.get_tool(calls[0]["name"]) is not None

    text_shorthand = '<tool_call>{"name": "get_game_state", "arguments": {}}</tool_call>'
    _, calls_short = OpenAIProvider._extract_text_tool_calls(text_shorthand)
    assert len(calls_short) == 1
    assert calls_short[0]["name"] == "get_game_state"
    assert registry.get_tool(calls_short[0]["name"]) is not None
