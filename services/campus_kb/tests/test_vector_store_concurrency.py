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


@pytest.mark.asyncio
async def test_delete_by_source_cleanup_failure_does_not_abort_the_batch():
    """A failure in _delete_by_source's in-memory cleanup step (called once
    per source, after that source's new points are already written and
    counted as ingested) must not propagate out of ingest() and abort
    processing of later sources in the same batch -- write-then-delete
    moved this call outside the per-document try/except that used to catch
    it, so _delete_by_source itself must now be exception-safe.
    """
    store = KnowledgeStore(HashedNgramEmbedder(dim=64))

    class _PoisonedDocs(dict):
        def items(self):
            raise RuntimeError("boom")

    store._docs = _PoisonedDocs()

    ingested, failed = await store.ingest([
        IngestDocument(content="图书馆周一至周五开放至22:00。", source="faq.md"),
        IngestDocument(content="超市营业时间07:00-23:00。", source="market.md"),
    ])

    # Both documents' embed/write steps succeeded -- only the (poisoned)
    # stale-cleanup step failed, for both sources, which must be swallowed
    # rather than counted as a document failure or abort the second source.
    assert (ingested, failed) == (2, 0)


@pytest.mark.asyncio
async def test_reingest_fully_replaces_old_content_not_a_union():
    """A source re-ingest must end up with *only* the new content -- this is
    the correctness property ingest()'s write-then-delete reordering has to
    preserve (write-then-exclude-and-delete is more error-prone than the old
    delete-then-write if the excluded-id tracking is wrong, since stale
    content would then survive forever instead of just being briefly visible).
    """
    store = KnowledgeStore(HashedNgramEmbedder(dim=64))

    await store.ingest([
        IngestDocument(content="图书馆周一至周五开放至22:00。", source="faq.md"),
        IngestDocument(content="超市营业时间07:00-23:00。", source="faq.md"),
    ])
    assert len(store._source_ids["faq.md"]) == 2

    await store.ingest([
        IngestDocument(content="图书馆全年无休，24小时开放。", source="faq.md"),
    ])

    docs_for_source = [doc for doc in store._docs.values() if doc["source"] == "faq.md"]
    assert len(docs_for_source) == 1, f"expected old content fully replaced, got {docs_for_source}"
    assert docs_for_source[0]["content"] == "图书馆全年无休，24小时开放。"
    assert store._source_ids["faq.md"] == {docs_for_source[0]["id"]}


@pytest.mark.asyncio
async def test_reingest_never_exposes_an_empty_window_for_the_source():
    """During a re-ingest, the source's document count must never drop to 0
    -- write-then-delete means the worst observable state is "old and new
    briefly coexist", never "neither exists yet" (the delete-then-write
    ordering this replaces could expose exactly that empty window).
    """
    store = KnowledgeStore(HashedNgramEmbedder(dim=64))
    await store.ingest([
        IngestDocument(content="图书馆周一至周五开放至22:00。", source="faq.md"),
    ])

    min_observed = None
    stop = asyncio.Event()

    async def prober():
        nonlocal min_observed
        while not stop.is_set():
            count = len(store._source_ids.get("faq.md", set()))
            if min_observed is None or count < min_observed:
                min_observed = count
            await asyncio.sleep(0)

    class _SlowEmbedder:
        """Embeds with several real await points, widening the window a
        prober task would need to race into to observe an inconsistent
        state -- HashedNgramEmbedder alone never yields control at all.
        """

        def __init__(self) -> None:
            self._inner = HashedNgramEmbedder(dim=64)

        async def embed(self, texts):
            for _ in range(5):
                await asyncio.sleep(0)
            return await self._inner.embed(texts)

    store.embedder = _SlowEmbedder()

    prober_task = asyncio.create_task(prober())
    await store.ingest([
        IngestDocument(content="图书馆全年无休，24小时开放。", source="faq.md"),
    ])
    stop.set()
    await prober_task

    assert min_observed is not None and min_observed >= 1, (
        f"expected the source to always have >=1 document during re-ingest, "
        f"observed a minimum of {min_observed}"
    )
