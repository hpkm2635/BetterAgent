"""Retrieval primitives: BM25, RRF fusion, query expansion, category routing."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple


class BM25:
    """Dependency-free BM25 keyword ranker."""

    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.doc_len) / len(corpus)) if corpus else 0.0
        self.doc_freqs = [Counter(doc) for doc in corpus]
        self.idf = self._build_idf()

    def _build_idf(self) -> Dict[str, float]:
        document_freq: Dict[str, int] = defaultdict(int)
        for doc in self.corpus:
            for term in set(doc):
                document_freq[term] += 1

        n_docs = len(self.corpus)
        return {
            term: math.log((n_docs - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in document_freq.items()
        }

    def score(self, doc_index: int, query_terms: List[str]) -> float:
        if not self.corpus:
            return 0.0

        doc_freq = self.doc_freqs[doc_index]
        doc_len = self.doc_len[doc_index]
        score = 0.0

        for term in query_terms:
            idf = self.idf.get(term)
            if idf is None:
                continue

            term_freq = doc_freq.get(term, 0)
            if term_freq == 0:
                continue

            denominator = term_freq + self.k1 * (1.0 - self.b + self.b * doc_len / self.avgdl)
            score += idf * (term_freq * (self.k1 + 1.0)) / denominator

        return score


def rrf_fusion(ranked_lists: List[List[int]], k: int = 60) -> Dict[int, float]:
    """Fuse ranked document indices with Reciprocal Rank Fusion."""
    fused: Dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, doc_index in enumerate(ranked):
            fused[doc_index] += 1.0 / (k + rank + 1)
    return dict(fused)


_SYNONYM_GROUPS: List[Tuple[str, ...]] = [
    ("关门", "关闭", "闭馆", "打烊"),
    ("开放", "营业", "开门"),
    ("几点", "什么时间", "什么时候", "时间"),
    ("超市", "商店", "便利店"),
    ("选课", "选课系统", "教务处"),
    ("食堂", "餐厅", "饭堂"),
    ("宿舍", "寝室", "公寓"),
    ("校园卡", "一卡通", "饭卡"),
]


def expand_query(query: str) -> str:
    """Append campus synonym terms that are not already in the query."""
    terms = [query]
    for group in _SYNONYM_GROUPS:
        if any(term in query for term in group):
            terms.extend(term for term in group if term not in query)
    return " ".join(terms)


_CATEGORY_KEYWORDS: Dict[str, Dict[str, int]] = {
    "faq": {
        "怎么": 1,
        "如何": 1,
        "怎么办": 2,
        "流程": 2,
        "申请": 2,
        "办理": 2,
        "是否": 1,
        "可以": 1,
    },
    "schedule": {
        "时间": 1,
        "几点": 1,
        "开放": 1,
        "营业": 1,
        "选课": 2,
        "考试": 2,
        "安排": 1,
        "什么时候": 1,
    },
    "resource": {
        "图书馆": 3,
        "自习室": 2,
        "借书": 2,
        "资源": 1,
        "下载": 1,
        "数据库": 2,
        "电子": 1,
    },
    "service": {
        "超市": 3,
        "食堂": 3,
        "餐厅": 2,
        "校园卡": 3,
        "一卡通": 3,
        "充值": 2,
        "补办": 2,
        "宿舍": 2,
    },
}


def detect_category(query: str) -> Optional[str]:
    """Best-effort category routing when the caller does not provide one."""
    scores = {category: 0 for category in _CATEGORY_KEYWORDS}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for keyword, weight in keywords.items():
            if keyword in query:
                scores[category] += weight

    best = max(scores, key=lambda category: scores[category])
    return best if scores[best] > 0 else None
