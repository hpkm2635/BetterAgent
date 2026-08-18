import pytest
from services.cognitive.prompt_builder import PromptBuilder
from shared.persona_loader import PersonaLoader
from shared.schema.payloads import ReasoningRequestPayload


def test_prompt_builder_game_turn_omits_unrelated_sections():
    """Verify trigger_type='game_turn' omits personal RAG, KB, and self-event sections."""
    payload = ReasoningRequestPayload(
        event_id="evt_gt_1",
        source_component="csm",
        chat_id=1001,
        user_id=1001,
        trigger_type="game_turn",
        rag_facts=["个人事实: 主人喜欢看动漫"],
        kb_facts=["校园知识库: 宿舍23点断网"],
        agent_self_events=[{"description": "执行动作 send_message: 喵"}]
    )

    prompt = PromptBuilder.build_system_prompt(payload)

    assert "[游戏自动托管" in prompt
    assert "[长期记忆/个人相关信息]:" not in prompt
    assert "[校园知识库 (Campus KB)]:" not in prompt
    assert "[近期行为摘要]" not in prompt


def test_prompt_builder_chat_message_includes_compact_self_events():
    """Verify trigger_type='chat_message' formats self events as compact summary."""
    payload = ReasoningRequestPayload(
        event_id="evt_chat_1",
        source_component="csm",
        chat_id=1002,
        user_id=1002,
        trigger_type="chat_message",
        rag_facts=["个人事实: 主人喜欢吃火锅"],
        agent_self_events=[
            {"description": "执行动作 send_message: 喵喵喵"},
            {"description": "执行动作 send_message: 好的"},
            {"description": "执行动作 generate_image: 生成可爱猫娘"},
        ]
    )

    prompt = PromptBuilder.build_system_prompt(payload)

    assert "[Agent 自身近期行为记录]:" in prompt
    assert "早期动作摘要: 执行动作 send_message×1" in prompt
    assert "最新动作: 执行动作 generate_image: 生成可爱猫娘" in prompt


def test_persona_loader_cache_invalidation():
    """Verify PersonaLoader.invalidate_cache() clears cached persona dict."""
    p1 = PersonaLoader.load_active_persona()
    assert PersonaLoader._cached_persona != {}

    PersonaLoader.invalidate_cache()
    assert PersonaLoader._cached_persona == {}
    assert PersonaLoader._last_active_id == ""
