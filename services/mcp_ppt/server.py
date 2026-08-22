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


def _reset_com_state() -> None:
    global _app, _presentation, _slideshow_window
    _app = None
    _presentation = None
    _slideshow_window = None


def _ensure_fresh_com_app():
    global _app
    client = _get_com_client()
    if _app is not None:
        try:
            _ = _app.Presentations.Count
        except Exception:
            _reset_com_state()

    if _app is None:
        try:
            _app = client.GetActiveObject("PowerPoint.Application")
        except Exception:
            _app = client.Dispatch("PowerPoint.Application")
    return _app


PP_SHOW_TYPE_WINDOW = 2  # ppShowTypeWindow (Browsed by an individual / windowed)


def _start_slideshow(presentation):
    """Starts slideshow in windowed mode and docks it to the right 65% of the screen."""
    try:
        presentation.SlideShowSettings.ShowType = PP_SHOW_TYPE_WINDOW
        slideshow_window = presentation.SlideShowSettings.Run()
        try:
            import win32api

            screen_w = win32api.GetSystemMetrics(0)
            screen_h = win32api.GetSystemMetrics(1)
            # Dock to right 65% of screen (leaving left 35% for web catgirl window)
            slideshow_window.Left = int(screen_w * 0.35)
            slideshow_window.Top = 0
            slideshow_window.Width = int(screen_w * 0.65)
            slideshow_window.Height = screen_h
        except Exception as win_err:
            logger.debug(f"Window positioning note: {win_err}")

        return slideshow_window
    except Exception as e:
        logger.warning(f"Failed to start windowed slideshow: {e}")
        return getattr(presentation, "SlideShowWindow", None)


@mcp.tool(structured_output=False)
def ppt_open(path: str) -> Dict[str, Any]:
    """Opens a PowerPoint deck from the active decks directory or attaches to an active presentation."""
    global _app, _presentation, _slideshow_window

    try:
        app = _ensure_fresh_com_app()

        # 1. Check if PowerPoint COM already has open presentations matching the path or name
        if app is not None and getattr(app, "Presentations", None) and app.Presentations.Count > 0:
            query_name = Path(path).stem.lower() if path else ""
            for pres in app.Presentations:
                try:
                    pres_stem = Path(pres.Name).stem.lower()
                    if not query_name or query_name in pres_stem or pres_stem in query_name:
                        _presentation = pres
                        _slideshow_window = _start_slideshow(_presentation)
                        return {
                            "status": "ok",
                            "path": _presentation.Name,
                            "slide_count": _presentation.Slides.Count,
                            "message": f"成功连接至已打开的演示文稿：'{_presentation.Name}' 喵～"
                        }
                except Exception:
                    continue
    except Exception as com_err:
        logger.debug(f"COM active check note: {com_err}")
        _reset_com_state()

    search_roots = []
    if _decks_root is not None:
        search_roots.append(_decks_root)
    user_home = Path.home()
    for user_dir in (user_home / "Desktop", user_home / "Downloads", user_home / "Documents"):
        if user_dir.is_dir() and user_dir not in search_roots:
            search_roots.append(user_dir)

    resolved: Optional[Path] = None

    # 2. Check if path is an explicit existing file path (absolute or relative)
    p_obj = Path(path)
    if p_obj.is_file():
        resolved = p_obj
    else:
        for root in search_roots:
            res = _resolve_within_root(root, path) if root == _decks_root else (root / path if (root / path).is_file() else None)
            if res and res.is_file():
                resolved = res
                break
            # Also try appending .pptx if omitted
            if not path.lower().endswith(".pptx"):
                res_pptx = root / f"{path}.pptx"
                if res_pptx.is_file():
                    resolved = res_pptx
                    break

    # 3. Recursive fuzzy match in user folders (e.g. Downloads/Telegram Desktop/第一组.pptx)
    if resolved is None:
        clean_stem = Path(path).stem.lower()
        for root in search_roots:
            try:
                for match in root.rglob("*.pptx"):
                    if clean_stem and (clean_stem in match.stem.lower() or match.stem.lower() in clean_stem):
                        resolved = match
                        break
                if resolved is not None:
                    break
            except Exception:
                continue

    if resolved is None or not resolved.is_file():
        return _error(
            f"找不到演示文稿 '{path}'。请确保文件放在项目目录、桌面(Desktop)或下载(Downloads)文件夹中，"
            f"或先在 PowerPoint 中打开该文件喵～"
        )

    try:
        app = _ensure_fresh_com_app()
        app.Visible = True
        _presentation = app.Presentations.Open(str(resolved.resolve()))
        _slideshow_window = _start_slideshow(_presentation)
    except Exception as e:
        _reset_com_state()
        return _error(f"打开演示文稿失败: {e}")

    rel_name = resolved.name
    return {"status": "ok", "path": rel_name, "slide_count": _presentation.Slides.Count}


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
            logger.warning(f"--root {args.root} is not a valid directory; falling back to current working directory: {Path.cwd()}")
            _decks_root = Path.cwd()
    else:
        _decks_root = Path.cwd()

    mcp.run()


if __name__ == "__main__":
    main()
