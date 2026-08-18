from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseLLMProvider(ABC):

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Single-shot generation.
        Returns: {"text": str, "tool_calls": List[Dict], "finish_reason": str}
        """

    @abstractmethod
    async def generate_stream(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        cancel_event: Optional[Any] = None,
    ):
        """
        Streaming generation. Yields tagged-union dicts:
          {"type": "text", "delta": str}
          {"type": "tool_calls", "calls": [{"name": str, "args": dict}, ...]}
        Must yield at least one event before returning.
        """

    def supports_vision(self) -> bool:
        """Override to True if this provider accepts image bytes in messages."""
        return False

    def supports_audio_input(self) -> bool:
        """Override to True if this provider accepts audio bytes in messages."""
        return False

    def supports_audio_output(self) -> bool:
        """Override to True if this provider can generate audio output."""
        return False
