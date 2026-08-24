"""Regression test for the BYOK provider hot-reload defect: ProviderFactory
had an invalidate_cache() method whose docstring promised it was "useful on
config hot-reload", but nothing ever called it -- and even if something did,
CognitiveEngine cached its own self.default_provider once at __init__ and
never re-resolved it, so an admin-panel API key / default provider change
required a full service restart to take effect.
"""
from unittest.mock import patch

from services.cognitive.cognitive_engine import CognitiveEngine
from services.cognitive.providers.factory import ProviderFactory
from services.cognitive.providers.gemini_provider import GeminiProvider
from services.cognitive.providers.claude_provider import ClaudeProvider


def test_refresh_default_provider_picks_up_new_config_after_cache_invalidation():
    ProviderFactory.invalidate_cache()

    with patch(
        "services.cognitive.cognitive_engine.get_config_val",
        return_value="gemini",
    ):
        engine = CognitiveEngine()

    assert isinstance(engine.default_provider, GeminiProvider)
    original_instance = engine.default_provider

    # Simulate an admin-panel BYOK change: default_provider switches to
    # "claude" in config.yaml, and the admin backend publishes
    # agent.config.reloaded (services/cognitive/main.py's
    # config_reloaded_handler calls exactly these two lines in response).
    ProviderFactory.invalidate_cache()
    with patch(
        "services.cognitive.cognitive_engine.get_config_val",
        return_value="claude",
    ):
        engine.refresh_default_provider()

    assert isinstance(engine.default_provider, ClaudeProvider)
    assert engine.default_provider is not original_instance
