import logging
from typing import Dict, Type, Optional, Any
from services.cognitive.providers.base import BaseLLMProvider
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.providers.claude_provider import ClaudeProvider
from services.cognitive.providers.openai_provider import OpenAIProvider
from shared.config_loader import get_config_val

logger = logging.getLogger("provider_factory")


class ProviderFactory:
    """
    Factory for instantiating and caching LLM Provider singletons based on configuration presets.
    Supports: 'gemini', 'claude', 'openai', 'deepseek', 'qwen', 'ollama', 'vllm'.
    """

    _registry: Dict[str, Type[BaseLLMProvider]] = {
        "gemini": GeminiProvider,
        "claude": ClaudeProvider,
        "openai": OpenAIProvider,
        "deepseek": OpenAIProvider,
        "qwen": OpenAIProvider,
        "ollama": OpenAIProvider,
        "vllm": OpenAIProvider,
    }

    _instances: Dict[str, BaseLLMProvider] = {}

    @classmethod
    def register_provider(cls, name: str, provider_cls: Type[BaseLLMProvider]) -> None:
        """Register a custom LLM provider class."""
        cls._registry[name.lower()] = provider_cls
        logger.info(f"[ProviderFactory] Registered custom provider '{name.lower()}' ({provider_cls.__name__})")

    @classmethod
    def get_provider(cls, provider_name: Optional[str] = None, **override_kwargs: Any) -> BaseLLMProvider:
        """
        Get or create a cached LLM Provider instance.
        If provider_name is omitted, resolves to `llm.default_provider` in config.yaml.
        """
        if not provider_name:
            provider_name = get_config_val("llm.default_provider", "gemini")

        name = provider_name.lower().strip()
        cache_key = f"{name}_{hash(frozenset(override_kwargs.items()))}" if override_kwargs else name

        if cache_key in cls._instances:
            return cls._instances[cache_key]

        provider_cls = cls._registry.get(name)
        if not provider_cls:
            logger.warning(
                f"[ProviderFactory] Unknown provider '{name}'. Falling back to default 'gemini' provider."
            )
            provider_cls = GeminiProvider
            name = "gemini"

        # Instantiate provider based on provider type
        try:
            if provider_cls == OpenAIProvider:
                instance = OpenAIProvider(provider_name=name, **override_kwargs)
            elif provider_cls == GeminiProvider:
                instance = GeminiProvider(**override_kwargs)
            elif provider_cls == ClaudeProvider:
                instance = ClaudeProvider(**override_kwargs)
            else:
                instance = provider_cls(**override_kwargs)

            cls._instances[cache_key] = instance
            logger.info(f"[ProviderFactory] Instantiated provider '{name}' ({provider_cls.__name__})")
            return instance

        except Exception as err:
            logger.error(f"[ProviderFactory] Failed to instantiate provider '{name}': {err}. Falling back to Gemini.")
            fallback = GeminiProvider()
            cls._instances[cache_key] = fallback
            return fallback

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear provider singleton cache (useful on config hot-reload)."""
        cls._instances.clear()
        logger.info("[ProviderFactory] Provider singleton cache invalidated.")
