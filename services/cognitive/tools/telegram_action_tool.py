import logging
from typing import Dict, Any, Optional
from services.cognitive.tools.base_tool import BaseTool
from services.cognitive.tools.validation import is_safe_media_filename

logger = logging.getLogger("telegram_action_tool")


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
        # sticker_id is model-controlled and is used downstream as a local
        # filename -- reject anything that isn't a bare filename so it can
        # never be used to reference a file outside the managed temp dir.
        if sticker_id is not None and not is_safe_media_filename(sticker_id):
            logger.warning(f"Rejected unsafe sticker_id from LLM tool call: {sticker_id!r}")
            sticker_id = None

        return {
            "status": "success",
            "action_type": action_type,
            "sticker_id": sticker_id,
            "reaction_emoji": reaction_emoji,
        }
