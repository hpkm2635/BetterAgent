import struct
import logging
import numpy as np

logger = logging.getLogger("audio_normalizer")


class AudioNormalizer:
    """
    Normalizes 16-bit PCM audio samples to target peak volume to prevent audio clipping or quiet output.
    """

    @staticmethod
    def normalize_pcm_16bit(pcm_bytes: bytes, target_peak: float = 0.95) -> bytes:
        if not pcm_bytes or len(pcm_bytes) < 2:
            return pcm_bytes

        try:
            # Interpret 16-bit PCM little-endian samples
            audio_data = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
            max_val = np.max(np.abs(audio_data))

            if max_val <= 0:
                return pcm_bytes

            scale = (target_peak * 32767.0) / max_val
            # Only apply scaling if scaling factor is reasonable (avoid blowing up quiet noise)
            if 0.5 <= scale <= 4.0:
                audio_data = np.clip(audio_data * scale, -32768.0, 32767.0)

            return audio_data.astype(np.int16).tobytes()
        except Exception as err:
            logger.debug(f"Audio normalization skipped: {err}")
            return pcm_bytes


def smooth_pcm_chunk_edges(pcm_bytes: bytes, sample_rate: int = 32000, fade_ms: float = 3.0) -> bytes:
    """
    Applies a 3ms linear fade-in at the start and 3ms linear fade-out at the end of a 16-bit PCM chunk.
    Eliminates step discontinuities at chunk boundaries, preventing popping, clicking, and static noise
    when streaming AudioBuffers back-to-back in Web Audio API.
    """
    if not pcm_bytes or len(pcm_bytes) < 4:
        return pcm_bytes

    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        n_samples = len(samples)
        fade_len = int((sample_rate * fade_ms) / 1000.0)

        if fade_len > 0 and n_samples >= 2 * fade_len:
            # Linear fade in
            fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            samples[:fade_len] *= fade_in

            # Linear fade out
            fade_out = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
            samples[-fade_len:] *= fade_out

        return samples.astype(np.int16).tobytes()
    except Exception as err:
        logger.debug(f"Edge smoothing skipped: {err}")
        return pcm_bytes


def add_wav_header(
    pcm_bytes: bytes,
    sample_rate: int = 32000,
    channels: int = 1,
    bit_depth: int = 16
) -> bytes:
    """
    Adds a standard 44-byte RIFF WAV header to raw 16-bit PCM audio bytes.
    Enables modern Web browsers (Chrome/Safari/Edge) AudioContext.decodeAudioData
    to decode and play audio without throwing EncodingError.
    """
    if not pcm_bytes:
        return b""

    data_size = len(pcm_bytes)
    byte_rate = sample_rate * channels * (bit_depth // 8)
    block_align = channels * (bit_depth // 8)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        data_size + 36,
        b"WAVE",
        b"fmt ",
        16,          # Subchunk1Size (16 for uncompressed PCM)
        1,           # AudioFormat (1 for PCM)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bit_depth,
        b"data",
        data_size,
    )
    return header + pcm_bytes

