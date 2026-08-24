import asyncio
import base64
import json
import math
import os
import struct
import nats
from dotenv import load_dotenv

load_dotenv()

NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
NATS_USER = os.getenv("NATS_USER")
NATS_PASSWORD = os.getenv("NATS_PASSWORD")
CHAT_ID = 123456789

SAMPLE_RATE = 16000
DURATION_SECONDS = 3
FREQUENCY = 440
CHUNK_SIZE = 1024


def generate_pcm() -> bytes:
    """Generate a 3-second 16kHz 16-bit PCM sine wave."""
    samples = []
    for i in range(SAMPLE_RATE * DURATION_SECONDS):
        value = int(math.sin(2 * math.pi * FREQUENCY * i / SAMPLE_RATE) * 0.5 * 32767)
        samples.append(value)
    return struct.pack(f"<{len(samples)}h", *samples)


def speech_start_payload(chat_id: int) -> dict:
    return {
        "id": "test-event-1",
        "subject": "agent.speech.start",
        "source": "test_script",
        "payload": {
            "chat_id": chat_id,
            "generation_id": 1,
        },
    }


def stream_chunk_payload(chat_id: int, audio_bytes: bytes) -> dict:
    return {
        "id": "test-event-2",
        "subject": "agent.stt.stream_chunk",
        "source": "test_script",
        "payload": {
            "chat_id": chat_id,
            "generation_id": 1,
            "source_channel": "test",
            "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
            "sample_rate": SAMPLE_RATE,
            "format": "pcm",
        },
    }


def speech_end_payload(chat_id: int) -> dict:
    return {
        "id": "test-event-3",
        "subject": "agent.speech.end",
        "source": "test_script",
        "payload": {
            "chat_id": chat_id,
            "generation_id": 1,
        },
    }


async def main():
    nc = await nats.connect(NATS_URL, user=NATS_USER, password=NATS_PASSWORD)
    print(f"Connected to NATS at {NATS_URL}")

    # Subscribe to final transcripts
    async def on_final(msg):
        try:
            data = json.loads(msg.data.decode())
            print("[FINAL]", json.dumps(data, ensure_ascii=False))
        except Exception as e:
            print("[ERROR parsing final]", e)

    async def on_partial(msg):
        try:
            data = json.loads(msg.data.decode())
            print("[PARTIAL]", json.dumps(data, ensure_ascii=False))
        except Exception as e:
            print("[ERROR parsing partial]", e)

    await nc.subscribe("agent.stt.stream.final", cb=on_final)
    await nc.subscribe("agent.stt.stream.partial", cb=on_partial)

    pcm = generate_pcm()
    print(f"Generated {len(pcm)} bytes of PCM audio")

    # Send speech_start
    await nc.publish("agent.speech.start", json.dumps(speech_start_payload(CHAT_ID)).encode())
    print("Sent speech_start")

    # Send audio chunks
    for i in range(0, len(pcm), CHUNK_SIZE):
        chunk = pcm[i : i + CHUNK_SIZE]
        payload = stream_chunk_payload(CHAT_ID, chunk)
        await nc.publish("agent.stt.stream_chunk", json.dumps(payload).encode())
        await asyncio.sleep(0.04)

    print("Sent audio chunks")

    # Send speech_end
    await nc.publish("agent.speech.end", json.dumps(speech_end_payload(CHAT_ID)).encode())
    print("Sent speech_end")

    # Wait for results
    print("Waiting 10 seconds for STT results...")
    await asyncio.sleep(10)
    await nc.close()


if __name__ == "__main__":
    asyncio.run(main())
