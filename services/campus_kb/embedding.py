"""Embedding backends for the campus KB service.

The default backend is a dependency-free deterministic hashed n-gram embedder
so the service can start without a model or API key. If EMBEDDING_BASE_URL,
EMBEDDING_API_KEY, and EMBEDDING_MODEL are all set, an OpenAI-compatible
/embeddings endpoint is used instead.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import List, Protocol

from services.campus_kb.text_utils import text_features


class Embedder(Protocol):
    dim: int

    async def embed(self, texts: List[str]) -> List[List[float]]:
        ...


class HashedNgramEmbedder:
    """Deterministic local embedding via feature hashing and L2 normalization."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    async def embed(self, texts: List[str]) -> List[List[float]]:
        return [_embed_one(text, self.dim) for text in texts]


def _embed_one(text: str, dim: int) -> List[float]:
    vector = [0.0] * dim
    for feature in text_features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class OpenAICompatibleEmbedder:
    """Optional OpenAI-compatible /embeddings client."""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 1536) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim

    async def embed(self, texts: List[str]) -> List[List[float]]:
        import aiohttp

        payload = {"model": self.model, "input": texts}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                data = await response.json()

        items = sorted(data["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]


def create_embedder() -> Embedder:
    """Build the configured embedder, defaulting to the local hashed backend."""
    base_url = os.getenv("EMBEDDING_BASE_URL")
    api_key = os.getenv("EMBEDDING_API_KEY")
    model = os.getenv("EMBEDDING_MODEL")
    try:
        dim = int(os.getenv("EMBEDDING_DIM", "1536") or "1536")
    except ValueError:
        dim = 1536

    if base_url and api_key and model:
        return OpenAICompatibleEmbedder(base_url, api_key, model, dim)
    return HashedNgramEmbedder()
