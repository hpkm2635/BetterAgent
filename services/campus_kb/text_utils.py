"""Text preprocessing helpers shared by embedding and retrieval."""

from __future__ import annotations

import logging
import re
from typing import List


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")

_DOMAIN_TERMS = (
    "校园卡",
    "一卡通",
    "教务处",
    "选课系统",
    "第三食堂",
    "自习室",
    "图书馆",
    "宿舍楼",
    "学生证",
    "校园网",
    "医务室",
    "运动场",
    "失物招领",
    "社团",
    "助学金",
    "奖学金",
)

_jieba = None


def _get_jieba():
    global _jieba
    if _jieba is None:
        try:
            import jieba

            jieba.setLogLevel(logging.WARNING)
            for term in _DOMAIN_TERMS:
                jieba.add_word(term)
            _jieba = jieba
        except ImportError:
            _jieba = False
    return _jieba


def bm25_tokens(text: str) -> List[str]:
    """Tokenize for BM25, preferring jieba words with an n-gram fallback."""
    jieba = _get_jieba()
    if jieba:
        return [token.strip().lower() for token in jieba.lcut(text or "") if token.strip()]
    return text_features(text)


def text_features(text: str) -> List[str]:
    """Return deterministic token-like features for Chinese/ASCII text.

    Chinese runs are expanded into character unigrams, bigrams, and trigrams;
    ASCII runs become lowercase word tokens plus character bigrams. Keeping
    BM25 and the local hashed embedder on the same features makes the two
    retrieval signals comparable without a tokenizer dependency.
    """
    text = (text or "").lower()
    features: List[str] = []

    for match in _CJK_RUN_RE.finditer(text):
        run = match.group()
        features.extend(run)
        features.extend(run[i:i + 2] for i in range(len(run) - 1))
        features.extend(run[i:i + 3] for i in range(len(run) - 2))

    for match in _ASCII_WORD_RE.finditer(text):
        word = match.group()
        features.append(word)
        features.extend(word[i:i + 2] for i in range(len(word) - 1))

    return features


def chunk_text(text: str, max_chars: int = 500, overlap_chars: int = 50) -> List[str]:
    """Split text along sentence boundaries while respecting a length cap.

    Short documents are kept whole. Long sentences are force-split, and
    adjacent chunks carry a small overlap so a topic that straddles a boundary
    is not lost.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[。！？；\n])", text)
    chunks: List[str] = []
    current = ""

    for sentence in sentences:
        if not sentence.strip():
            continue

        while len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars - overlap_chars:] if overlap_chars > 0 else ""

        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            if overlap_chars > 0 and len(current) > overlap_chars:
                current = current[-overlap_chars:]
            else:
                current = ""

        current += sentence

    if current.strip():
        chunks.append(current)

    return chunks or [text]
