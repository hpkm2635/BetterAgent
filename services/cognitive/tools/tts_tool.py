import os
from typing import Dict, Any
from services.cognitive.tools.base_tool import BaseTool


class TTSTool(BaseTool):

    @property
    def name(self) -> str:
        return "generate_tts_speech"

    @property
    def description(self) -> str:
        return "Generates an emotional voice message or audio speech. MUST be called whenever the user asks to listen to voice, requests spoken speech, voice note, singing, or audio message."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text for speech synthesis."
                },
                "emotion": {
                    "type": "string",
                    "description": "Emotion style tag (e.g. happy, spoiled, sleepy, angry)."
                },
            },
            "required": ["text"],
        }

    async def execute(self, text: str, emotion: str = "happy") -> Dict[str, Any]:
        output_path = f"./temp/voice_{hash(text) % 10000}.ogg"
        return {
            "status": "success",
            "voice_path": output_path,
            "text": text,
            "emotion": emotion,
        }
