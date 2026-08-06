import pytest
import asyncio
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.tool_registry import ToolRegistry

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

    print("Gemini response res:", res)
    assert "tool_calls" in res
    assert len(res["tool_calls"]) > 0
    assert res["tool_calls"][0]["name"] == "generate_image"

if __name__ == "__main__":
    asyncio.run(test_gemini_provider_function_calling())
    print("Gemini Function Calling unit test passed!")
