"""
Shared path-confinement helper for presenter MCP servers (mcp_ppt, mcp_vscode).

Both servers let the LLM pass a path argument to a file-facing tool
(vscode_read_range, ppt_open, ...). Without server-side confinement that's an
arbitrary-file-read/open primitive on the presenter's machine -- the same
class of problem this repo already guards against for sticker_id/media paths
(see docs/SECURITY.md, services/cognitive/tools/validation.py). A session's
root is set once at MCP server spawn time (--root, appended by
PresenterSessionManager.activate()), not by the LLM.
"""
from pathlib import Path
from typing import Any, Dict, Optional


def error(detail: str) -> Dict[str, Any]:
    return {"error": True, "detail": detail}


def resolve_within_root(root: Optional[Path], rel_or_abs_path: str) -> Optional[Path]:
    """Resolves a model-supplied path against a confinement root.
    Returns None if no root is configured, or if the resolved path escapes it."""
    if root is None:
        return None

    candidate = (root / rel_or_abs_path) if not Path(rel_or_abs_path).is_absolute() else Path(rel_or_abs_path)
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except OSError:
        return None

    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    return resolved
