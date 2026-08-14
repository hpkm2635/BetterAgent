import pytest

from services.cognitive.tools.sts2_action_tool import (
    STS2_ACTION_SPECS,
    Sts2ActionTool,
    Sts2GetStateTool,
    build_sts2_tools,
    _is_valid_entity_id,
)


class FakeHttpClient:
    def __init__(self):
        self.post_calls = []
        self.get_calls = 0

    async def post_action(self, action, args):
        self.post_calls.append((action, dict(args)))
        return {"status": "ok", "action": action, "args": args}

    async def get_state(self):
        self.get_calls += 1
        return {"status": "ok", "state_type": "map"}


def test_all_specs_have_required_shape():
    names = set()
    for spec in STS2_ACTION_SPECS:
        assert spec["action"]
        assert spec["name"].startswith("sts2_")
        assert spec["name"] not in names, f"duplicate tool name {spec['name']}"
        names.add(spec["name"])
        assert isinstance(spec["properties"], dict)
        assert isinstance(spec["required"], list)
        for req in spec["required"]:
            assert req in spec["properties"]


def test_menu_select_and_deferred_actions_excluded():
    action_names = {spec["action"] for spec in STS2_ACTION_SPECS}
    assert "menu_select" not in action_names
    assert "select_bundle" not in action_names
    assert "crystal_sphere_set_tool" not in action_names


def test_build_sts2_tools_count_and_names():
    tools = build_sts2_tools(FakeHttpClient())
    # 21 action specs + 1 get_state tool.
    assert len(STS2_ACTION_SPECS) == 21
    assert len(tools) == 22
    assert any(t.name == "sts2_get_game_state" for t in tools)
    assert any(isinstance(t, Sts2GetStateTool) for t in tools)
    assert all(isinstance(t, (Sts2ActionTool, Sts2GetStateTool)) for t in tools)


@pytest.mark.asyncio
async def test_play_card_execute_maps_args_to_post_action():
    client = FakeHttpClient()
    spec = next(s for s in STS2_ACTION_SPECS if s["action"] == "play_card")
    tool = Sts2ActionTool(spec, client)

    result = await tool.execute(card_index=2, target="KIN_PRIEST_0")

    assert client.post_calls == [("play_card", {"card_index": 2, "target": "KIN_PRIEST_0"})]
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_execute_drops_malformed_target_instead_of_rejecting():
    client = FakeHttpClient()
    spec = next(s for s in STS2_ACTION_SPECS if s["action"] == "play_card")
    tool = Sts2ActionTool(spec, client)

    await tool.execute(card_index=0, target="../../etc/passwd")

    action, args = client.post_calls[0]
    assert args["target"] is None  # dropped, not rejected -- call still goes through


@pytest.mark.asyncio
async def test_get_state_tool_calls_client():
    client = FakeHttpClient()
    tool = Sts2GetStateTool(client)
    result = await tool.execute()
    assert client.get_calls == 1
    assert result["status"] == "ok"


def test_is_valid_entity_id():
    assert _is_valid_entity_id(None) is True
    assert _is_valid_entity_id("KIN_PRIEST_0") is True
    assert _is_valid_entity_id("JAW_WORM_0") is True
    assert _is_valid_entity_id("") is False
    assert _is_valid_entity_id("../../etc/passwd") is False
    assert _is_valid_entity_id("kin_priest_0") is False  # lowercase not allowed
    assert _is_valid_entity_id("a" * 65) is False  # too long


def test_tool_schema_is_valid_json_schema_object():
    client = FakeHttpClient()
    for spec in STS2_ACTION_SPECS:
        tool = Sts2ActionTool(spec, client)
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert schema["properties"] == spec["properties"]
        assert schema["required"] == spec["required"]
        assert tool.name == spec["name"]
        assert tool.description == spec["description"]
