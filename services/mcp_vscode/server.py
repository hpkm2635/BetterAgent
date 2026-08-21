"""
MCP server exposing a Claude-Code-style read/navigate/highlight tool set for
a code walkthrough, deliberately scoped to a single workspace directory and
with no edit/write/terminal capability -- this is a presentation aid, not a
coding agent (see docs/ARCHITECTURE.md discussion on MCP presenter tools).

Run standalone: python -m services.mcp_vscode.server --root <workspace_dir>
(--root is normally appended by PresenterSessionManager.activate() at spawn
time; without it every file-facing tool fails closed instead of accepting an
arbitrary model-supplied path.)
"""
import argparse
import fnmatch
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from services.mcp_common.confinement import error as _error, resolve_within_root
from services.mcp_common.file_walk import iter_files

logger = logging.getLogger("mcp_vscode")

MAX_READ_LINES = 300
MAX_SEARCH_RESULTS = 50
MAX_FIND_RESULTS = 200
CODE_CLI_CANDIDATES = ("code", "code-insiders", "cursor")

# Debounce/throttle floor for anything that spawns an external process
# (the `code` CLI) or pokes the highlighter extension: a model that calls
# vscode_open_file/vscode_highlight_range repeatedly in a tight tool-call
# loop used to spawn a new `code` CLI process (or FS-watcher wakeup) on every
# single call with no floor at all -- a real contributor to a VS Code window
# becoming unresponsive during a multi-round presenter session.
_MIN_SIGNAL_INTERVAL_SECONDS = 0.3

mcp = FastMCP("betteragent-vscode")

# Populated by main() from --root / --signal-path; kept module-level because
# FastMCP tool functions are registered as plain functions, not methods.
_workspace_root: Optional[Path] = None
_signal_path: Optional[Path] = None

_last_signal_payload: Optional[Dict[str, Any]] = None
_last_signal_time: float = 0.0
_last_open_target: Optional[str] = None
_last_open_time: float = 0.0


def _throttle_since(last_time: float) -> None:
    elapsed = time.monotonic() - last_time
    if elapsed < _MIN_SIGNAL_INTERVAL_SECONDS:
        time.sleep(_MIN_SIGNAL_INTERVAL_SECONDS - elapsed)


def _resolve_within_root(rel_or_abs_path: str) -> Optional[Path]:
    p_obj = Path(rel_or_abs_path)
    if p_obj.is_file():
        return p_obj.resolve()

    ws_root = _workspace_root or Path.cwd()
    res = resolve_within_root(ws_root, rel_or_abs_path)
    if res and res.is_file():
        return res

    # Fallback to checking relative to ws_root or current working directory
    rel_target = ws_root / rel_or_abs_path
    if rel_target.is_file():
        return rel_target.resolve()

    return None


@mcp.tool(structured_output=False)
def vscode_read_range(path: str, start_line: int, end_line: int) -> Dict[str, Any]:
    """Reads a line range from a file inside the active workspace (like Claude Code's Read tool). 1-indexed, inclusive. Capped to 300 lines per call."""
    if _workspace_root is None:
        return _error("no workspace root configured; call presenter_mode(activate, vscode, root_path=...) first")

    resolved = _resolve_within_root(path)
    if resolved is None or not resolved.is_file():
        return _error(f"path outside workspace root or not a file: {path}")

    if start_line < 1:
        start_line = 1
    if end_line < start_line:
        end_line = start_line
    if end_line - start_line + 1 > MAX_READ_LINES:
        end_line = start_line + MAX_READ_LINES - 1

    try:
        all_lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return _error(f"failed to read file: {e}")

    slice_lines = all_lines[start_line - 1:end_line]
    return {
        "path": resolved.relative_to(_workspace_root.resolve()).as_posix() if (_workspace_root and resolved.is_relative_to(_workspace_root.resolve())) else resolved.as_posix(),
        "start_line": start_line,
        "end_line": min(end_line, len(all_lines)),
        "total_lines": len(all_lines),
        "lines": slice_lines,
    }


@mcp.tool(structured_output=False)
def vscode_search(query: str, path: str = "") -> Dict[str, Any]:
    """Regex-searches file contents under the workspace root (like Claude Code's Grep tool), optionally scoped to a subdirectory. Capped to 50 matches."""
    if _workspace_root is None:
        return _error("no workspace root configured; call presenter_mode(activate, vscode, root_path=...) first")

    base = _resolve_within_root(path) if path else _workspace_root.resolve()
    if base is None or not base.is_dir():
        return _error(f"path outside workspace root or not a directory: {path}")

    try:
        pattern = re.compile(query)
    except re.error as e:
        return _error(f"invalid regex: {e}")

    matches: List[Dict[str, Any]] = []
    truncated_by_scan_limit = False
    scanned_any = False
    for file_path in iter_files(base):
        scanned_any = True
        if len(matches) >= MAX_SEARCH_RESULTS:
            truncated_by_scan_limit = True
            break
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append({
                    "path": file_path.relative_to(_workspace_root.resolve()).as_posix(),
                    "line": line_no,
                    "text": line.strip()[:300],
                })
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break

    return {
        "query": query,
        "matches": matches,
        "truncated": truncated_by_scan_limit or len(matches) >= MAX_SEARCH_RESULTS,
        "note": None if scanned_any else "no files found under this path (or everything was excluded/too large)",
    }


@mcp.tool(structured_output=False)
def vscode_find_files(pattern: str) -> Dict[str, Any]:
    """Finds files under the workspace root by glob pattern (like Claude Code's Glob tool, e.g. '**/*.py'). Capped to 200 results."""
    if _workspace_root is None:
        return _error("no workspace root configured; call presenter_mode(activate, vscode, root_path=...) first")

    root_resolved = _workspace_root.resolve()
    found: List[str] = []
    truncated = False
    for file_path in iter_files(root_resolved, max_files=20000):
        rel = file_path.relative_to(root_resolved).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(file_path.name, pattern):
            found.append(rel)
            if len(found) >= MAX_FIND_RESULTS:
                truncated = True
                break

    return {"pattern": pattern, "files": found, "truncated": truncated}


def _detect_code_cli() -> Optional[str]:
    for cli in CODE_CLI_CANDIDATES:
        try:
            probe = subprocess.run(["which" if _is_posix() else "where", cli], capture_output=True, timeout=5)
            if probe.returncode == 0 and probe.stdout.strip():
                return cli
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _is_posix() -> bool:
    import os
    return os.name == "posix"


@mcp.tool(structured_output=False)
def vscode_open_file(path: str, line: Optional[int] = None) -> Dict[str, Any]:
    """Brings a file in the active workspace to the foreground in the visible VSCode window, optionally jumping to a line. Uses the `code` CLI, not UI automation."""
    global _last_open_target, _last_open_time

    if _workspace_root is None:
        return _error("no workspace root configured; call presenter_mode(activate, vscode, root_path=...) first")

    resolved = _resolve_within_root(path)
    if resolved is None or not resolved.is_file():
        return _error(f"path outside workspace root or not a file: {path}")

    cli = _detect_code_cli()
    if cli is None:
        return _error("no VSCode CLI found on PATH (tried: code, code-insiders, cursor)")

    target = str(resolved) + (f":{line}" if line else "")
    rel_path = str(resolved.relative_to(_workspace_root.resolve()))

    # Dedupe: an identical open request that just fired is a no-op -- skip
    # spawning another `code` CLI process for it.
    if target == _last_open_target and (time.monotonic() - _last_open_time) < _MIN_SIGNAL_INTERVAL_SECONDS:
        return {"status": "skipped", "reason": "duplicate vscode_open_file call within debounce window", "path": rel_path, "line": line}

    # Throttle: even a genuinely different target is paced to at most one
    # CLI spawn per debounce window, so a burst of calls across several
    # files can't fire them all back-to-back.
    _throttle_since(_last_open_time)

    try:
        subprocess.run([cli, "--reuse-window", "--goto", target], timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return _error(f"failed to invoke {cli}: {e}")

    _last_open_target = target
    _last_open_time = time.monotonic()
    return {"status": "ok", "path": rel_path, "line": line}


def _write_signal(payload: Dict[str, Any]) -> Dict[str, Any]:
    global _last_signal_payload, _last_signal_time

    if _signal_path is None:
        return _error("no highlight signal path configured")

    if payload == _last_signal_payload and (time.monotonic() - _last_signal_time) < _MIN_SIGNAL_INTERVAL_SECONDS:
        return {"status": "skipped", "reason": "duplicate highlight signal within debounce window"}

    _throttle_since(_last_signal_time)

    try:
        _signal_path.parent.mkdir(parents=True, exist_ok=True)
        _signal_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        return _error(f"failed to write highlight signal: {e}")

    _last_signal_payload = payload
    _last_signal_time = time.monotonic()
    return {"status": "ok"}


@mcp.tool(structured_output=False)
def vscode_highlight_range(path: str, start_line: int, end_line: int, label: str = "") -> Dict[str, Any]:
    """Highlights a line range in the given file in the visible VSCode window via the companion betteragent-highlighter extension (frontend/integrations/vscode/betteragent-highlighter)."""
    if _workspace_root is None:
        return _error("no workspace root configured; call presenter_mode(activate, vscode, root_path=...) first")

    resolved = _resolve_within_root(path)
    if resolved is None or not resolved.is_file():
        return _error(f"path outside workspace root or not a file: {path}")

    return _write_signal({
        "action": "highlight",
        "path": str(resolved),
        "start_line": start_line,
        "end_line": end_line,
        "label": label,
    })


@mcp.tool(structured_output=False)
def vscode_clear_highlight() -> Dict[str, Any]:
    """Clears any active highlight decoration in VSCode."""
    return _write_signal({"action": "clear"})


def main() -> None:
    global _workspace_root, _signal_path

    parser = argparse.ArgumentParser(description="BetterAgent VSCode presenter MCP server")
    parser.add_argument("--root", type=str, default=None, help="Workspace directory this session is confined to")
    parser.add_argument("--signal-path", type=str, default="temp/vscode_highlight_signal.json",
                         help="File the companion VSCode extension watches for highlight commands")
    args = parser.parse_args()

    if args.root:
        _workspace_root = Path(args.root)
        if not _workspace_root.is_dir():
            logger.warning(f"--root {args.root} is not a valid directory; falling back to current working directory: {Path.cwd()}")
            _workspace_root = Path.cwd()
    else:
        _workspace_root = Path.cwd()

    _signal_path = Path(args.signal_path)

    mcp.run()


if __name__ == "__main__":
    main()
