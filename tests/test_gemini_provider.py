import pytest
import asyncio
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.cognitive_engine import CognitiveEngine
from services.cognitive.tool_registry import ToolRegistry


def test_gemini_provider_messages_to_contents_thought_signature():
    provider = GeminiProvider()
    messages = [
        {
            "role": "model",
            "content": "",
            "metadata": {
                "function_call": {
                    "name": "sts2_play_card",
                    "args": {"card_index": 2},
                    "thought_signature": None,
                }
            },
        }
    ]
    contents = provider._messages_to_contents(messages)
    assert len(contents) == 1
    part = contents[0].parts[0]
    assert part.function_call.name == "sts2_play_card"
    # Ensure thought_signature is not present when None
    assert getattr(part, "thought_signature", None) is None


def test_gemini_provider_function_declarations_empty_required():
    provider = GeminiProvider()
    tools_schema = [
        {
            "name": "sts2_end_turn",
            "description": "End turn",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    ]
    decls = provider._build_function_declarations(tools_schema)
    assert len(decls) == 1
    decl = decls[0]
    assert decl.name == "sts2_end_turn"
    # Ensure required is empty or not set as a populated list
    assert not getattr(decl.parameters, "required", None)


def test_cognitive_engine_index_shifting_reorder():
    pending_calls = [
        {"name": "play_card", "args": {"card_index": 0}},
        {"name": "play_card", "args": {"card_index": 3}},
        {"name": "sts2_play_card", "args": {"card_index": 1}},
        {"name": "sts2_play_card", "args": {"card_index": 2}},
    ]
    reordered_play = CognitiveEngine._reorder_index_shifting_calls(pending_calls[:2])
    assert [c["args"]["card_index"] for c in reordered_play] == [3, 0]

    reordered_sts2 = CognitiveEngine._reorder_index_shifting_calls(pending_calls[2:])
    assert [c["args"]["card_index"] for c in reordered_sts2] == [2, 1]


def test_cognitive_engine_terminating_tools_set():
    assert "sts2_end_turn" in CognitiveEngine.STS2_TURN_TERMINATING_TOOLS
    assert "end_turn" in CognitiveEngine.STS2_TURN_TERMINATING_TOOLS
    assert "sts2_choose_map_node" in CognitiveEngine.STS2_TURN_TERMINATING_TOOLS
    assert "choose_map_node" in CognitiveEngine.STS2_TURN_TERMINATING_TOOLS


@pytest.mark.asyncio
async def test_gemini_provider_function_calling():
    provider = GeminiProvider()
    if not provider.client:
        pytest.skip("Gemini API key not configured or offline")

    registry = ToolRegistry()
    tools_schema = registry.get_all_schemas()

    messages = [{"role": "user", "content": "发张自拍照给主人看喵"}]
    system_prompt = "你是 Miao，一个猫娘。当主人要看自拍时，必须调用 generate_image 函数。"

    res = await provider.generate(
        messages=messages,
        tools_schema=tools_schema,
        system_prompt=system_prompt
    )

    assert "tool_calls" in res
    assert len(res["tool_calls"]) > 0
    assert res["tool_calls"][0]["name"] == "generate_image"


if __name__ == "__main__":
    test_gemini_provider_messages_to_contents_thought_signature()
    test_gemini_provider_function_declarations_empty_required()
    test_cognitive_engine_index_shifting_reorder()
    test_cognitive_engine_terminating_tools_set()
    print("Gemini Provider & Cognitive Engine unit tests passed!")
