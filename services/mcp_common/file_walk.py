"""
Shared bounded/pruned file walker for presenter MCP servers that scan a
confined root (services/mcp_vscode's vscode_search / vscode_find_files).

Path.rglob()/Path.glob() cannot prune directories -- they always descend into
every subdirectory before filtering results. Pointed at a real repository
root that includes node_modules, .git, or a venv, that turns a "search for
this identifier" tool call into a multi-minute, IO-heavy full-tree scan (this
is exactly what happened: a vscode_search call took over two minutes and is
the leading suspect for a VS Code dev container becoming unresponsive and
dropping its remote connection during the same session). os.walk(), unlike
glob, lets us drop excluded directory names from `dirnames` in place so the
walk never enters them at all.
"""
import os
import time
from pathlib import Path
from typing import Iterator, Optional, Set

DEFAULT_EXCLUDED_DIRS: Set[str] = {
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", ".turbo", ".cache",
    "logs", "temp",
}


def iter_files(
    root: Path,
    excluded_dirs: Optional[Set[str]] = None,
    max_files: int = 5000,
    max_file_bytes: int = 1_000_000,
    time_budget_seconds: float = 5.0,
) -> Iterator[Path]:
    """
    Yields file paths under `root`, pruning excluded/hidden directories and
    bounded by file count, per-file size, and wall-clock time -- an unexpected
    or huge tree degrades (stops early) instead of hanging the MCP server
    process or the caller waiting on it.
    """
    excluded = excluded_dirs if excluded_dirs is not None else DEFAULT_EXCLUDED_DIRS
    start = time.monotonic()
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        if time.monotonic() - start > time_budget_seconds:
            return

        dirnames[:] = [d for d in dirnames if d not in excluded and not d.startswith(".")]

        for name in filenames:
            if scanned >= max_files:
                return
            scanned += 1

            path = Path(dirpath) / name
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue

            yield path
