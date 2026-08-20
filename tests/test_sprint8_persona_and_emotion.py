import json
import pytest
import os
from unittest.mock import MagicMock
from shared.persona_loader import PersonaLoader


@pytest.mark.asyncio
async def test_persona_loader_hot_reload_and_patch(tmp_path):
    """Verify PersonaLoader._patch_yaml() and handle_persona_update() hot reloading."""
    PersonaLoader.invalidate_cache()

    # Create temporary persona file
    test_yaml = tmp_path / "test_catgirl.yaml"
    test_yaml.write_text(
        "id: catgirl\nname: Camelia\nbase_prompt: Original Base Prompt\n",
        encoding="utf-8"
    )

    # Patch PersonaLoader._persona_path to return our temp file
    PersonaLoader._persona_path = lambda persona_id: str(test_yaml)

    # Test patch
    patch_payload = {
        "persona_id": "catgirl",
        "name": "Camelia v2",
        "base_prompt": "Updated Base Prompt喵~",
        "knowledge_scope": "校园 FAQ 知识域",
    }
    raw_nats_bytes = json.dumps(patch_payload).encode("utf-8")

    await PersonaLoader.handle_persona_update(raw_nats_bytes)

    # Verify memory cache is invalidated and updated content is read
    assert PersonaLoader._cached_persona == {}

    content = test_yaml.read_text(encoding="utf-8")
    assert "Camelia v2" in content
    assert "Updated Base Prompt喵~" in content
    assert "校园 FAQ 知识域" in content


@pytest.mark.asyncio
async def test_persona_loader_whitelist_filtering(tmp_path):
    """Verify handle_persona_update ignores disallowed fields (security boundary)."""
    PersonaLoader.invalidate_cache()

    test_yaml = tmp_path / "test_catgirl.yaml"
    test_yaml.write_text("id: catgirl\nname: Camelia\n", encoding="utf-8")
    PersonaLoader._persona_path = lambda persona_id: str(test_yaml)

    # Payload with forbidden fields (e.g. tts provider or system secrets)
    bad_payload = {
        "persona_id": "catgirl",
        "name": "New Camelia",
        "tts_provider": "malicious_provider",
        "system_secret": "hacked_key",
    }
    await PersonaLoader.handle_persona_update(json.dumps(bad_payload).encode("utf-8"))

    content = test_yaml.read_text(encoding="utf-8")
    assert "New Camelia" in content
    assert "malicious_provider" not in content
    assert "hacked_key" not in content


@pytest.mark.asyncio
async def test_prompt_builder_injects_knowledge_scope_and_forbidden_topics(tmp_path):
    """Verify PromptBuilder includes knowledge_scope and forbidden_topics in system prompt."""
    from services.cognitive.prompt_builder import PromptBuilder
    from shared.schema.payloads import ReasoningRequestPayload

    PersonaLoader.invalidate_cache()
    test_yaml = tmp_path / "test_catgirl.yaml"
    test_yaml.write_text(
        "id: catgirl\n"
        "name: Camelia\n"
        "base_prompt: 你叫 Camelia，是一个猫娘喵~\n"
        "knowledge_scope: 校园 FAQ 知识域\n"
        "forbidden_topics: 政治敏感与非法话题\n",
        encoding="utf-8"
    )
    PersonaLoader._persona_path = lambda persona_id: str(test_yaml)

    payload = ReasoningRequestPayload(
        event_id="evt_test",
        source_component="test",
        chat_id=123,
        user_id=456,
        current_emotion="[情绪]: 快乐",
    )

    system_prompt = PromptBuilder.build_system_prompt(payload)
    assert "[知识专业范围]: 你擅长并专注于回答关于【校园 FAQ 知识域】" in system_prompt
    assert "[禁忌话题与交互边界 - 严格遵守]: 严禁讨论以下话题内容【政治敏感与非法话题】" in system_prompt

