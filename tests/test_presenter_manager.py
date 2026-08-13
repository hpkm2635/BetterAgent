import sys

import pytest

from services.cognitive.mcp.presenter_manager import PresenterSessionManager


def _make_manager(idle_timeout_seconds: float = 600.0) -> PresenterSessionManager:
    return PresenterSessionManager(
        server_commands={
            "vscode": [sys.executable, "-m", "services.mcp_vscode.server"],
        },
        idle_timeout_seconds=idle_timeout_seconds,
    )


@pytest.fixture
def workspace(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


@pytest.mark.asyncio
async def test_activate_exposes_real_tool_schemas(workspace):
    manager = _make_manager()
    chat_id = 1

    message = await manager.activate(chat_id, "vscode", root_path=str(workspace))
    assert "已激活" in message

    names = [s["name"] for s in manager.get_active_tool_schemas(chat_id)]
    assert "vscode_read_range" in names
    assert "vscode_find_files" in names

    await manager.deactivate(chat_id)


@pytest.mark.asyncio
async def test_activate_is_idempotent(workspace):
    manager = _make_manager()
    chat_id = 2

    first = await manager.activate(chat_id, "vscode", root_path=str(workspace))
    second = await manager.activate(chat_id, "vscode")
    assert "已激活" in first
    assert "已经是激活状态" in second

    await manager.deactivate(chat_id)


@pytest.mark.asyncio
async def test_call_tool_routes_to_the_right_session_and_confines_paths(workspace):
    manager = _make_manager()
    chat_id = 3
    await manager.activate(chat_id, "vscode", root_path=str(workspace))

    found = await manager.call_tool(chat_id, "vscode_find_files", {"pattern": "**/*.py"})
    assert found["files"] == ["src/hello.py"]

    escape_attempt = await manager.call_tool(chat_id, "vscode_read_range", {
        "path": "/etc/passwd", "start_line": 1, "end_line": 5,
    })
    assert escape_attempt["error"] is True

    await manager.deactivate(chat_id)


@pytest.mark.asyncio
async def test_call_tool_for_unknown_tool_returns_none(workspace):
    manager = _make_manager()
    chat_id = 4
    await manager.activate(chat_id, "vscode", root_path=str(workspace))

    assert await manager.call_tool(chat_id, "does_not_exist", {}) is None

    await manager.deactivate(chat_id)


@pytest.mark.asyncio
async def test_unconfigured_target_fails_without_spawning(workspace):
    manager = _make_manager()
    message = await manager.activate(99, "ppt")  # no "ppt" in server_commands
    assert "未知或未配置" in message
    assert manager.get_active_tool_schemas(99) == []


@pytest.mark.asyncio
async def test_sweep_idle_deactivates_stale_sessions(workspace):
    manager = _make_manager(idle_timeout_seconds=0.05)
    chat_id = 5
    await manager.activate(chat_id, "vscode", root_path=str(workspace))
    assert manager.get_active_tool_schemas(chat_id)

    import asyncio
    await asyncio.sleep(0.2)
    await manager.sweep_idle()

    assert manager.get_active_tool_schemas(chat_id) == []


@pytest.mark.asyncio
async def test_deactivate_with_no_active_sessions_is_safe():
    manager = _make_manager()
    message = await manager.deactivate(12345)
    assert "没有正在运行" in message
