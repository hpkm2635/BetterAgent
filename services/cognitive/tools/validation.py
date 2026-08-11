import re
from typing import Optional

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_safe_media_filename(name: Optional[str]) -> bool:
    """
    True only if `name` is a bare filename with no path components.

    sticker_id (and any similar LLM-influenced field) is ultimately used by
    the Go core to open a local file by name inside a managed temp directory
    -- it must never be allowed to smuggle a path ("..", "/", absolute
    paths). This mirrors the Go-side MediaManager.ResolveMediaPath()
    basename+existence check; both layers enforce independently since either
    one could regress on its own. See docs/SECURITY.md.
    """
    if not name:
        return False
    if name in (".", ".."):
        return False
    return bool(_SAFE_FILENAME_RE.match(name))
