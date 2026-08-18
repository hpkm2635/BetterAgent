import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from services.memory.memory_hub import MemoryHub
from services.memory.user_profile import UserProfileManager
from services.cognitive.prompt_builder import PromptBuilder
from shared.schema.payloads import ActionDecisionPayload, ActionCompletedPayload, EnrichContextReqPayload, ReasoningRequestPayload


@pytest.mark.asyncio
async def test_agent_self_memory_record_and_prompt_injection():
    """Verify AgentSelfMemory records completed actions and injects them into system prompt."""
    hub = MemoryHub()
    chat_id = 889900

    # 1. Simulate completed action
    decision = ActionDecisionPayload(
        event_id="act_s4_1",
        source_component="cognitive",
        chat_id=chat_id,
        action_type="send_message",
        text_content="已为主人查询好图书室开门时间喵~",
        is_final=True
    )
    action_completed = ActionCompletedPayload(
        event_id="act_s4_1",
        source_component="webgateway",
        chat_id=chat_id,
        action_decision=decision,
        status="success"
    )

    await hub.handle_action_completed(action_completed)

    # Assert self_memory captured the event
    self_events = hub.self_memory.get_recent_self_events(limit=3)
    assert len(self_events) == 1
    assert "执行动作 send_message" in self_events[0]["description"]

    # 2. Enrich context request carries agent_self_events
    req_payload = EnrichContextReqPayload(
        event_id="evt_s4_req",
        source_component="csm",
        chat_id=chat_id,
        user_id=chat_id,
        current_state="IDLE",
        trigger_type="chat_message"
    )

    reasoning_req = await hub.handle_enrich_context_req(req_payload)
    assert len(reasoning_req.agent_self_events) == 1

    # 3. PromptBuilder injects section
    system_prompt = PromptBuilder.build_system_prompt(reasoning_req)
    assert "[Agent 自身近期行为记录]:" in system_prompt
    assert "执行动作 send_message" in system_prompt

    await hub.vector_store.close()


@pytest.mark.asyncio
async def test_user_profile_manager_config_default_loading():
    """Verify UserProfileManager loads default preferred_name without hardcoded dummy values."""
    mgr = UserProfileManager()
    mgr.redis_client = None
    mgr._redis_disabled = True

    profile = await mgr.get_profile(user_id=123456)
    assert profile["preferred_name"] == "主人"
    assert profile["likes"] == []
    assert profile["dislikes"] == []
