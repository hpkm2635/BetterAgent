"""
Thin client for FunASR's official streaming WebSocket protocol
(funasr_wss_server.py, part of FunASR's runtime -- not part of this repo,
deployed as its own process the same way GPT-SoVITS is; see
services/tts/gpt_sovits_client.py for that same "bridge an external
inference server" role on the TTS side).

Protocol (per FunASR's public wss server/client reference):
  1. Connect, send one JSON config frame: {"mode", "chunk_size",
     "chunk_interval", "encoder_chunk_look_back", "decoder_chunk_look_back",
     "audio_fs", "wav_name", "wav_format", "is_speaking": true, "hotwords",
     "itn"}.
  2. Stream raw 16-bit PCM binary frames (16kHz mono).
  3. Send {"is_speaking": false} to signal end of utterance.
  4. Server streams back JSON result frames tagged by "mode":
     "*-online" (partial, no punctuation) and "*-offline" (final,
     punctuation-restored).

NOTICE: exact field names/behavior should be verified against whatever
FunASR server version is actually deployed -- this was written against the
publicly documented protocol without a live server to test against.
"""
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

import websockets

from shared.config_loader import get_config_val

logger = logging.getLogger("funasr_client")


class FunASRSession:
    """
    One streaming ASR session over one WebSocket connection, scoped to a
    single utterance (VAD speech_start -> speech_end). Not reused across
    utterances -- services/stt/main.py opens a fresh session per speech_start.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        mode: Optional[str] = None,
        sample_rate: Optional[int] = None,
    ):
        self._endpoint = endpoint or get_config_val("stt.funasr.endpoint", "ws://127.0.0.1:10095")
        self._mode = mode or get_config_val("stt.funasr.mode", "2pass")
        self._sample_rate = sample_rate or get_config_val("stt.funasr.sample_rate", 16000)
        self._ws = None

    async def start(self) -> None:
        self._ws = await websockets.connect(self._endpoint, max_size=None)
        # Field set matches FunASR's official funasr_wss_client.py reference
        # exactly -- encoder/decoder_chunk_look_back and audio_fs have
        # server-side defaults that happen to match what we send anyway, but
        # sending them explicitly rules out "incomplete config frame" as a
        # variable when a session goes silent.
        config = {
            "mode": self._mode,
            "chunk_size": [5, 10, 5],
            "chunk_interval": 10,
            "encoder_chunk_look_back": 4,
            "decoder_chunk_look_back": 0,
            "audio_fs": self._sample_rate,
            "wav_name": "betteragent",
            "wav_format": "pcm",
            "is_speaking": True,
            "hotwords": "",
            "itn": True,
        }
        await self._ws.send(json.dumps(config))

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("FunASR session not started")
        try:
            await self._ws.send(pcm_bytes)
        except websockets.exceptions.ConnectionClosed:
            # Same benign race as iflytek_client.py's send_audio -- the
            # server can close its side as soon as it has sent the final
            # result, racing with the tail end of our own audio/finish
            # frames. Not worth an ERROR-level traceback in the caller.
            logger.debug("FunASR connection already closed while sending audio; ignoring (utterance likely already finished).")

    async def finish(self) -> None:
        """Signals end of utterance -- FunASR replies with the final
        punctuation-restored transcript on the results() stream after this."""
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"is_speaking": False}))
        except websockets.exceptions.ConnectionClosed:
            logger.debug("FunASR connection already closed before finish() could send is_speaking:false; ignoring.")

    async def results(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Yields {"text": str, "is_final": bool} as FunASR streams results."""
        if self._ws is None:
            return
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            # The server only reports errors (e.g. dropped/misconfigured
            # frames) in the end-of-session acknowledgement, which carries
            # no "text" field -- without this check it's silently skipped
            # below and a misbehaving session just looks like a bare timeout.
            error = data.get("error")
            if error:
                logger.warning(f"FunASR reported an error for this session: {error}")

            text = data.get("text", "")
            if not text:
                continue
            mode = data.get("mode", "")
            yield {"text": text, "is_final": mode.endswith("-offline") or mode == "offline"}

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"Error closing FunASR session: {e}")
            self._ws = None
