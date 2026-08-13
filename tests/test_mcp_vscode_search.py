import sys

import pytest

from services.cognitive.mcp.client import McpSession


@pytest.fixture
def repo_like_workspace(tmp_path):
    # Mimics a real project root: a real source file, plus the kind of huge
    # vendored/generated directories a workspace root realistically contains
    # (node_modules, .git, .venv). vscode_search/vscode_find_files used to
    # use Path.rglob(), which cannot prune directories -- pointed at a real
    # repo root this walked (and read the full contents of) every file in
    # trees like these, which is what turned one vscode_search call into a
    # 2+ minute, IO-heavy scan in production.
    src = tmp_path / "src"
    src.mkdir()
    (src / "tool_registry.py").write_text("def PresenterControlTool(): pass\n")

    vendored = tmp_path / "node_modules" / "somepkg"
    vendored.mkdir(parents=True)
    for i in range(50):
        (vendored / f"file_{i}.js").write_text("PresenterControlTool " * 5)

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG").write_text("PresenterControlTool")

    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "site.py").write_text("PresenterControlTool")

    return tmp_path


async def _session(root):
    session = McpSession([sys.executable, "-m", "services.mcp_vscode.server", "--root", str(root)])
    await session.start()
    return session


@pytest.mark.asyncio
async def test_search_excludes_vendored_and_vcs_directories(repo_like_workspace):
    session = await _session(repo_like_workspace)
    try:
        result = await session.call_tool("vscode_search", {"query": "PresenterControlTool"})
        assert result["matches"] == [{
            "path": "src/tool_registry.py",
            "line": 1,
            "text": "def PresenterControlTool(): pass",
        }]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_find_files_excludes_vendored_and_vcs_directories(repo_like_workspace):
    session = await _session(repo_like_workspace)
    try:
        result = await session.call_tool("vscode_find_files", {"pattern": "**/*.py"})
        assert result["files"] == ["src/tool_registry.py"]
    finally:
        await session.close()
