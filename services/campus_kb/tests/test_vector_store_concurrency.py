import asyncio

import pytest

from services.campus_kb.embedding import HashedNgramEmbedder
from services.campus_kb.schemas import IngestDocument
from services.campus_kb.vector_store import KnowledgeStore


class _YieldingEmbedder:
    """Wraps HashedNgramEmbedder with a real await point.

    HashedNgramEmbedder.embed() does no actual async I/O, so awaiting it
    never yields control back to the event loop -- two concurrent ingest()
    calls would just run back-to-back rather than interleaving. Adding one
    asyncio.sleep(0) reproduces the interleaving that a real embedding
    backend (an HTTP call) would naturally cause.
    """

    def __init__(self) -> None:
        self._inner = HashedNgramEmbedder(dim=64)
        self.dim = self._inner.dim

    async def embed(self, texts):
        await asyncio.sleep(0)
        return await self._inner.embed(texts)


@pytest.mark.asyncio
async def test_concurrent_ingest_same_source_does_not_duplicate_or_lose_docs():
    """Two concurrent ingest() calls replacing the same source must not
    interleave their delete-then-write phases: whichever finishes last should
    be the one whose documents survive, not a union of both (a source
    re-ingest is meant to fully replace what was there before).
    """
    store = KnowledgeStore(_YieldingEmbedder())

    async def ingest_version(content: str):
        return await store.ingest([
            IngestDocument(content=content, source="faq.md"),
        ])

    results = await asyncio.gather(
        ingest_version("图书馆周一至周五开放至22:00。"),
        ingest_version("图书馆全年无休，24小时开放。"),
    )

    assert results == [(1, 0), (1, 0)]

    docs_for_source = [doc for doc in store._docs.values() if doc["source"] == "faq.md"]
    assert len(docs_for_source) == 1, (
        f"expected exactly 1 surviving document for source=faq.md after two "
        f"concurrent full-source ingests, got {len(docs_for_source)}: {docs_for_source}"
    )
