import pytest
import asyncio
from services.cognitive.tool_registry import ToolRegistry
from services.cognitive.tools.image_gen_tool import ImageGenTool
from services.cognitive.tools.tts_tool import TTSTool
from services.cognitive.tools.telegram_action_tool import TelegramActionTool

def test_tool_registry_registration():
    registry = ToolRegistry()
    schemas = registry.get_all_schemas()
    
    names = [s["name"] for s in schemas]
    assert "generate_image" in names
    assert "generate_tts_speech" in names
    assert "telegram_action" in names

@pytest.mark.asyncio
async def test_image_gen_tool_execution():
    tool = ImageGenTool()
    assert tool.name == "generate_image"
    res = await tool.execute(prompt="cute catgirl selfie")
    assert "photo_path" in res or "prompt" in res

@pytest.mark.asyncio
async def test_tts_tool_execution():
    tool = TTSTool()
    assert tool.name == "generate_tts_speech"
    res = await tool.execute(text="喵呜~ 主人好")
    assert "text" in res

if __name__ == "__main__":
    asyncio.run(test_image_gen_tool_execution())
    asyncio.run(test_tts_tool_execution())
    print("All tool unit tests passed!")
