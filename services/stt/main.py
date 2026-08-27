import asyncio
import base64
import json
import logging
import os
import nats
from dotenv import load_dotenv
from typing import Any

from shared.subjects import (
    SUBJECT_SPEECH_START,
    SUBJECT_SPEECH_END,
    SUBJECT_STT_STREAM_CHUNK,
    SUBJECT_STT_STREAM_PARTIAL,
    SUBJECT_STT_STREAM_FINAL,
)
from shared.schema.payloads import STTTranscriptPayload
from shared.logger import setup_logger
from shared.config_loader import get_config_val
from services.stt.funasr_client import FunASRSession
from services.stt.iflytek_client import IFlytekSession

load_dotenv()
logger = setup_logger("stt_service")

# If FunASR never sends another result (hung connection, server crash mid-
# utterance), don't leak the session/task forever -- matches the watchdog
# pattern used throughout this codebase (Go's StreamingSTTTimeoutDuration is
# 30s for the same state; kept in step here).
RESULT_TIMEOUT_SECONDS = 30.0


async def error_cb(e):
    logger.warning(f"NATS Connection event in STT service: {e}")


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
        logger.info(f"STT Service connected to NATS at {nats_url}")
    except Exception as e:
        logger.warning(f"Failed to connect to NATS ({e}). STT Service exiting gracefully.")
        return

    provider = os.getenv("STT_PROVIDER", get_config_val("stt.provider", "funasr")).lower().strip()
    logger.info(f"STT provider configured: {provider}")

    if provider == "iflytek":
        iflytek_app_id = os.getenv("IFLYTEK_APPID")
        iflytek_api_key = os.getenv("IFLYTEK_APIKEY")
        iflytek_api_secret = os.getenv("IFLYTEK_APISECRET")
        if not (iflytek_app_id and iflytek_api_key and iflytek_api_secret):
            logger.warning("STT_PROVIDER=iflytek but IFLYTEK_APPID / IFLYTEK_APIKEY / IFLYTEK_APISECRET are not all set. Falling back to funasr.")
            provider = "funasr"
    elif provider != "funasr":
        logger.error(f"Unknown STT_PROVIDER '{provider}', falling back to funasr")
        provider = "funasr"

    sessions: dict[int, Any] = {}
    result_tasks: dict[int, asyncio.Task] = {}

    async def publish_transcript(subject: str, chat_id: int, text: str):
        payload = STTTranscriptPayload(
            source_component="stt_service",
            chat_id=chat_id,
            text=text,
            source_channel="web",
        )
        envelope = {
            "id": payload.event_id,
            "subject": subject,
            "source": "stt_service",
            "payload": payload.model_dump(),
        }
        await nc.publish(subject, json.dumps(envelope).encode())

    async def drain_results(chat_id: int, session: Any):
        try:
            result_iter = session.results().__aiter__()
            while True:
                try:
                    result = await asyncio.wait_for(result_iter.__anext__(), timeout=RESULT_TIMEOUT_SECONDS)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.warning(f"⏳ STT result timeout for chat_id={chat_id}, closing session")
                    break

                is_final = result["is_final"]
                text = result["text"]


                subject = SUBJECT_STT_STREAM_FINAL if is_final else SUBJECT_STT_STREAM_PARTIAL
                await publish_transcript(subject, chat_id, text)
                logger.info(f"{'\u2705 Final' if is_final else '\u2026Partial'} STT transcript for chat_id={chat_id}: '{text}'")

                if is_final:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error draining STT results for chat_id={chat_id}: {e}", exc_info=True)
        finally:
            await session.close()
            sessions.pop(chat_id, None)
            result_tasks.pop(chat_id, None)
    async def close_existing_session(chat_id: int):
        old_task = result_tasks.pop(chat_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
        old_session = sessions.pop(chat_id, None)
        if old_session:
            await old_session.close()

    async def speech_start_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            chat_id = data.get("payload", {}).get("chat_id", 0)
            if not chat_id:
                return

            # Stale session from a previous (possibly abandoned) utterance --
            # don't let it linger, this one supersedes it.
            await close_existing_session(chat_id)

            if provider == "iflytek":
                session = IFlytekSession(
                    app_id=iflytek_app_id,
                    api_key=iflytek_api_key,
                    api_secret=iflytek_api_secret,
                )
            else:
                session = FunASRSession()
            try:
                await session.start()
            except Exception as e:
                logger.warning(f"Failed to start {provider} STT session for chat_id={chat_id} ({e}); STT unavailable for this utterance.")
                return

            sessions[chat_id] = session
            result_tasks[chat_id] = asyncio.create_task(drain_results(chat_id, session))
            logger.info(f"🎤 {provider} STT session started for chat_id={chat_id}")
        except Exception as e:
            logger.error(f"Error handling speech_start: {e}", exc_info=True)

    async def stream_chunk_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            payload_dict = data.get("payload", {})
            chat_id = payload_dict.get("chat_id", 0)
            audio_b64 = payload_dict.get("audio_base64")
            # Every mic chunk (tens of times a second while listening) hits this
            # handler -- logging each one at INFO floods the log with routine
            # traffic. Debug-only; real problems still surface via the
            # WARNING/ERROR paths elsewhere in this handler and in speech_start.
            logger.debug(f"STT stream_chunk received chat_id={chat_id} audio_len={len(audio_b64) if audio_b64 else 0} session_exists={chat_id in sessions}")
            if not chat_id or not audio_b64:
                return

            session = sessions.get(chat_id)
            if session is None:
                # Chunk arrived before speech_start's STT handshake finished,
                # or after the session already closed -- drop it rather than
                # error; the rest of the utterance still gets through.
                return

            pcm_bytes = base64.b64decode(audio_b64)
            await session.send_audio(pcm_bytes)
        except Exception as e:
            logger.error(f"Error forwarding audio chunk to STT provider: {e}", exc_info=True)

    async def speech_end_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            chat_id = data.get("payload", {}).get("chat_id", 0)
            if not chat_id:
                return

            session = sessions.get(chat_id)
            if session is None:
                return

            await session.finish()
            logger.info(f"🎤 {provider} STT session finishing for chat_id={chat_id}")
        except Exception as e:
            logger.error(f"Error handling speech_end: {e}", exc_info=True)

    await nc.subscribe(SUBJECT_SPEECH_START, cb=speech_start_handler)
    await nc.subscribe(SUBJECT_STT_STREAM_CHUNK, queue="stt_workers", cb=stream_chunk_handler)
    await nc.subscribe(SUBJECT_SPEECH_END, cb=speech_end_handler)

    logger.info("STT Service listening on NATS subjects (Speech Start/End & Audio Chunks)...")
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
