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
    return text.strip()


class GPTSoVITSClient:
    """
    GPT-SoVITS TTS Async Client connecting to local FastAPI (http://127.0.0.1:9880/tts).
    Features zero-dependency urllib streaming and automatic Audio Loudness Normalization.
    """

    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or get_config_val("tts.gpt_sovits.endpoint", "http://127.0.0.1:9880/tts")
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
            "text_split_method": "cut0",
            "batch_size": 1,
            "speed_factor": 1.0,
            "streaming_mode": True,
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
                        try:
                            async for chunk in resp.content.iter_chunked(4096):
                                if cancel_event and cancel_event.is_set():
                                    break
                                if chunk:
                                    pcm_buffer.extend(chunk)
                                    usable_bytes = (len(pcm_buffer) // 2) * 2
                                    if usable_bytes > 0:
                                        frame_bytes = bytes(pcm_buffer[:usable_bytes])
                                        pcm_buffer = pcm_buffer[usable_bytes:]
                                        wav_chunk = add_wav_header(frame_bytes, sample_rate=self.sample_rate)
                                        yield (wav_chunk, "wav")
                        except (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError) as spe:
                            logger.debug(f"GPT-SoVITS HTTP stream completed with connection close: {spe}")

                        if pcm_buffer and len(pcm_buffer) >= 2:
                            usable_bytes = (len(pcm_buffer) // 2) * 2
                            frame_bytes = bytes(pcm_buffer[:usable_bytes])
                            wav_chunk = add_wav_header(frame_bytes, sample_rate=self.sample_rate)
                            yield (wav_chunk, "wav")
                        return
        except ModuleNotFoundError:
            # Fallback to standard library urllib.request
            try:
                def _stream_sync():
                    req = urllib.request.Request(
                        self.endpoint,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    chunks = []
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        while True:
                            chunk = resp.read(4096)
                            if not chunk:
                                break
                            chunks.append(chunk)
                    return chunks

                raw_chunks = await asyncio.to_thread(_stream_sync)
                pcm_buffer = bytearray()
                for chunk in raw_chunks:
                    if cancel_event and cancel_event.is_set():
                        break
                    pcm_buffer.extend(chunk)
                    usable_bytes = (len(pcm_buffer) // 2) * 2
                    if usable_bytes > 0:
                        frame_bytes = bytes(pcm_buffer[:usable_bytes])
                        pcm_buffer = pcm_buffer[usable_bytes:]
                        wav_chunk = add_wav_header(frame_bytes, sample_rate=self.sample_rate)
                        yield (wav_chunk, "wav")

                if pcm_buffer and len(pcm_buffer) >= 2:
                    usable_bytes = (len(pcm_buffer) // 2) * 2
                    frame_bytes = bytes(pcm_buffer[:usable_bytes])
                    wav_chunk = add_wav_header(frame_bytes, sample_rate=self.sample_rate)
                    yield (wav_chunk, "wav")
                return
            except Exception as err:
                logger.warning(f"GPT-SoVITS urllib request failed: {err}")
        except Exception as err:
            logger.warning(f"GPT-SoVITS API endpoint ({self.endpoint}) unreachable ({err}).")

        # Fallback to CosyVoiceClient or synthetic tone if offline
        from services.tts.cosyvoice_client import CosyVoiceClient
        cosy_client = CosyVoiceClient()
        async for chunk, fmt in cosy_client.synthesize_stream(text, cancel_event=cancel_event):
            yield (chunk, fmt)
