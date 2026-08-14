import asyncio
import json

import pytest
import pytest_asyncio
import websockets

from services.stt.funasr_client import FunASRSession


@pytest_asyncio.fixture
async def fake_funasr_server():
    """
    A minimal stand-in for FunASR's official wss server: accepts the config
    frame, echoes one online (partial) result per audio frame received, and
    replies with an offline (final) result once told is_speaking=False --
    same shape FunASRSession.results() expects to distinguish partial/final.
    """
    received_audio_bytes = {"total": 0}

    async def handler(ws):
        config_raw = await ws.recv()
        config = json.loads(config_raw)
        assert config["is_speaking"] is True
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                received_audio_bytes["total"] += len(msg)
                await ws.send(json.dumps({"mode": "2pass-online", "text": "你好", "wav_name": config["wav_name"]}))
                continue
            data = json.loads(msg)
            if data.get("is_speaking") is False:
                await ws.send(json.dumps({
                    "mode": "2pass-offline",
                    "text": f"你好主人，收到 {received_audio_bytes['total']} 字节音频喵。",
                    "wav_name": config["wav_name"],
                }))
                await ws.close()
                return

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}", received_audio_bytes


@pytest.mark.asyncio
async def test_funasr_session_streams_partial_then_final(fake_funasr_server):
    endpoint, received = fake_funasr_server
    session = FunASRSession(endpoint=endpoint, mode="2pass", sample_rate=16000)
    await session.start()

    await session.send_audio(b"\x00\x01" * 320)
    await session.finish()

    results = []
    async for r in session.results():
        results.append(r)
        if r["is_final"]:
            break

    await session.close()

    assert results[0] == {"text": "你好", "is_final": False}
    assert results[-1]["is_final"] is True
    assert "640" in results[-1]["text"]  # 320 * 2 bytes/sample
    assert received["total"] == 640


@pytest.mark.asyncio
async def test_funasr_session_close_before_start_is_safe():
    session = FunASRSession(endpoint="ws://127.0.0.1:1", mode="2pass", sample_rate=16000)
    await session.close()  # must not raise even though start() was never called


@pytest.mark.asyncio
async def test_funasr_session_send_audio_before_start_raises():
    session = FunASRSession(endpoint="ws://127.0.0.1:1", mode="2pass", sample_rate=16000)
    with pytest.raises(RuntimeError):
        await session.send_audio(b"\x00\x00")
