"""
MCP server exposing read/navigate-only PowerPoint control for slide-by-slide
narration, deliberately scoped to a single decks directory and with no
edit/add/delete-slide capability (see docs/ARCHITECTURE.md discussion on MCP
presenter tools).

Windows-only: drives a live PowerPoint instance via COM (pywin32). On any
other platform, or if PowerPoint/pywin32 isn't available, tools return a
clear error dict instead of crashing.

Run standalone: python -m services.mcp_ppt.server --root <decks_dir>
(--root is normally appended by PresenterSessionManager.activate() at spawn
time; without it ppt_open fails closed instead of accepting an arbitrary
model-supplied path.)
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from services.mcp_common.confinement import error as _error, resolve_within_root

logger = logging.getLogger("mcp_ppt")

mcp = FastMCP("betteragent-ppt")

# Populated by main() from --root; kept module-level because FastMCP tool
# functions are registered as plain functions, not methods.
_decks_root: Optional[Path] = None

# Lazily-created COM handles -- only touched inside ppt_open/ppt_close/etc,
# never at import time, so this module still imports cleanly on non-Windows
# hosts for testing.
_app = None
_presentation = None
_slideshow_window = None


def _resolve_within_root(rel_or_abs_path: str) -> Optional[Path]:
    return resolve_within_root(_decks_root, rel_or_abs_path)


def _get_com_client():
    if sys.platform != "win32":
        raise RuntimeError("mcp_ppt requires Windows (PowerPoint COM automation)")
    try:
        import win32com.client
    except ImportError as e:
        raise RuntimeError("pywin32 is not installed (pip install pywin32)") from e
    return win32com.client


def _slide_text(slide) -> Dict[str, Any]:
    title = ""
    body_parts = []
    try:
        if slide.Shapes.HasTitle:
            title = slide.Shapes.Title.TextFrame.TextRange.Text
    except Exception:
        pass

    for shape in slide.Shapes:
        try:
            if not shape.HasTextFrame or not shape.TextFrame.HasText:
                continue
            text = shape.TextFrame.TextRange.Text
            if text and text != title:
                body_parts.append(text)
        except Exception:
            continue

    notes = None
    try:
        notes_shapes = slide.NotesPage.Shapes
        for shape in notes_shapes:
            if shape.Type == 2 and shape.HasTextFrame and shape.TextFrame.HasText:  # msoPlaceholder body
                candidate = shape.TextFrame.TextRange.Text.strip()
                if candidate:
                    notes = candidate
                break
    except Exception:
        pass

    return {"title": title, "body": "\n".join(body_parts), "notes": notes}


@mcp.tool(structured_output=False)
def ppt_open(path: str) -> Dict[str, Any]:
    """Opens a PowerPoint deck from the active decks directory and starts the slide show."""
    global _app, _presentation, _slideshow_window

    if _decks_root is None:
        return _error("no decks root configured; call presenter_mode(activate, ppt, root_path=...) first")

    resolved = _resolve_within_root(path)
    if resolved is None or not resolved.is_file():
        return _error(f"path outside decks root or not a file: {path}")

    try:
        client = _get_com_client()
        _app = client.Dispatch("PowerPoint.Application")
        _presentation = _app.Presentations.Open(str(resolved), WithWindow=True)
        _slideshow_window = _presentation.SlideShowSettings.Run()
    except Exception as e:
        return _error(f"failed to open presentation: {e}")

    return {"status": "ok", "path": str(resolved.relative_to(_decks_root.resolve())), "slide_count": _presentation.Slides.Count}


@mcp.tool(structured_output=False)
def ppt_get_deck_outline() -> Dict[str, Any]:
    """Returns every slide's title and the total slide count for the currently open deck."""
    if _presentation is None:
        return _error("no deck is open; call ppt_open first")

    try:
        titles = []
        for i, slide in enumerate(_presentation.Slides, start=1):
            info = _slide_text(slide)
            titles.append({"slide": i, "title": info["title"]})
        return {"slide_count": _presentation.Slides.Count, "slides": titles}
    except Exception as e:
        return _error(f"failed to read deck outline: {e}")


@mcp.tool(structured_output=False)
def ppt_get_slide_text(n: int) -> Dict[str, Any]:
    """Returns slide n's title, body text, and speaker notes. `notes` is null if the slide has none -- narrate from the notes when present, improvise from title/body otherwise."""
    if _presentation is None:
        return _error("no deck is open; call ppt_open first")

    if n < 1 or n > _presentation.Slides.Count:
        return _error(f"slide {n} out of range (1..{_presentation.Slides.Count})")

    try:
        slide = _presentation.Slides(n)
        info = _slide_text(slide)
        return {"slide": n, **info}
    except Exception as e:
        return _error(f"failed to read slide {n}: {e}")


@mcp.tool(structured_output=False)
def ppt_get_current_slide() -> Dict[str, Any]:
    """Returns the slide number currently showing."""
    if _slideshow_window is None:
        return _error("slide show is not running; call ppt_open first")
    try:
        return {"slide": _slideshow_window.View.CurrentShowPosition, "slide_count": _presentation.Slides.Count}
    except Exception as e:
        return _error(f"failed to read current slide: {e}")


@mcp.tool(structured_output=False)
def ppt_goto_slide(n: int) -> Dict[str, Any]:
    """Jumps the slide show to slide n."""
    if _slideshow_window is None:
        return _error("slide show is not running; call ppt_open first")
    if n < 1 or n > _presentation.Slides.Count:
        return _error(f"slide {n} out of range (1..{_presentation.Slides.Count})")
    try:
        _slideshow_window.View.GotoSlide(n)
        return {"status": "ok", "slide": n}
    except Exception as e:
        return _error(f"failed to go to slide {n}: {e}")


@mcp.tool(structured_output=False)
def ppt_next_slide() -> Dict[str, Any]:
    """Advances the slide show by one slide."""
    if _slideshow_window is None:
        return _error("slide show is not running; call ppt_open first")
    try:
        _slideshow_window.View.Next()
        return {"status": "ok", "slide": _slideshow_window.View.CurrentShowPosition}
    except Exception as e:
        return _error(f"failed to advance slide: {e}")


@mcp.tool(structured_output=False)
def ppt_prev_slide() -> Dict[str, Any]:
    """Moves the slide show back one slide."""
    if _slideshow_window is None:
        return _error("slide show is not running; call ppt_open first")
    try:
        _slideshow_window.View.Previous()
        return {"status": "ok", "slide": _slideshow_window.View.CurrentShowPosition}
    except Exception as e:
        return _error(f"failed to go back a slide: {e}")


@mcp.tool(structured_output=False)
def ppt_close() -> Dict[str, Any]:
    """Closes the current presentation."""
    global _app, _presentation, _slideshow_window

    if _presentation is None:
        return {"status": "ok", "message": "nothing was open"}

    try:
        _presentation.Close()
    except Exception as e:
        return _error(f"failed to close presentation: {e}")
    finally:
        _presentation = None
        _slideshow_window = None
        _app = None

    return {"status": "ok"}


def main() -> None:
    global _decks_root

    parser = argparse.ArgumentParser(description="BetterAgent PowerPoint presenter MCP server")
    parser.add_argument("--root", type=str, default=None, help="Decks directory this session is confined to")
    args = parser.parse_args()

    if args.root:
        _decks_root = Path(args.root)
        if not _decks_root.is_dir():
            logger.warning(f"--root {args.root} is not a directory; ppt_open will fail closed until a valid root is set")

    mcp.run()


if __name__ == "__main__":
    main()
