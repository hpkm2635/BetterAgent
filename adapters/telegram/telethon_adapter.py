import asyncio
import json
import logging
import os
import nats
from telethon import TelegramClient, events
from shared.subjects import (
    SUBJECT_INBOUND_MESSAGE,
    SUBJECT_ENRICH_CONTEXT_REQ,
    SUBJECT_REASONING_REQUEST,
    SUBJECT_ACTION_DECISION,
    SUBJECT_ACTION_COMPLETED,
)
from shared.schema.payloads import (
    InboundMessagePayload,
    EnrichContextReqPayload,
    ActionDecisionPayload,
    ActionCompletedPayload,
)
from shared.logger import setup_logger

logger = setup_logger("telethon_adapter")

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_NAME = "meowclient"


async def main():
    nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
    nc = None
    try:
        nc = await nats.connect(nats_url, connect_timeout=1, max_reconnect_attempts=1)
        logger.info(f"Connected to NATS at {nats_url}")
    except Exception as e:
        logger.warning(f"NATS Connection warning (offline bus mode): NATS server not reachable at {nats_url}")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    me = await client.get_me()
    logger.info(
        f"Telethon User Account Authorized: {me.first_name} (@{me.username}) ID: {me.id}"
    )

    # Listen to ActionDecision from NATS
    async def action_decision_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            action = ActionDecisionPayload(**payload_dict)

            logger.info(
                f"ActionDecision received: {action.action_type} for chat_id={action.chat_id}"
            )

            # Humanization typing delay
            if action.typing_delay > 0:
                await asyncio.sleep(action.typing_delay)

            sent_msg = None
            if action.text_content:
                sent_msg = await client.send_message(
                    action.chat_id, action.text_content)
                logger.info(
                    f"Sent text reply to {action.chat_id}: {action.text_content}"
                )

            if action.voice_path and os.path.exists(action.voice_path):
                sent_msg = await client.send_file(action.chat_id,
                                                  action.voice_path,
                                                  voice_note=True)
                logger.info(f"Sent voice reply to {action.chat_id}")

            if action.photo_path and os.path.exists(action.photo_path):
                sent_msg = await client.send_file(action.chat_id,
                                                  action.photo_path)
                logger.info(f"Sent photo reply to {action.chat_id}")

            # Publish ActionCompleted
            completed = ActionCompletedPayload(
                event_id=action.event_id,
                source_component="telethon_adapter",
                chat_id=action.chat_id,
                sent_message_id=sent_msg.id if sent_msg else None,
                action_decision=action,
                status="success",
            )
            envelope = {
                "id": action.event_id,
                "subject": SUBJECT_ACTION_COMPLETED,
                "source": "telethon_adapter",
                "payload": completed.model_dump(),
            }
            await nc.publish(SUBJECT_ACTION_COMPLETED,
                             json.dumps(envelope).encode())

        except Exception as e:
            logger.error(f"Error handling ActionDecision: {e}")

    await nc.subscribe(SUBJECT_ACTION_DECISION, cb=action_decision_handler) if nc else None

    # Listen to inbound private messages from Telegram
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def inbound_message_handler(event):
        chat = await event.get_chat()
        sender = await event.get_sender()
        text = event.raw_text or ""

        logger.info(f"Incoming Telegram Message from {sender.first_name}: {text}")

        inbound_payload = InboundMessagePayload(
            event_id=str(event.id),
            source_component="telethon_adapter",
            chat_id=event.chat_id,
            user_id=sender.id,
            message_id=event.id,
            raw_text=text,
            chat_type="private",
            sender_username=sender.username,
            sender_first_name=sender.first_name or "",
            sender_last_name=sender.last_name,
            sender_display_name=f"{sender.first_name} {sender.last_name or ''}".strip(
            ),
        )

        # Publish inbound message to NATS if online
        if nc:
            env = {
                "id": str(event.id),
                "subject": SUBJECT_INBOUND_MESSAGE,
                "source": "telethon_adapter",
                "payload": inbound_payload.model_dump(),
            }
            await nc.publish(SUBJECT_INBOUND_MESSAGE, json.dumps(env).encode())

            # Perform sync context enrichment request over NATS
            enrich_req = EnrichContextReqPayload(
                event_id=str(event.id),
                source_component="telethon_adapter",
                chat_id=event.chat_id,
                user_id=sender.id,
                inbound_message=inbound_payload,
                current_state="IDLE",
                trigger_type="user_message",
            )

            try:
                resp = await nc.request(
                    SUBJECT_ENRICH_CONTEXT_REQ,
                    json.dumps({
                        "id": str(event.id),
                        "subject": SUBJECT_ENRICH_CONTEXT_REQ,
                        "source": "telethon_adapter",
                        "payload": enrich_req.model_dump(),
                    }).encode(),
                    timeout=5.0,
                )
                resp_data = json.loads(resp.data.decode())
                reasoning_req = resp_data.get("payload", {})

                # Publish ReasoningRequest to cognitive service
                reasoning_env = {
                    "id": str(event.id),
                    "subject": SUBJECT_REASONING_REQUEST,
                    "source": "telethon_adapter",
                    "payload": reasoning_req,
                }
                await nc.publish(SUBJECT_REASONING_REQUEST,
                                 json.dumps(reasoning_env).encode())
                logger.info("Dispatched ReasoningRequest to cognitive service")
            except Exception as e:
                logger.error(f"EnrichContext request timeout/failed: {e}")
        else:
            logger.info("NATS offline: message received (waiting for microservices & NATS bus)")

    logger.info("TelethonAdapter started & listening to Telegram messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
