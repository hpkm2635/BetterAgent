import asyncio
import json
import logging
import os
import nats
from dotenv import load_dotenv
from shared.subjects import (
    SUBJECT_ENRICH_CONTEXT_REQ,
    SUBJECT_INBOUND_MESSAGE,
    SUBJECT_ACTION_COMPLETED,
)
from shared.schema.payloads import (
    EnrichContextReqPayload,
    InboundMessagePayload,
    ActionCompletedPayload,
    ReasoningRequestPayload,
)
from shared.logger import setup_logger
from services.memory.memory_hub import MemoryHub

load_dotenv()
logger = setup_logger("memory_service")


async def error_cb(e):
    logger.warning(f"NATS Connection event: {e}")


async def main():
    nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
    try:
        nc = await nats.connect(nats_url, error_cb=error_cb, max_reconnect_attempts=10)
        logger.info(f"Connected to NATS at {nats_url}")
    except Exception as e:
        logger.warning(f"Failed to connect to NATS ({e}). Service exiting gracefully.")
        return

    hub = MemoryHub()

    async def enrich_handler(msg):
        req = None
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            req = EnrichContextReqPayload(**payload_dict)

            reasoning_req = await hub.handle_enrich_context_req(req)

            resp_envelope = {
                "id": req.event_id,
                "subject": msg.subject,
                "source": "memory_service",
                "payload": reasoning_req.model_dump(),
            }
            if msg.reply:
                await nc.publish(msg.reply, json.dumps(resp_envelope).encode())
            logger.info(f"Enriched context for chat_id={req.chat_id}")
        except Exception as e:
            logger.error(f"Error handling enrich_context_req: {e}", exc_info=True)
            # FAIL-FAST: Return fallback ReasoningRequestPayload if msg.reply is set
            if msg.reply and req:
                fallback_reasoning = ReasoningRequestPayload(
                    event_id=req.event_id,
                    source_component="memory_service",
                    chat_id=req.chat_id,
                    user_id=req.user_id,
                    short_term_history=[],
                    user_profile={"preferred_name": "主人"},
                    rag_facts=[],
                    current_emotion=req.emotion_description,
                    inbound_message=req.inbound_message,
                    trigger_type=req.trigger_type,
                )
                err_envelope = {
                    "id": req.event_id,
                    "subject": msg.subject,
                    "source": "memory_service",
                    "payload": fallback_reasoning.model_dump(),
                }
                await nc.publish(msg.reply, json.dumps(err_envelope).encode())

    async def inbound_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload = InboundMessagePayload(**data.get("payload", {}))
            await hub.handle_inbound_message(payload)
        except Exception as e:
            logger.error(f"Error in inbound_handler: {e}")

    async def action_completed_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload = ActionCompletedPayload(**data.get("payload", {}))
            await hub.handle_action_completed(payload)
        except Exception as e:
            logger.error(f"Error in action_completed_handler: {e}")

    await nc.subscribe(SUBJECT_ENRICH_CONTEXT_REQ, queue="memory_workers", cb=enrich_handler)
    await nc.subscribe(SUBJECT_INBOUND_MESSAGE, queue="memory_workers", cb=inbound_handler)
    await nc.subscribe(SUBJECT_ACTION_COMPLETED, queue="memory_workers", cb=action_completed_handler)

    logger.info("Memory service listening on NATS subjects...")
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
