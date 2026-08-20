import os
import math
import json
import struct
import base64
import logging
import asyncio
import urllib.request
from typing import Optional, Tuple, AsyncGenerator, Any
from shared.config_loader import get_config_val

logger = logging.getLogger("cosyvoice_client")


class CosyVoiceClient:
    """
    CosyVoice TTS Client connecting to local FastAPI (http://localhost:50000/tts)
    or DashScope cloud API. Fallbacks gracefully to lightweight synthetic PCM if offline.
    """

    def __init__(self, endpoint: Optional[str] = None, prompt_voice: Optional[str] = None):
        self.endpoint = endpoint or get_config_val("tts.cosyvoice.endpoint", "http://localhost:50000/tts")

        # Load voice from PersonaLoader if available
        persona_voice = None
        try:
            from shared.persona_loader import PersonaLoader
            p_data = PersonaLoader.load_active_persona()
            if isinstance(p_data.get("tts"), dict):
                persona_voice = p_data["tts"].get("voice_id")
        except Exception:
            pass

        self.voice = prompt_voice or persona_voice or get_config_val("tts.cosyvoice.voice", "default")
        self.sample_rate = get_config_val("tts.cosyvoice.sample_rate", 24000)

    async def synthesize_stream(self, text: str, cancel_event: Optional[Any] = None) -> AsyncGenerator[Tuple[bytes, str], None]:
        if not text or not text.strip():
            return

        payload = {
            "text": text,
            "voice": self.voice,
            "sample_rate": self.sample_rate,
            "stream": True,
        }

        # Attempt HTTP API call to CosyVoice FastAPI server
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=15.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.endpoint, json=payload) as resp:
                    if resp.status == 200:
                        async for chunk in resp.content.iter_chunked(4096):
                            if cancel_event and cancel_event.is_set():
                                logger.info(f"⚡ CosyVoice HTTP stream cancelled for text: '{text[:10]}...'")
                                break
                            if chunk:
                                yield (chunk, "pcm")
                        return
                    else:
                        logger.warning(f"CosyVoice API returned non-200 status: {resp.status}")
        except ModuleNotFoundError:
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
                for chunk in raw_chunks:
                    if cancel_event and cancel_event.is_set():
                        break
                    yield (chunk, "pcm")
                return
            except Exception as err:
                logger.debug(f"CosyVoice urllib request failed: {err}")
        except Exception as err:
            logger.debug(f"CosyVoice HTTP endpoint ({self.endpoint}) offline/unreachable ({err}). Using synthetic fallback PCM.")

        # Fallback: Generate lightweight synthetic PCM audio chunk for development testing
        if cancel_event and cancel_event.is_set():
            return

        pcm_bytes = self._generate_synthetic_pcm(text)
        yield (pcm_bytes, "pcm")

    def _generate_synthetic_pcm(self, text: str) -> bytes:
        """
        Generates 24000Hz 16-bit mono PCM sine wave tone corresponding to text length.
        Allows full system testing even when local GPU CosyVoice FastAPI server is not started.
        """
        char_count = len(text.strip())
        duration_sec = max(0.4, min(3.0, char_count * 0.15))
        sample_rate = self.sample_rate
        freq = 440.0  # 440 Hz A4 tone

        num_samples = int(sample_rate * duration_sec)
        pcm_data = bytearray()

        for i in range(num_samples):
            t = float(i) / float(sample_rate)
            # Apply smooth attack & decay envelope
            envelope = math.sin(math.pi * (i / num_samples))
            sample = int(16384 * envelope * math.sin(2 * math.pi * freq * t))
            pcm_data.extend(struct.pack("<h", sample))

        return bytes(pcm_data)
