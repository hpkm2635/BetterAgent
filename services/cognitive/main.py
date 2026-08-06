import asyncio
import json
import logging
import os
import nats
from dotenv import load_dotenv
from shared.subjects import (
    SUBJECT_REASONING_REQUEST,
    SUBJECT_ACTION_DECISION,
    SUBJECT_REASONING_COMPLETED,
    SUBJECT_VISION_FRAME,
)
from shared.schema.payloads import ReasoningRequestPayload, ActionDecisionPayload
from shared.logger import setup_logger
from services.cognitive.cognitive_engine import CognitiveEngine

load_dotenv()
logger = setup_logger("cognitive_service")


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

    engine = CognitiveEngine()

    async def reasoning_handler(msg):
        req_chat_id = 0
        req_event_id = "evt_err"
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            req_chat_id = payload_dict.get("chat_id", 0)
            req_event_id = payload_dict.get("event_id", data.get("id", "evt_err"))
            req = ReasoningRequestPayload(**payload_dict)

            logger.info(f"Processing ReasoningRequest for chat_id={req.chat_id}")
            actions = await engine.execute_reasoning_loop(req)

            # Publish each ActionDecision Payload
            for act in actions:
                envelope = {
                    "id": req.event_id,
                    "subject": SUBJECT_ACTION_DECISION,
                    "source": "cognitive_service",
                    "payload": act.model_dump(),
                }
                await nc.publish(SUBJECT_ACTION_DECISION, json.dumps(envelope).encode())
                logger.info(f"Published ActionDecision: {act.action_type} to chat_id={act.chat_id}")

            # Publish Reasoning Completed
            completed_envelope = {
                "id": req.event_id,
                "subject": SUBJECT_REASONING_COMPLETED,
                "source": "cognitive_service",
                "payload": {
                    "chat_id": req.chat_id,
                    "has_action": len(actions) > 0,
                },
            }
            await nc.publish(SUBJECT_REASONING_COMPLETED, json.dumps(completed_envelope).encode())

        except Exception as e:
            logger.error(f"Error handling reasoning request: {e}", exc_info=True)
            # FAIL-FAST: Publish Fallback Error ActionDecision & ReasoningCompleted so Go Core never deadlocks
            if req_chat_id:
                fallback_act = ActionDecisionPayload(
                    event_id=req_event_id,
                    source_component="cognitive_service",
                    chat_id=req_chat_id,
                    action_type="send_message",
                    text_content="呜……人家的大脑突然打了个瞌睡喵，主人能不能过一会儿再理我一次？",
                    chat_action="typing",
                )
                err_envelope = {
                    "id": req_event_id,
                    "subject": SUBJECT_ACTION_DECISION,
                    "source": "cognitive_service",
                    "payload": fallback_act.model_dump(),
                }
                await nc.publish(SUBJECT_ACTION_DECISION, json.dumps(err_envelope).encode())

                completed_envelope = {
                    "id": req_event_id,
                    "subject": SUBJECT_REASONING_COMPLETED,
                    "source": "cognitive_service",
                    "payload": {
                        "chat_id": req_chat_id,
                        "has_action": True,
                    },
                }
                await nc.publish(SUBJECT_REASONING_COMPLETED, json.dumps(completed_envelope).encode())

    async def vision_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            chat_id = payload_dict.get("chat_id", 0)
            image_base64 = payload_dict.get("image_base64", "")
            source_type = payload_dict.get("source_type", "screen")
            fmt = payload_dict.get("format", "jpeg")

            if chat_id and image_base64:
                engine.update_vision_frame(chat_id, image_base64, source_type, fmt)
                logger.info(f"📷 Vision frame updated in CognitiveEngine for chat_id={chat_id} ({source_type}, {len(image_base64)} base64 bytes)")
        except Exception as e:
            logger.warning(f"Error handling vision frame: {e}")

    await nc.subscribe(SUBJECT_REASONING_REQUEST, queue="cognitive_workers", cb=reasoning_handler)
    await nc.subscribe(SUBJECT_VISION_FRAME, cb=vision_handler)

    logger.info("Cognitive service listening on NATS subjects (Reasoning & Vision Frame)...")
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
