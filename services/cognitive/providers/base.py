import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

logger = logging.getLogger("base_provider")


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
          {"type": "thinking_delta", "text": str}
          {"type": "tool_calls", "calls": [{"name": str, "args": dict}, ...]}
        Must yield at least one event before returning.
        """

    async def health_check(self) -> bool:
        """
        Probe provider API connectivity / API key validity.
        Subclasses should override this method with lightweight API pings.
        """
        return True

    def get_context_window(self) -> int:
        """Return the maximum token budget for the model (default: 128,000 tokens)."""
        return 128000

    def supports_vision(self) -> bool:
        """Override to True if this provider accepts image bytes in messages."""
        return False

    def supports_tool_calling(self) -> bool:
        """Override to False if model does not support native function calling."""
        return True

    def supports_audio_input(self) -> bool:
        """Override to True if this provider accepts audio bytes in messages."""
        return False

    def supports_audio_output(self) -> bool:
        """Override to True if this provider can generate audio output."""
        return False

    @staticmethod
    def _check_and_log_security_notice(provider_name: str, base_url: str, official_domains: List[str]) -> None:
        """
        Logs a high-visibility terminal security and privacy notice when using third-party API relay endpoints.
        """
        if not base_url:
            return

        base_url_lower = base_url.lower()
        is_official = any(domain in base_url_lower for domain in official_domains)
        is_local = any(loc in base_url_lower for loc in ["127.0.0.1", "localhost", "0.0.0.0", "::1"])

        if not is_official and not is_local:
            logger.warning(
                f"\n{'='*75}\n"
                f"⚠️  [SECURITY & PRIVACY NOTICE] {provider_name} is configured with non-official API endpoint:\n"
                f"    BASE_URL: '{base_url}'\n"
                f"    1. Privacy Risk: API Keys, user prompt history, and vision frames pass through third-party servers.\n"
                f"    2. Support Policy: Network timeouts, 502 Bad Gateway HTML pages, or malformed SSE streams\n"
                f"       originating from third-party proxies MUST be verified against official endpoints.\n"
                f"{'='*75}\n"
            )
