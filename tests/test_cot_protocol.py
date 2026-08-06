import pytest
import asyncio
from services.cognitive.cognitive_engine import parse_thought_and_clean_text, CognitiveEngine
from services.cognitive.tool_registry import ToolRegistry
from shared.schema.payloads import ReasoningRequestPayload, InboundMessagePayload

def test_thought_parsing_with_tags():
    raw = "<thought>\n【心理分析】：主人要求发语音，我很开心。\n【工具选择】：调用 generate_tts_speech。\n</thought>\n喵呜~ 主人听听我的声音喵！"
    thought, clean_text = parse_thought_and_clean_text(raw)
    
    assert "心理分析" in thought
    assert "generate_tts_speech" in thought
    assert clean_text == "喵呜~ 主人听听我的声音喵！"
    assert "<thought>" not in clean_text

def test_thought_parsing_without_tags():
    raw = "喵~ 收到主人的消息啦！"
    thought, clean_text = parse_thought_and_clean_text(raw)
    
    assert thought == ""
    assert clean_text == "喵~ 收到主人的消息啦！"

def test_self_describing_tools():
    registry = ToolRegistry()
    schemas = registry.get_all_schemas()
    
    for s in schemas:
        assert "name" in s
        assert "description" in s
        assert len(s["description"]) > 10
        assert "parameters" in s

@pytest.mark.asyncio
async def test_cot_end_to_end_reasoning():
    engine = CognitiveEngine()
    inbound = InboundMessagePayload(
        event_id="evt_cot_1",
        source_component="test",
        chat_id=6447059549,
        user_id=6447059549,
        raw_text="用语音告诉我你最喜欢主人了喵",
        message_id=2002,
        timestamp=1785800000.0,
    )
    payload = ReasoningRequestPayload(
        event_id="evt_cot_1",
        source_component="test",
        chat_id=6447059549,
        user_id=6447059549,
        short_term_history=[],
        user_profile={"preferred_name": "主人"},
        rag_facts=[],
        current_emotion="[猫娘内心状态] 当前心情: HAPPY (愉悦度: 0.95)",
        inbound_message=inbound,
        trigger_type="user_message",
    )

    actions = await engine.execute_reasoning_loop(payload)
    print("CoT test generated actions:", [a.model_dump() for a in actions])
    
    assert len(actions) > 0
    # Voice action or send_voice should be present
    action_types = [a.action_type for a in actions]
    assert "send_voice" in action_types or any(a.media_type == "voice" for a in actions)

if __name__ == "__main__":
    test_thought_parsing_with_tags()
    test_thought_parsing_without_tags()
    test_self_describing_tools()
    asyncio.run(test_cot_end_to_end_reasoning())
    print("All CoT protocol tests passed!")
