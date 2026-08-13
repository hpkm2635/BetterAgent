import json
import sys
import time

import pytest

from services.cognitive.mcp.client import McpSession


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "hello.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


async def _session(root, signal_path):
    session = McpSession([
        sys.executable, "-m", "services.mcp_vscode.server",
        "--root", str(root),
        "--signal-path", str(signal_path),
    ])
    await session.start()
    return session


@pytest.mark.asyncio
async def test_identical_highlight_calls_are_deduped(workspace, tmp_path):
    # Regression test: a model retrying/re-confirming the same highlight in a
    # tight tool-call loop used to poke the signal file (and the extension's
    # FS watcher) on every single call with no floor at all -- a real
    # contributor to VS Code becoming unresponsive during a presenter session.
    signal_path = tmp_path / "signal.json"
    session = await _session(workspace, signal_path)
    try:
        args = {"path": "hello.py", "start_line": 1, "end_line": 2}
        first = await session.call_tool("vscode_highlight_range", args)
        assert first["status"] == "ok"
        written_after_first = json.loads(signal_path.read_text())

        second = await session.call_tool("vscode_highlight_range", args)
        assert second["status"] == "skipped"

        # The signal file itself must not have been touched by the skipped call.
        assert json.loads(signal_path.read_text()) == written_after_first
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_different_highlight_right_after_is_throttled_not_dropped(workspace, tmp_path):
    signal_path = tmp_path / "signal.json"
    session = await _session(workspace, signal_path)
    try:
        await session.call_tool("vscode_highlight_range", {"path": "hello.py", "start_line": 1, "end_line": 1})

        start = time.monotonic()
        result = await session.call_tool("vscode_highlight_range", {"path": "hello.py", "start_line": 2, "end_line": 2})
        elapsed = time.monotonic() - start

        # A genuinely different request still goes through -- it's paced, not dropped.
        assert result["status"] == "ok"
        assert elapsed >= 0.25  # ~0.3s debounce floor, small margin for scheduling jitter
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_clear_after_highlight_is_not_treated_as_a_duplicate(workspace, tmp_path):
    signal_path = tmp_path / "signal.json"
    session = await _session(workspace, signal_path)
    try:
        await session.call_tool("vscode_highlight_range", {"path": "hello.py", "start_line": 1, "end_line": 1})
        # A wait avoids also asserting the throttle-pacing behavior here.
        await session.call_tool("vscode_clear_highlight", {})
        cleared = json.loads(signal_path.read_text())
        assert cleared == {"action": "clear"}
    finally:
        await session.close()
