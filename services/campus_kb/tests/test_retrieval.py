"""Pure-Python tests for chunking, BM25, RRF, expansion, and embedding."""

from __future__ import annotations

import asyncio
import math

from services.campus_kb.embedding import HashedNgramEmbedder
from services.campus_kb.retrieval import (
    BM25,
    detect_category,
    expand_query,
    rrf_fusion,
)
from services.campus_kb.text_utils import chunk_text, text_features


def test_chunk_text_short_returns_single():
    assert chunk_text("图书馆开放至22点。") == ["图书馆开放至22点。"]


def test_chunk_text_respects_length_cap():
    text = "。".join("句子" + str(i) for i in range(300))
    chunks = chunk_text(text, max_chars=100)
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_bm25_prefers_matching_document():
    corpus = [
        text_features("图书馆周一至周五开放至22:00，周末20:00关闭。"),
        text_features("校内超市位于第三食堂一楼，营业时间07:00-23:00。"),
        text_features("选课系统每学期第9周开放。"),
    ]
    query = text_features("图书馆几点关门")
    bm25 = BM25(corpus)
    scores = [bm25.score(index, query) for index in range(len(corpus))]
    assert max(range(len(scores)), key=lambda index: scores[index]) == 0


def test_rrf_fusion_rewards_top_rank():
    fused = rrf_fusion([[0, 1, 2], [0, 2, 1]])
    assert fused[0] > fused[1]
    assert fused[0] > fused[2]


def test_expand_query_adds_synonyms():
    expanded = expand_query("图书馆几点关门")
    assert "关闭" in expanded


def test_detect_category():
    assert detect_category("图书馆借书") == "resource"
    assert detect_category("选课时间") == "schedule"
    assert detect_category("你好") is None


def test_hashed_embedder_returns_normalized_vectors():
    embedder = HashedNgramEmbedder(dim=128)
    vectors = asyncio.run(embedder.embed(["图书馆几点关门", "超市在哪里"]))
    assert len(vectors) == 2
    for vector in vectors:
        assert abs(math.sqrt(sum(x * x for x in vector)) - 1.0) < 1e-6
