"""Async iFLYTEK streaming STT client (WebAPI v1 / iat.xf-yun.com).

Mirrors the official iFLYTEK Python demo frame format:
  header / parameter / payload
and adapts it to the same async session interface used by FunASRSession.
"""
import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime
from time import mktime
from typing import Any, AsyncGenerator, Dict
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

import websockets

logger = logging.getLogger("iflytek_client")

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


class IFlytekSession:
    """One streaming ASR session over iFLYTEK WebAPI v1, scoped to one utterance."""

    def __init__(
        self,
        app_id: str,
        api_key: str,
        api_secret: str,
        endpoint: str = "ws://iat.xf-yun.com/v1",
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
        """Build the authenticated WebSocket URL (HMAC-SHA256) for v1."""
        host = "iat.xf-yun.com"
        path = "/v1"
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
        """Build a v1 request frame (header + parameter + payload)."""
        iat_params = {
            "domain": "slm",
            "language": self._language,
            "accent": self._accent,
                        "result": {
                "encoding": "utf8",
                "compress": "raw",
                "format": "plain",
            },
        }
        return {
            "header": {
                "app_id": self._app_id,
                "status": status,
            },
            "parameter": {
                "iat": iat_params,
            },
            "payload": {
                "audio": {
                    "encoding": "raw",
                    "status": status,
                    "audio": audio_b64,
                },
            },
        }

    async def start(self) -> None:
        url = self._create_url()
        logger.info(f"iFLYTEK v1 connecting to {url[:120]}...")
        self._ws = await websockets.connect(url, max_size=None)
        self._started = True
        self._status = STATUS_FIRST_FRAME
        logger.info("iFLYTEK v1 connected")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("iFLYTEK session not started")
        audio_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
        frame = self._build_frame(audio_b64, self._status)
        logger.info(f"iFLYTEK send_audio status={self._status} len={len(pcm_bytes)}")
        await self._ws.send(json.dumps(frame))
        if self._status == STATUS_FIRST_FRAME:
            self._status = STATUS_CONTINUE_FRAME

    async def finish(self) -> None:
        if self._ws is None or self._finished:
            return
        self._finished = True
        last_frame = self._build_frame("", STATUS_LAST_FRAME)
        await self._ws.send(json.dumps(last_frame))
        logger.info("iFLYTEK sent last frame (status=2)")

    async def results(self) -> AsyncGenerator[Dict[str, Any], None]:
        if self._ws is None:
            return
        # Accumulate words across partial results. iFLYTEK sends the full words
        # in each partial frame, but the final frame may only contain punctuation,
        # so we keep the last partial words and append the final punctuation.
        accumulated_words: list[str] = []
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            header = msg.get("header") or {}
            code = header.get("code")
            if code is not None and code != 0:
                logger.warning(f"iFLYTEK STT error: code={code}, message={header.get('message')}, raw_msg={raw}")
                break

            logger.info(f"iFLYTEK raw response: {raw}")

            payload = msg.get("payload")
            if not payload:
                continue

            result = payload.get("result") or {}
            text_b64 = result.get("text")
            if not text_b64:
                continue

            try:
                decoded = json.loads(base64.b64decode(text_b64).decode("utf-8"))
            except Exception as e:
                logger.warning(f"Failed to decode iFLYTEK result text: {e}")
                continue

            words = decoded.get("ws", [])
            frame_text = "".join(
                cw.get("w", "") for item in words for cw in item.get("cw", [])
            )
            if not frame_text:
                continue

            status = header.get("status")
            is_final = status == 2
            if is_final:
                # Append final punctuation/words to the accumulated partial words.
                accumulated_words.extend(words)
            else:
                accumulated_words = words

            transcript = "".join(
                cw.get("w", "") for item in accumulated_words for cw in item.get("cw", [])
            )
            logger.info(f"iFLYTEK transcript ({'final' if is_final else 'partial'}): {transcript}")
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
