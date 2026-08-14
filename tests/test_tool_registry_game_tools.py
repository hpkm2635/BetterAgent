from services.cognitive.tool_registry import ToolRegistry
from services.cognitive.tools.sts2_action_tool import STS2_ACTION_SPECS


def test_get_all_schemas_excludes_game_tools():
    registry = ToolRegistry()
    names = {s["name"] for s in registry.get_all_schemas()}
    assert not any(name.startswith("sts2_") for name in names)
    # Sanity: the ordinary chat tools are still there.
    assert "generate_tts_speech" in names
    assert "generate_image" in names
    assert "telegram_action" in names


def test_get_game_schemas_includes_exactly_the_game_tools():
    registry = ToolRegistry()
    names = {s["name"] for s in registry.get_game_schemas()}
    expected = {spec["name"] for spec in STS2_ACTION_SPECS} | {"sts2_get_game_state"}
    assert names == expected


def test_get_tool_resolves_game_tools_by_name():
    registry = ToolRegistry()
    tool = registry.get_tool("sts2_end_turn")
    assert tool is not None
    assert tool.name == "sts2_end_turn"
