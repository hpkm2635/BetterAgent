import collections
import hashlib
import logging
import math
import os
import time
import uuid
from typing import List, Dict, Any, Optional
from shared.config_loader import get_config_val

logger = logging.getLogger("vector_store")


def _hashed_embed(text: str, dim: int = 256) -> List[float]:
    """Deterministic local embedding fallback via feature hashing and L2 normalization."""
    if not text:
        return [0.0] * dim
    vector = [0.0] * dim
    chars = text.strip()
    features = list(chars) + [chars[i : i + 2] for i in range(len(chars) - 1)]
    for feat in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class VectorMemoryStore:

    def __init__(
        self,
        collection_name: Optional[str] = None,
        decay_lambda: Optional[float] = None,
        max_in_memory_capacity: int = 1000,
    ):
        self.collection_name = collection_name or get_config_val("memory.qdrant_collection", "betteragent_memories")
        self.decay_lambda = decay_lambda if decay_lambda is not None else float(get_config_val("memory.ebbinghaus_lambda", 0.0001))
        self.max_in_memory_capacity = max_in_memory_capacity

        # Fixed vector dimension initialized up front based on active provider configuration
        default_dim = 1536 if (os.getenv("EMBEDDING_BASE_URL") or os.getenv("GEMINI_API_KEY")) else 256
        self.dim = int(os.getenv("EMBEDDING_DIM", str(default_dim)))

        self.qdrant_url = get_config_val("infrastructure.qdrant_url", os.getenv("QDRANT_URL", "http://127.0.0.1:6333"))
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY")

        self.qdrant_client = None
        self._qdrant_disabled = False
        # Strictly bounded ring-buffer for in-memory storage fallback (prevents heap memory leaks under all conditions)
        self.in_memory_docs: collections.deque[Dict[str, Any]] = collections.deque(maxlen=self.max_in_memory_capacity)
        self._http_session = None
        self._session_lock = None

        self._init_client()

    def _init_client(self) -> None:
        try:
            from qdrant_client import AsyncQdrantClient
            # Pass check_compatibility=False to avoid blocking server version checks when Qdrant is offline
            self.qdrant_client = AsyncQdrantClient(
                url=self.qdrant_url,
                api_key=self.qdrant_api_key,
                timeout=0.5,
                check_compatibility=False,
            )
            logger.info(f"VectorMemoryStore configured Qdrant client at {self.qdrant_url} (dim={self.dim})")
        except Exception as e:
            logger.warning(f"Failed to initialize AsyncQdrantClient ({e}). Falling back to in-memory store.")
            self.qdrant_client = None
            self._qdrant_disabled = True

    def _handle_qdrant_error(self, e: Exception) -> None:
        if not self._qdrant_disabled:
            logger.warning(f"Qdrant error in VectorMemoryStore ({e}), disabling Qdrant for current process.")
            self._qdrant_disabled = True
            self.qdrant_client = None

    async def _get_http_session(self):
        """Reusable singleton ClientSession to avoid socket/port exhaustion under load."""
        import aiohttp
        import asyncio
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        if self._http_session is None or self._http_session.closed:
            async with self._session_lock:
                if self._http_session is None or self._http_session.closed:
                    self._http_session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=5)
                    )
        return self._http_session

    async def close(self) -> None:
        """Cleanly close underlying HTTP sessions and Qdrant client connection."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
        if self.qdrant_client:
            try:
                await self.qdrant_client.close()
            except Exception:
                pass
            self.qdrant_client = None

    async def _ensure_collection(self) -> bool:
        if not self.qdrant_client or self._qdrant_disabled:
            return False
        try:
            from qdrant_client.http import models as qmodels
            exists = await self.qdrant_client.collection_exists(self.collection_name)
            if not exists:
                await self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=qmodels.VectorParams(
                        size=self.dim,
                        distance=qmodels.Distance.COSINE,
                    ),
                )
                logger.info(f"Created Qdrant collection '{self.collection_name}' (dim={self.dim})")
            return True
        except Exception as e:
            self._handle_qdrant_error(e)
            return False

    async def _embed_text(self, text: str) -> List[float]:
        base_url = os.getenv("EMBEDDING_BASE_URL")
        api_key = os.getenv("EMBEDDING_API_KEY")
        model = os.getenv("EMBEDDING_MODEL")

        if base_url and api_key and model:
            try:
                session = await self._get_http_session()
                async with session.post(
                    f"{base_url.rstrip('/')}/embeddings",
                    json={"model": model, "input": [text]},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_vec = data["data"][0]["embedding"]
                        # Return exact embedding vector produced by model (no truncation or zero padding)
                        return list(raw_vec)
            except Exception as e:
                logger.warning(f"External embedding endpoint failed ({e}), falling back to hashed embedder.")

        # Local hashed fallback matching self.dim exactly
        return _hashed_embed(text, self.dim)

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
        norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (norm_a * norm_b)

    async def add_memory_segment(
        self,
        user_id: int,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not text or not text.strip():
            return ""

        timestamp = time.time()
        doc_id = str(uuid.uuid4())
        vector = await self._embed_text(text)

        doc = {
            "id": doc_id,
            "user_id": int(user_id),
            "text": text.strip(),
            "vector": vector,
            "metadata": metadata or {},
            "timestamp": timestamp,
        }

        stored_in_qdrant = False
        if self.qdrant_client and not self._qdrant_disabled:
            try:
                from qdrant_client.http import models as qmodels
                ready = await self._ensure_collection()
                if ready:
                    point = qmodels.PointStruct(
                        id=doc_id,
                        vector=vector,
                        payload={
                            "user_id": int(user_id),
                            "text": text.strip(),
                            "timestamp": timestamp,
                            "metadata": metadata or {},
                        },
                    )
                    await self.qdrant_client.upsert(
                        collection_name=self.collection_name,
                        points=[point],
                    )
                    stored_in_qdrant = True
                    logger.info(f"Upserted memory segment doc_id={doc_id} to Qdrant for user_id={user_id}")
            except Exception as e:
                self._handle_qdrant_error(e)

        if not stored_in_qdrant:
            self.in_memory_docs.append(doc)

        return doc_id

    async def search_relevant_memories(
        self,
        user_id: int,
        query_text: str,
        top_k: int = 5,
        score_threshold: float = 0.5,
    ) -> List[str]:
        if not query_text or not query_text.strip():
            return []

        now = time.time()
        query_vector = await self._embed_text(query_text)
        scored_results: List[tuple[float, str]] = []

        if self.qdrant_client and not self._qdrant_disabled:
            try:
                from qdrant_client.http import models as qmodels
                ready = await self._ensure_collection()
                if ready:
                    user_filter = qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="user_id",
                                match=qmodels.MatchValue(value=int(user_id)),
                            )
                        ]
                    )
                    res = await self.qdrant_client.query_points(
                        collection_name=self.collection_name,
                        query=query_vector,
                        query_filter=user_filter,
                        limit=top_k * 2,
                    )
                    for hit in res.points:
                        payload = hit.payload or {}
                        raw_score = float(hit.score)
                        ts = float(payload.get("timestamp", now))
                        text = payload.get("text", "")
                        if not text:
                            continue

                        delta_t_hours = max(0.0, (now - ts) / 3600.0)
                        decay_factor = math.exp(-self.decay_lambda * delta_t_hours)
                        final_score = raw_score * decay_factor

                        if final_score >= score_threshold:
                            scored_results.append((final_score, text))

                    if scored_results:
                        scored_results.sort(key=lambda x: x[0], reverse=True)
                        return [t for _, t in scored_results[:top_k]]
            except Exception as e:
                self._handle_qdrant_error(e)

        # In-memory search fallback (using bounded deque)
        scored_results = []
        for doc in list(self.in_memory_docs):
            if doc.get("user_id") != int(user_id):
                continue

            similarity = self._cosine_similarity(query_vector, doc.get("vector", []))
            delta_t_hours = max(0.0, (now - doc.get("timestamp", now)) / 3600.0)
            decay_factor = math.exp(-self.decay_lambda * delta_t_hours)
            final_score = similarity * decay_factor

            if final_score >= score_threshold:
                scored_results.append((final_score, doc["text"]))

        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored_results[:top_k]]
