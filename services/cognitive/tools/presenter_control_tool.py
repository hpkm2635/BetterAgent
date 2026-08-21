import logging
from typing import Any, Dict, Optional

from services.cognitive.tools.base_tool import BaseTool
from services.cognitive.mcp.presenter_manager import PresenterSessionManager

logger = logging.getLogger("presenter_control_tool")


class PresenterControlTool(BaseTool):
    """
    Meta-tool that turns the ppt_*/vscode_* MCP tool surface on/off for a chat.
    Kept as a plain local BaseTool (always visible, no MCP round-trip needed)
    so the LLM has a cheap, explicit switch -- outside of an active session it
    can't even see the presenter tools exist.
    """

    def __init__(self, presenter_manager: PresenterSessionManager):
        self._presenter_manager = presenter_manager

    @property
    def name(self) -> str:
        return "presenter_mode"

    @property
    def description(self) -> str:
        return (
            "Activates or deactivates a presenter MCP session for the current chat: "
            "'ppt' for PowerPoint slide narration, 'vscode' for a code walkthrough. "
            "No ppt_*/vscode_* tool exists yet -- not vscode_search, not vscode_read_range, "
            "none of them -- until this is called with action=activate first. Never guess a "
            "ppt_*/vscode_* tool name before activating; the activation result lists the exact "
            "tool names this session actually has. root_path scopes the session to one directory "
            "(the deck's folder, or the workspace folder) -- file-facing tools "
            "in that session refuse to touch anything outside it."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["activate", "deactivate"]},
                "target": {"type": "string", "enum": ["ppt", "vscode"]},
                "root_path": {
                    "type": "string",
                    "description": "Directory to confine this session to (deck folder for ppt, workspace root for vscode). Optional for deactivate.",
                },
            },
            "required": ["action", "target"],
        }

    async def execute(
        self,
        action: str,
        target: str,
        root_path: Optional[str] = None,
        chat_id: Optional[int] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        if chat_id is None:
            return {"status": "error", "message": "presenter_mode requires an active chat context"}

        if action == "activate":
            import os
            resolved_root = root_path or os.getcwd()
            message = await self._presenter_manager.activate(chat_id, target, root_path=resolved_root)
        elif action == "deactivate":
            message = await self._presenter_manager.deactivate(chat_id, target)
        else:
            return {"status": "error", "message": f"unknown action: {action}"}

        return {"status": "ok", "message": message}
