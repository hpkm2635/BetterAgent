import pytest
import asyncio
from shared.schema.payloads import ReasoningRequestPayload, InboundMessagePayload
from services.cognitive.cognitive_engine import CognitiveEngine

@pytest.mark.asyncio
async def test_cognitive_engine_selfie_request():
    engine = CognitiveEngine()
    
    inbound = InboundMessagePayload(
        event_id="evt_test_1",
        source_component="test",
        chat_id=6447059549,
        user_id=6447059549,
        raw_text="发张自拍照给主人看喵",
        message_id=1001,
        timestamp=1785800000.0,
    )

    payload = ReasoningRequestPayload(
        event_id="evt_test_1",
        source_component="test",
        chat_id=6447059549,
        user_id=6447059549,
        short_term_history=[],
        user_profile={"preferred_name": "主人"},
        rag_facts=[],
        current_emotion="[猫娘内心状态] 当前心情: HAPPY (愉悦度: 0.9)",
        inbound_message=inbound,
        trigger_type="user_message",
    )

    actions = await engine.execute_reasoning_loop(payload)
    
    print("CognitiveEngine generated actions:", [a.model_dump() for a in actions])
    assert len(actions) > 0
    
    # Verify that at least one action contains photo_path or send_photo action type
    action_types = [a.action_type for a in actions]
    assert "send_photo" in action_types or any(a.media_type == "photo" for a in actions)

if __name__ == "__main__":
    asyncio.run(test_cognitive_engine_selfie_request())
    print("Cognitive Engine End-to-End selfie test passed!")
