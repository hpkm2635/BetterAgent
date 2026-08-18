from typing import Dict, List, Any, Optional, Set
from services.cognitive.tools.base_tool import BaseTool
from services.cognitive.tools.tts_tool import TTSTool
from services.cognitive.tools.image_gen_tool import ImageGenTool
from services.cognitive.tools.telegram_action_tool import TelegramActionTool
from services.cognitive.tools.presenter_control_tool import PresenterControlTool
from services.cognitive.tools.campus_kb_tool import CampusKBTool
from services.cognitive.tools.sts2_http_client import Sts2HttpClient
from services.cognitive.tools.sts2_action_tool import build_sts2_tools
from services.cognitive.mcp.presenter_manager import PresenterSessionManager


class ToolRegistry:

    def __init__(self, presenter_manager: Optional[PresenterSessionManager] = None):
        self._tools: Dict[str, BaseTool] = {}
        self._game_tool_names: Set[str] = set()
        self.register(TTSTool())
        self.register(ImageGenTool())
        self.register(TelegramActionTool())
        self.register(CampusKBTool())
        if presenter_manager is not None:
            self.register(PresenterControlTool(presenter_manager))

        # Registered unconditionally (cheap -- no subprocess, unlike
        # PresenterSessionManager) but tracked separately so get_all_schemas
        # never leaks them into an ordinary chat turn; only exposed via
        # get_game_schemas() to trigger_type == "game_turn" turns (see
        # cognitive_engine.py's stream_reasoning_loop).
        sts2_http_client = Sts2HttpClient()
        for tool in build_sts2_tools(sts2_http_client):
            self.register(tool)
            self._game_tool_names.add(tool.name)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        return [{
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        } for tool in self._tools.values() if tool.name not in self._game_tool_names]

    def get_game_schemas(self) -> List[Dict[str, Any]]:
        return [{
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        } for tool in self._tools.values() if tool.name in self._game_tool_names]
