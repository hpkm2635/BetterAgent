import asyncio
import json
import logging
import os
import nats
from dotenv import load_dotenv
from shared.subjects import (
    SUBJECT_REASONING_REQUEST,
    SUBJECT_REASONING_COMPLETED,
    SUBJECT_VISION_FRAME,
    SUBJECT_STREAM_CANCEL_REQ,
    SUBJECT_USER_INTERRUPT,
    SUBJECT_EMOTION_DELTA,
    action_decision_subject,
)
from shared.schema.payloads import ReasoningRequestPayload, ActionDecisionPayload, EmotionDeltaPayload
from shared.logger import setup_logger
from shared.persona_loader import PersonaLoader
from services.cognitive.cognitive_engine import CognitiveEngine

load_dotenv()
logger = setup_logger("cognitive_service")


async def error_cb(e):
    logger.warning(f"NATS Connection event: {e}")


from shared.config_loader import get_config_val


async def main():
    # 127.0.0.1, not "localhost" -- see docs/SECURITY.md §2.8.
    nats_url = os.getenv("NATS_URL", get_config_val("infrastructure.nats_url", "nats://127.0.0.1:4222"))
    nats_user = os.getenv("NATS_USER")
    nats_password = os.getenv("NATS_PASSWORD")
    if not nats_user or not nats_password:
        logger.error("NATS_USER / NATS_PASSWORD are not set. Refusing to connect to an unauthenticated message bus (see .env.example).")
        return
    try:
        nc = await nats.connect(nats_url, user=nats_user, password=nats_password, error_cb=error_cb, max_reconnect_attempts=10)
        logger.info(f"Connected to NATS at {nats_url}")
    except Exception as e:
        logger.warning(f"Failed to connect to NATS ({e}). Service exiting gracefully.")
        return

    engine = CognitiveEngine()
    active_tasks: dict[int, tuple[asyncio.Task, asyncio.Event]] = {}

    def cancel_chat_stream(chat_id: int):
        if chat_id in active_tasks:
            task, event = active_tasks[chat_id]
            event.set()
            if not task.done():
                task.cancel()
            logger.info(f"⚡ Cancelled in-flight LLM stream for chat_id={chat_id}")

    async def cancel_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            chat_id = payload_dict.get("chat_id", 0)
            if chat_id:
                cancel_chat_stream(chat_id)
        except Exception as e:
            logger.warning(f"Error handling stream cancel request: {e}")

    async def run_streaming_reasoning(req: ReasoningRequestPayload, cancel_event: asyncio.Event):
        actions_count = 0
        try:
            async for act in engine.stream_reasoning_loop(req, cancel_event=cancel_event):
                if cancel_event.is_set():
                    logger.info(f"⚡ Stream loop stopped mid-reasoning for chat_id={req.chat_id}")
                    break

                if isinstance(act, EmotionDeltaPayload):
                    envelope = {
                        "id": req.event_id,
                        "subject": SUBJECT_EMOTION_DELTA,
                        "source": "cognitive_service",
                        "payload": act.model_dump(),
                    }
                    await nc.publish(SUBJECT_EMOTION_DELTA, json.dumps(envelope).encode())
                    logger.info(f"Published agent.emotion.delta for chat_id={act.chat_id}: {act.model_dump()}")
                    continue

                subject = action_decision_subject(act.source_channel, act.chat_id)
                envelope = {
                    "id": req.event_id,
                    "subject": subject,
                    "source": "cognitive_service",
                    "payload": act.model_dump(),
                }
                await nc.publish(subject, json.dumps(envelope).encode())
                actions_count += 1
                logger.info(f"Published Stream Sentence Chunk: '{act.text_content}' to chat_id={act.chat_id}")

            # Post Stat & Mood update to companion_service (:8096)
            try:
                import time
                import httpx
                companion_url = get_config_val("infrastructure.companion_url", "http://127.0.0.1:8096")
                is_proactive = (getattr(req, "trigger_type", "") == "proactive")
                today_str = time.strftime("%Y-%m-%d")
                emo_tag = (getattr(req, "current_emotion", "") or "HAPPY").upper()
                stat_body = {
                    "chat_id": req.chat_id,
                    "date": today_str,
                    "mood_score": 0.5,
                    "emotion_tag": emo_tag,
                    "is_proactive": is_proactive,
                }
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.post(f"{companion_url}/api/companion/stat", json=stat_body)
                    logger.info(f"📊 Companion stat recorded for chat_id={req.chat_id} (date={today_str}, tag={emo_tag}, proactive={is_proactive})")
            except Exception as e:
                logger.debug(f"Failed to post companion stat update: {e}")

            # Publish Reasoning Completed
            completed_envelope = {
                "id": req.event_id,
                "subject": SUBJECT_REASONING_COMPLETED,
                "source": "cognitive_service",
                "payload": {
                    "chat_id": req.chat_id,
                    "has_action": actions_count > 0,
                },
            }
            await nc.publish(SUBJECT_REASONING_COMPLETED, json.dumps(completed_envelope).encode())

        except asyncio.CancelledError:
            logger.info(f"⚡ Streaming reasoning task for chat_id={req.chat_id} caught CancelledError & terminated cleanly.")
        except Exception as e:
            logger.error(f"Error in streaming reasoning task: {e}", exc_info=True)
            if req.chat_id:
                # `req` is a real ReasoningRequestPayload instance, so
                # getattr always finds the source_channel attribute (never
                # falls to the "web" default below) -- but the attribute
                # itself can be None (Optional field, not reachable through
                # the normal Go->memory_hub pipeline today, but reachable
                # from any direct/test construction). ActionDecisionPayload's
                # source_channel is a non-optional str, so passing None
                # through would raise a pydantic ValidationError right here
                # in the error handler, silently losing this apology message.
                fallback_act = ActionDecisionPayload(
                    event_id=req.event_id,
                    source_component="cognitive_service",
                    chat_id=req.chat_id,
                    generation_id=getattr(req, "generation_id", 1),
                    source_channel=getattr(req, "source_channel", "web") or "web",
                    action_type="send_message",
                    text_content="呜……人家的大脑突然打了个瞌睡，主人能不能过一会儿再理我一次？",
                    chat_action="typing",
                    is_final=True,
                )
                subject = action_decision_subject(fallback_act.source_channel, fallback_act.chat_id)
                err_envelope = {
                    "id": req.event_id,
                    "subject": subject,
                    "source": "cognitive_service",
                    "payload": fallback_act.model_dump(),
                }
                await nc.publish(subject, json.dumps(err_envelope).encode())
        finally:
            # 🧹 Always clean up task entry from active_tasks dictionary
            active_tasks.pop(req.chat_id, None)

    async def reasoning_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            req = ReasoningRequestPayload(**payload_dict)

            logger.info(f"Processing Stream ReasoningRequest for chat_id={req.chat_id} (gen_id={getattr(req, 'generation_id', 1)})")

            # Cancel any previous task for the same chat_id
            cancel_chat_stream(req.chat_id)

            cancel_event = asyncio.Event()
            task = asyncio.create_task(run_streaming_reasoning(req, cancel_event))
            active_tasks[req.chat_id] = (task, cancel_event)

        except Exception as e:
            logger.error(f"Error handling reasoning request initialization: {e}", exc_info=True)

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

    async def persona_update_handler(msg):
        await PersonaLoader.handle_persona_update(msg.data)

    await nc.subscribe(SUBJECT_REASONING_REQUEST, queue="cognitive_workers", cb=reasoning_handler)
    await nc.subscribe(SUBJECT_VISION_FRAME, cb=vision_handler)
    await nc.subscribe(SUBJECT_STREAM_CANCEL_REQ, cb=cancel_handler)
    await nc.subscribe(SUBJECT_USER_INTERRUPT, cb=cancel_handler)
    await nc.subscribe("agent.persona.update", cb=persona_update_handler)

    async def presenter_sweep_loop():
        """Sweep idle MCP presenter sessions every 60 seconds."""
        while True:
            await asyncio.sleep(60)
            try:
                await engine.presenter_manager.sweep_idle()
            except Exception as e:
                logger.warning(f"Error in presenter sweep loop: {e}")

    asyncio.create_task(presenter_sweep_loop())

    logger.info("Cognitive service listening on NATS subjects (Stream Reasoning, Vision & Cancel Controls)...")
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
