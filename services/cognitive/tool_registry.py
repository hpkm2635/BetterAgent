from typing import Dict, List, Any, Optional
from services.cognitive.tools.base_tool import BaseTool
from services.cognitive.tools.tts_tool import TTSTool
from services.cognitive.tools.image_gen_tool import ImageGenTool
from services.cognitive.tools.telegram_action_tool import TelegramActionTool
from services.cognitive.tools.presenter_control_tool import PresenterControlTool
from services.cognitive.mcp.presenter_manager import PresenterSessionManager


class ToolRegistry:

    def __init__(self, presenter_manager: Optional[PresenterSessionManager] = None):
        self._tools: Dict[str, BaseTool] = {}
        self.register(TTSTool())
        self.register(ImageGenTool())
        self.register(TelegramActionTool())
        if presenter_manager is not None:
            self.register(PresenterControlTool(presenter_manager))

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        return [{
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        } for tool in self._tools.values()]
