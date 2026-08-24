"""
Thin async client for iFLYTEK streaming speech-to-text (WebAPI).

Protocol reference: https://doc.xfyun.cn/rest_api/语音听写（流式版）.html
Mirrors the official demo's frame format but adapts it to the same
async session interface used by FunASRSession so services/stt/main.py
can switch providers without changing its NATS bridge logic.
"""
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from time import mktime
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import urlencode, urlparse
from wsgiref.handlers import format_date_time

import websockets

logger = logging.getLogger("iflytek_client")

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


class IFlytekSession:
    """
    One streaming ASR session over one iFLYTEK WebSocket connection, scoped to
    a single utterance (VAD speech_start -> speech_end).
    """

    def __init__(
        self,
        app_id: str,
        api_key: str,
        api_secret: str,
        endpoint: str = "wss://iat-api.xfyun.cn/v2/iat",
        sample_rate: int = 16000,
        language: str = "zh_cn",
        accent: str = "mandarin",
    ):
        self._app_id = app_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._endpoint = endpoint
        self._sample_rate = sample_rate
        self._language = language
        self._accent = accent
        self._ws = None
        self._started = False
        self._finished = False
        self._status = STATUS_FIRST_FRAME

    def _create_url(self) -> str:
        """Build the authenticated WebSocket URL (HMAC-SHA256)."""
        host = "iat-api.xfyun.cn"
        path = "/v2/iat"
        # iFLYTEK docs require GET /v2/iat with RFC1123 date. Use local time
        # to match the official demo; the server validates the signature against
        # the provided date, not a fixed timezone.
        date = format_date_time(mktime(datetime.now().timetuple()))
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature_sha = hmac.new(
            self._api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature = base64.b64encode(signature_sha).decode("utf-8")
        authorization_origin = (
            f'api_key="{self._api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        params = {
            "authorization": authorization,
            "date": date,
            "host": host,
        }
        return f"{self._endpoint}?{urlencode(params)}"

    def _build_frame(self, audio_b64: str, status: int) -> Dict[str, Any]:
        # iFLYTEK WebAPI v2 expects a different frame shape than the v1 protocol:
        # {common, business, data} instead of {header, parameter, payload}.
        # Reference: official iFLYTEK streaming ASR WebAPI v2 docs.
        if status == STATUS_FIRST_FRAME:
            return {
                "common": {
                    "app_id": self._app_id,
                },
                "business": {
                    "language": self._language,
                    "accent": self._accent,
                    "domain": "slm",
                    "dwa": "wpgs",
                },
                "data": {
                    "status": status,
                    "format": f"audio/L16;rate={self._sample_rate}",
                    "encoding": "raw",
                    "audio": audio_b64,
                },
            }
        return {
            "data": {
                "status": status,
                "format": f"audio/L16;rate={self._sample_rate}",
                "encoding": "raw",
                "audio": audio_b64,
            },
        }

    async def start(self) -> None:
        url = self._create_url()
        self._ws = await websockets.connect(url, max_size=None)
        self._started = True
        self._status = STATUS_FIRST_FRAME

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("iFLYTEK session not started")
        audio_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
        frame = self._build_frame(audio_b64, self._status)
        logger.info(f"iFLYTEK send_audio status={self._status} len={len(pcm_bytes)}")
        await self._ws.send(json.dumps(frame))
        # After the first real audio frame we are in "continue" mode.
        if self._status == STATUS_FIRST_FRAME:
            self._status = STATUS_CONTINUE_FRAME

    async def finish(self) -> None:
        if self._ws is None or self._finished:
            return
        self._finished = True
        # Send an empty last frame to flush the final result.
        last_frame = self._build_frame("", STATUS_LAST_FRAME)
        await self._ws.send(json.dumps(last_frame))

    async def results(self) -> AsyncGenerator[Dict[str, Any], None]:
        if self._ws is None:
            return
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            # v2 errors are top-level, not nested under header.
            code = msg.get("code")
            if code is not None and code != 0:
                logger.warning(f"iFLYTEK STT error: code={code}, message={msg.get('message')}, raw_msg={raw}")
                break

            logger.info(f"iFLYTEK raw response: {raw}")

            # v2 successful responses nest the result under data.result.
            payload = msg.get("data") or msg.get("payload")
            if not payload:
                continue

            result = payload.get("result", {}) or {}
            text_b64 = result.get("text") if isinstance(result, dict) else None
            if not text_b64:
                continue

            try:
                decoded = json.loads(base64.b64decode(text_b64).decode("utf-8"))
            except Exception as e:
                logger.warning(f"Failed to decode iFLYTEK result text: {e}")
                continue

            words = decoded.get("ws", [])
            transcript = "".join(
                cw.get("w", "") for item in words for cw in item.get("cw", [])
            )
            if not transcript:
                continue

            # status == 2 from iFLYTEK means the final result for this utterance.
            is_final = status == 2
            yield {"text": transcript, "is_final": is_final}

            if is_final:
                break

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"Error closing iFLYTEK session: {e}")
            self._ws = None
