import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from services.memory.memory_hub import MemoryHub
from services.cognitive.prompt_builder import PromptBuilder
from shared.schema.payloads import EnrichContextReqPayload, ReasoningRequestPayload, InboundMessagePayload


@pytest.mark.asyncio
async def test_memory_hub_search_campus_kb_graceful_fallback():
    """Verify search_campus_kb returns [] gracefully when campus_kb service is offline or errors out."""
    hub = MemoryHub()
    try:
        # Mock connection error
        with patch.object(hub.vector_store, "_get_http_session", side_effect=ConnectionError("KB Service Offline")):
            facts = await hub.search_campus_kb("图书馆开门时间")
            assert facts == []
    finally:
        await hub.vector_store.close()


@pytest.mark.asyncio
async def test_memory_hub_concurrent_rag_and_kb_retrieval():
    """Verify MemoryHub retrieves personal RAG and Campus KB facts concurrently via asyncio.gather."""
    hub = MemoryHub()
    user_id = 778899

    req_payload = EnrichContextReqPayload(
        event_id="evt_kb_1",
        source_component="csm",
        chat_id=user_id,
        user_id=user_id,
        current_state="IDLE",
        trigger_type="chat_message",
        inbound_message=InboundMessagePayload(
            event_id="evt_in_kb",
            source_component="gotd",
            chat_id=user_id,
            user_id=user_id,
            message_id=1,
            raw_text="图书馆几点关门？",
        )
    )

    mock_rag_facts = ["个人记忆: 主人喜欢在图书馆看书"]
    mock_kb_facts = ["图书馆周一至周五开放至22:00，周末20:00关闭。 (来源: faq.md)"]

    try:
        with patch.object(hub.vector_store, "search_relevant_memories", new_callable=AsyncMock, return_value=mock_rag_facts), \
             patch.object(hub, "search_campus_kb", new_callable=AsyncMock, return_value=mock_kb_facts):

            reasoning_req = await hub.handle_enrich_context_req(req_payload)

            assert isinstance(reasoning_req, ReasoningRequestPayload)
            assert reasoning_req.rag_facts == mock_rag_facts
            assert reasoning_req.kb_facts == mock_kb_facts

            # Test PromptBuilder injection
            system_prompt = PromptBuilder.build_system_prompt(reasoning_req)
            assert "[长期记忆/个人相关信息]:" in system_prompt
            assert "主人喜欢在图书馆看书" in system_prompt
            assert "[校园知识库 (Campus KB)]:" in system_prompt
            assert "图书馆周一至周五开放至22:00" in system_prompt
    finally:
        await hub.vector_store.close()
