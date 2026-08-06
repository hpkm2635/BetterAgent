from typing import Dict, Any, Optional
from services.cognitive.tools.base_tool import BaseTool


class TelegramActionTool(BaseTool):

    @property
    def name(self) -> str:
        return "telegram_action"

    @property
    def description(self) -> str:
        return "Performs Telegram interactive actions like sending a sticker or emoji reaction."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["send_sticker", "set_reaction"],
                },
                "sticker_id": {
                    "type": "string",
                    "description": "Telegram Sticker ID."
                },
                "reaction_emoji": {
                    "type": "string",
                    "description": "Emoji for reaction (e.g. ❤️, 👍, 😡)."
                },
            },
            "required": ["action_type"],
        }

    async def execute(self,
                      action_type: str,
                      sticker_id: Optional[str] = None,
                      reaction_emoji: Optional[str] = None) -> Dict[str, Any]:
        return {
            "status": "success",
            "action_type": action_type,
            "sticker_id": sticker_id,
            "reaction_emoji": reaction_emoji,
        }
