import os
import json
import logging
import asyncio
import urllib.request
import numpy as np
from typing import Optional, Tuple, AsyncGenerator, Any
from shared.config_loader import get_config_val
from shared.persona_loader import PersonaLoader
from services.tts.audio_normalizer import AudioNormalizer, add_wav_header

logger = logging.getLogger("gpt_sovits_client")


import re

def sanitize_text_for_tts(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\.{2,}", "，", text)
    text = re.sub(r"…+", "，", text)
    text = re.sub(r"！{2,}", "！", text)
    text = re.sub(r"？{2,}", "？", text)
    text = re.sub(r"，{2,}", "，", text)
    # "PresenterControlTool" -> "Presenter Control Tool": GPT-SoVITS's zh-mode
    # embedded-English handling looks up recognizable English words fine, but
    # an unbroken CamelCase compound isn't one -- that mismatch is the
    # concrete trigger behind a mid-stream truncation bug (HTTP stream
    # aborted while emitting "PresenterC..."). text_lang="auto" was tried as a
    # broader fix and reverted: it caused GPT-SoVITS to misdetect Chinese
    # segments (helped by the ja reference audio) as Japanese and read hanzi
    # with Japanese on'yomi/kun'yomi instead of Mandarin pinyin.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return text.strip()


class GPTSoVITSClient:
    """
    GPT-SoVITS TTS Async Client connecting to local FastAPI (http://127.0.0.1:9888/tts).
    Features zero-dependency urllib streaming and automatic Audio Loudness Normalization.
    """

    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or get_config_val("tts.gpt_sovits.endpoint", "http://127.0.0.1:9888/tts")

        self.sample_rate = get_config_val("tts.gpt_sovits.sample_rate", 32000)

    async def synthesize_stream(
        self, text: str, cancel_event: Optional[Any] = None
    ) -> AsyncGenerator[Tuple[bytes, str], None]:
        if not text or not text.strip():
            return

        text = sanitize_text_for_tts(text)

        persona_data = PersonaLoader.load_active_persona()
        tts_cfg = persona_data.get("tts", {})
        prompt_audio = tts_cfg.get("prompt_audio", "config/audio/vocal_patra3.wav_10.wav")
        prompt_text = tts_cfg.get("prompt_text", "こんばんわんわん、パトラの可愛いワンちゃんたち！")
        prompt_lang = tts_cfg.get("prompt_lang", "ja")
        text_lang = tts_cfg.get("text_lang", "zh")

        payload = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": os.path.abspath(prompt_audio),
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 0.7,
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": 1.0,
            "streaming_mode": 1,
            "media_type": "raw",
        }

        # Try aiohttp first if installed, fallback to urllib.request
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=30.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.endpoint, json=payload) as resp:
                    if resp.status == 200:
                        pcm_buffer = bytearray()
                        min_chunk_bytes = 12800  # ~200ms of 32kHz 16-bit mono PCM (6400 samples)
                        try:
                            async for chunk in resp.content.iter_chunked(4096):
                                if cancel_event and cancel_event.is_set():
                                    break
                                if chunk:
                                    pcm_buffer.extend(chunk)
                                    if len(pcm_buffer) >= min_chunk_bytes:
                                        usable_bytes = (len(pcm_buffer) // 2) * 2
                                        frame_bytes = bytes(pcm_buffer[:usable_bytes])
                                        pcm_buffer = pcm_buffer[usable_bytes:]
                                        yield (frame_bytes, "pcm")
                        except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError) as spe:
                            logger.warning(
                                f"GPT-SoVITS HTTP stream for chat text {text[:30]!r}... ended early ({spe}); "
                                "rest of this sentence's audio was not synthesized."
                            )

                        if pcm_buffer and len(pcm_buffer) >= 2:
                            usable_bytes = (len(pcm_buffer) // 2) * 2
                            frame_bytes = bytes(pcm_buffer[:usable_bytes])
                            pcm_buffer.clear()
                            yield (frame_bytes, "pcm")
                        return
        except ModuleNotFoundError:
            # Fallback to standard library urllib.request with async queue for true chunk-by-chunk streaming
            try:
                loop = asyncio.get_running_loop()
                chunk_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

                def _stream_reader():
                    try:
                        req = urllib.request.Request(
                            self.endpoint,
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            while True:
                                if cancel_event and cancel_event.is_set():
                                    break
                                chunk = resp.read(4096)
                                if not chunk:
                                    break
                                loop.call_soon_threadsafe(chunk_queue.put_nowait, chunk)
                    except Exception as err:
                        logger.warning(f"GPT-SoVITS urllib stream reader encountered error: {err}")
                    finally:
                        loop.call_soon_threadsafe(chunk_queue.put_nowait, None)

                asyncio.create_task(asyncio.to_thread(_stream_reader))

                pcm_buffer = bytearray()
                min_chunk_bytes = 12800  # ~200ms of 32kHz 16-bit mono PCM (6400 samples)
                while True:
                    chunk = await chunk_queue.get()
                    if chunk is None:
                        break
                    if cancel_event and cancel_event.is_set():
                        break
                    pcm_buffer.extend(chunk)
                    if len(pcm_buffer) >= min_chunk_bytes:
                        usable_bytes = (len(pcm_buffer) // 2) * 2
                        frame_bytes = bytes(pcm_buffer[:usable_bytes])
                        pcm_buffer = pcm_buffer[usable_bytes:]
                        yield (frame_bytes, "pcm")

                if pcm_buffer and len(pcm_buffer) >= 2:
                    usable_bytes = (len(pcm_buffer) // 2) * 2
                    frame_bytes = bytes(pcm_buffer[:usable_bytes])
                    pcm_buffer.clear()
                    yield (frame_bytes, "pcm")
                return
            except Exception as err:
                logger.error(f"Error in urllib fallback streaming: {err}")
        except Exception as err:
            logger.warning(f"GPT-SoVITS API endpoint ({self.endpoint}) unreachable ({err}).")

        # Fallback to CosyVoiceClient or synthetic tone if offline
        from services.tts.cosyvoice_client import CosyVoiceClient
        cosy_client = CosyVoiceClient()
        async for chunk, fmt in cosy_client.synthesize_stream(text, cancel_event=cancel_event):
            yield (chunk, fmt)
