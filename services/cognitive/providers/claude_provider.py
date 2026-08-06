import os
from typing import List, Dict, Any, Optional
from services.cognitive.providers.base import BaseLLMProvider
from shared.config_loader import get_config_val


class ClaudeProvider(BaseLLMProvider):

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("CLAUDE_API_KEY", "")
        self.model_name = get_config_val("llm.claude.model", "claude-3-5-sonnet-20241022")

    async def generate(self,
                       messages: List[Dict[str, Any]],
                       tools_schema: Optional[List[Dict[str, Any]]] = None,
                       system_prompt: Optional[str] = None) -> Dict[str, Any]:
        last_msg = messages[-1]["content"] if messages else ""
        reply_text = f"哼，主人（Claude视角）：{last_msg} 喵~"

        return {
            "text": reply_text,
            "tool_calls": [],
            "finish_reason": "end_turn"
        }
