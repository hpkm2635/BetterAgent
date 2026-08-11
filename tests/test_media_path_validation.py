import pytest
import asyncio
from services.cognitive.tools.validation import is_safe_media_filename
from services.cognitive.tools.telegram_action_tool import TelegramActionTool


def test_is_safe_media_filename_accepts_plain_filenames():
    assert is_safe_media_filename("sticker_123.webp")
    assert is_safe_media_filename("photo_45678.jpg")


def test_is_safe_media_filename_rejects_path_traversal():
    assert not is_safe_media_filename("../../.env")
    assert not is_safe_media_filename("..")
    assert not is_safe_media_filename(".")


def test_is_safe_media_filename_rejects_path_separators():
    assert not is_safe_media_filename("temp/../gotd.session.json")
    assert not is_safe_media_filename("/etc/passwd")
    assert not is_safe_media_filename("sub/dir/file.webp")
    assert not is_safe_media_filename("C:\\Windows\\win.ini")


def test_is_safe_media_filename_rejects_empty_or_none():
    assert not is_safe_media_filename("")
    assert not is_safe_media_filename(None)


@pytest.mark.asyncio
async def test_telegram_action_tool_strips_malicious_sticker_id():
    """
    Simulates a prompt-injection scenario: the model calls telegram_action
    with a sticker_id crafted to reference a sensitive file. The tool must
    never let this value through -- see docs/SECURITY.md.
    """
    tool = TelegramActionTool()
    res = await tool.execute(action_type="send_sticker", sticker_id="../../gotd.session.json")
    assert res["sticker_id"] is None


@pytest.mark.asyncio
async def test_telegram_action_tool_allows_safe_sticker_id():
    tool = TelegramActionTool()
    res = await tool.execute(action_type="send_sticker", sticker_id="sticker_happy.webp")
    assert res["sticker_id"] == "sticker_happy.webp"


if __name__ == "__main__":
    asyncio.run(test_telegram_action_tool_strips_malicious_sticker_id())
    asyncio.run(test_telegram_action_tool_allows_safe_sticker_id())
    print("All media path validation tests passed!")
