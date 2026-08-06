from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseLLMProvider(ABC):

    @abstractmethod
    async def generate(self,
                       messages: List[Dict[str, Any]],
                       tools_schema: Optional[List[Dict[str, Any]]] = None,
                       system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns a standardized response dict:
        {
            "text": str,
            "tool_calls": List[Dict],
            "finish_reason": str
        }
        """
        pass
