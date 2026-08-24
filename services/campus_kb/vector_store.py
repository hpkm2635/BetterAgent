"""Qdrant-backed knowledge store with an in-memory fallback."""

from __future__ import annotations

import asyncio
import math
import os
import uuid
from typing import Dict, List, Optional, Set, Tuple

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    HasIdCondition,
    MatchValue,
    PointStruct,
    VectorParams,
)

from shared.logger import setup_logger
from services.campus_kb.embedding import Embedder
from services.campus_kb.retrieval import (
    BM25,
    detect_category,
    expand_query,
)
from services.campus_kb.schemas import IngestDocument, SearchResult
from services.campus_kb.text_utils import bm25_tokens, chunk_text


logger = setup_logger("campus_kb")

_CATEGORY_BOOST = 0.005
_DENSE_MIN_SCORE = 0.25


class KnowledgeStore:
    """Owns the document index, Qdrant collection, and hybrid search pipeline."""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.collection_name = os.getenv("CAMPUS_KB_QDRANT_COLLECTION", "campus_kb")
        self.client: Optional[AsyncQdrantClient] = None
        self._docs: Dict[str, dict] = {}
        self._source_ids: Dict[str, Set[str]] = {}
        # Guards the delete-old-then-write-new critical section in ingest():
        # each document has multiple `await` points between them, so two
        # concurrent /api/kb/ingest calls targeting the same source could
        # otherwise interleave and leave duplicate or missing entries.
        self._mutation_lock = asyncio.Lock()

    @property
    def dim(self) -> int:
        return self.embedder.dim

    def _make_client(self) -> AsyncQdrantClient:
        url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
        api_key = os.getenv("QDRANT_API_KEY")
        if api_key:
            return AsyncQdrantClient(url=url, api_key=api_key)
        return AsyncQdrantClient(url=url)

    async def start(self) -> None:
        self._docs = {}
        self._source_ids = {}
        self.client = None
        try:
            self.client = self._make_client()
            await self._ensure_collection()
            await self._load_existing()
            logger.info(
                "campus_kb store started with Qdrant collection %r (%d docs)",
                self.collection_name,
                len(self._docs),
            )
        except Exception as exc:
            self.client = None
            self._docs = {}
            logger.warning("Qdrant unavailable (%s), using in-memory fallback", exc)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def _ensure_collection(self) -> None:
        if self.client is None:
            return
        if await self.client.collection_exists(self.collection_name):
            return
        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
        )

    async def _load_existing(self) -> None:
        if self.client is None:
            return

        offset = None
        while True:
            records, offset = await self.client.scroll(
                self.collection_name,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for record in records:
                point_id = str(record.id)
                payload = record.payload or {}
                source = payload.get("source", "")
                self._docs[point_id] = {
                    "id": point_id,
                    "content": payload.get("content", ""),
                    "question": payload.get("question"),
                    "source": payload.get("source", ""),
                    "category": payload.get("category"),
                    "metadata": payload.get("metadata", {}),
                    "vector": record.vector or [],
                }
                self._source_ids.setdefault(source, set()).add(point_id)
            if offset is None:
                break

    async def ingest(self, documents: List[IngestDocument]) -> Tuple[int, int]:
        if self.client is not None:
            try:
                await self._ensure_collection()
            except Exception:
                pass

        ingested = 0
        failed = 0

        # Group documents by source (preserving first-seen order) so every
        # new point for a source gets written *before* that source's old
        # points are deleted -- write-then-delete instead of the previous
        # delete-then-write. A concurrent search() (deliberately left
        # unlocked; see its own docstring) can then never observe an empty
        # window for a source mid-reingest, only a brief moment where old
        # and new points coexist, which is a strictly safer failure mode.
        order: List[str] = []
        groups: Dict[str, List[IngestDocument]] = {}
        for document in documents:
            source = document.source or ""
            if source not in groups:
                groups[source] = []
                order.append(source)
            groups[source].append(document)

        async with self._mutation_lock:
            for source in order:
                new_point_ids: Set[str] = set()
                for document in groups[source]:
                    try:
                        questions = [q.strip() for q in (document.questions or []) if q and q.strip()]
                        points: List[PointStruct] = []

                        if questions:
                            vectors = await self.embedder.embed(questions)
                            for question, vector in zip(questions, vectors):
                                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:q:{question}"))
                                payload = {
                                    "content": document.content,
                                    "question": question,
                                    "source": source,
                                    "category": document.category,
                                    "metadata": document.metadata,
                                }
                                self._docs[point_id] = {
                                    "id": point_id,
                                    "content": document.content,
                                    "question": question,
                                    "source": source,
                                    "category": document.category,
                                    "metadata": document.metadata,
                                    "vector": vector,
                                }
                                self._source_ids.setdefault(source, set()).add(point_id)
                                new_point_ids.add(point_id)
                                points.append(PointStruct(id=point_id, vector=vector, payload=payload))
                        else:
                            chunks = chunk_text(document.content)
                            vectors = await self.embedder.embed(chunks)
                            for chunk, vector in zip(chunks, vectors):
                                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:chunk:{chunk}"))
                                payload = {
                                    "content": chunk,
                                    "source": source,
                                    "category": document.category,
                                    "metadata": document.metadata,
                                }
                                self._docs[point_id] = {
                                    "id": point_id,
                                    "content": chunk,
                                    "source": source,
                                    "category": document.category,
                                    "metadata": document.metadata,
                                    "vector": vector,
                                }
                                self._source_ids.setdefault(source, set()).add(point_id)
                                new_point_ids.add(point_id)
                                points.append(PointStruct(id=point_id, vector=vector, payload=payload))

                        if self.client is not None:
                            try:
                                await self.client.upsert(
                                    collection_name=self.collection_name,
                                    points=points,
                                )
                            except Exception:
                                # A Qdrant outage is non-fatal: the in-memory index
                                # already contains this document and search falls back.
                                pass

                        ingested += 1
                    except Exception:
                        failed += 1

                if source:
                    await self._delete_by_source(source, exclude_ids=new_point_ids)

        logger.info("campus_kb ingest finished: ingested=%d failed=%d", ingested, failed)
        return ingested, failed

    async def _delete_by_source(self, source: str, exclude_ids: Optional[Set[str]] = None) -> None:
        """Delete every stored point for `source` except `exclude_ids`.

        Called *after* a re-ingest has already written the source's new
        points (see ingest()'s write-then-delete ordering), so exclude_ids
        is exactly "the points this ingest call just wrote for this
        source" -- without it, this would delete the points it just wrote.

        Never raises: this is stale-point cleanup for a source whose new
        points are already written and counted as ingested, so a failure
        here must not abort the rest of the batch (ingest() calls this once
        per source, outside any per-document try/except) or misreport
        already-successful writes as failed.
        """
        exclude_ids = exclude_ids or set()

        try:
            # The in-memory _source_ids map is rebuilt from Qdrant on startup,
            # but if Qdrant was unavailable the map may be empty or stale.
            # Scan the whole in-memory document set to guarantee no old
            # chunks survive.
            old_ids = {
                point_id for point_id, doc in self._docs.items()
                if doc.get("source") == source and point_id not in exclude_ids
            }
            for point_id in old_ids:
                self._docs.pop(point_id, None)
            self._source_ids[source] = set(exclude_ids)
        except Exception as exc:
            logger.warning("failed to clean up in-memory index for source=%r: %s", source, exc)

        if self.client is not None:
            try:
                must_not = [HasIdCondition(has_id=list(exclude_ids))] if exclude_ids else None
                await self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[FieldCondition(key="source", match=MatchValue(value=source))],
                            must_not=must_not,
                        )
                    ),
                )
            except Exception as exc:
                logger.warning("failed to delete old points for source=%r: %s", source, exc)

    async def search(
        self,
        query: str,
        top_k: int,
        category: Optional[str],
    ) -> List[SearchResult]:
        docs = list(self._docs.values())
        if category is not None:
            docs = [doc for doc in docs if doc["category"] == category]
        if not docs:
            return []

        expanded = expand_query(query)
        index_by_id = {doc["id"]: index for index, doc in enumerate(docs)}

        corpus = [bm25_tokens(doc["content"]) for doc in docs]
        bm25 = BM25(corpus)
        query_terms = bm25_tokens(expanded)
        bm25_scores = [bm25.score(index, query_terms) for index in range(len(docs))]

        # The dense side compares the user's original question against stored
        # question embeddings (QA chunking). Expansion is only for BM25 recall;
        # embedding the expanded text would dilute an exact question match.
        query_vector = (await self.embedder.embed([query]))[0]
        dense_scores = await self._dense_scores(query_vector, docs, category, index_by_id, top_k)

        max_bm25 = max(bm25_scores) if bm25_scores else 0.0
        max_dense = max(dense_scores) if dense_scores else 0.0
        if max_bm25 <= 0.0 and max_dense < _DENSE_MIN_SCORE:
            logger.info("campus_kb search below relevance threshold for query=%r", query)
            return []

        # Min-max normalize within the candidate set so the two signals are
        # comparable, then weight by signal strength. A weak dense signal
        # (below the relevance floor) is down-weighted to avoid noise.
        min_bm25 = min(bm25_scores) if bm25_scores else 0.0
        min_dense = min(dense_scores) if dense_scores else 0.0
        range_bm25 = max_bm25 - min_bm25
        range_dense = max_dense - min_dense
        dense_is_strong = max_dense >= _DENSE_MIN_SCORE

        relevance: List[float] = []
        for index in range(len(docs)):
            dense_norm = (
                (dense_scores[index] - min_dense) / range_dense
                if range_dense > 0 else 0.0
            )
            bm25_norm = (
                (bm25_scores[index] - min_bm25) / range_bm25
                if range_bm25 > 0 else 0.0
            )
            if dense_is_strong:
                # Both signals are usable; arithmetic mean is reasonable.
                relevance.append(0.5 * dense_norm + 0.5 * bm25_norm)
            else:
                # Dense signal is too weak to trust; rely mostly on BM25.
                relevance.append(0.2 * dense_norm + 0.8 * bm25_norm)

        detected_category = None if category is not None else detect_category(query)
        if detected_category:
            for index, doc in enumerate(docs):
                if doc["category"] == detected_category:
                    relevance[index] += _CATEGORY_BOOST

        ordered = sorted(
            range(len(docs)),
            key=lambda index: relevance[index],
            reverse=True,
        )

        results: List[SearchResult] = []
        seen_contents: Set[str] = set()
        for index in ordered:
            if len(results) >= top_k:
                break
            doc = docs[index]
            dedup_key = doc["content"].strip()
            if dedup_key in seen_contents:
                continue
            seen_contents.add(dedup_key)
            results.append(
                SearchResult(
                    content=doc["content"],
                    source=doc["source"],
                    score=round(relevance[index], 4),
                    category=doc["category"],
                )
            )
        logger.info(
            "campus_kb search query=%r category=%r returned=%d",
            query,
            category,
            len(results),
        )
        return results

    async def _dense_scores(
        self,
        query_vector: List[float],
        docs: List[dict],
        category: Optional[str],
        index_by_id: Dict[str, int],
        top_k: int,
    ) -> List[float]:
        if self.client is None:
            return [self._cosine(query_vector, doc["vector"]) for doc in docs]

        query_filter = None
        if category is not None:
            query_filter = Filter(
                must=[FieldCondition(key="category", match=MatchValue(value=category))]
            )

        try:
            limit = min(len(docs), max(top_k * 3, 20))
            response = await self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=False,
                with_vectors=False,
            )
            scores = [0.0] * len(docs)
            for point in response.points:
                index = index_by_id.get(str(point.id))
                if index is not None:
                    scores[index] = float(point.score)
            return scores
        except Exception:
            return [self._cosine(query_vector, doc["vector"]) for doc in docs]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
        norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (norm_a * norm_b)
