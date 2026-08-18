"""Pydantic request/response models for the campus KB HTTP contract."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Category = Literal["faq", "schedule", "resource", "service"]


class IngestDocument(BaseModel):
    content: str = Field(..., min_length=1)
    source: str = ""
    category: Optional[Category] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    questions: Optional[List[str]] = None


class IngestRequest(BaseModel):
    documents: List[IngestDocument] = Field(..., min_length=1, max_length=100)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 5
    category: Optional[Category] = None


class SearchResult(BaseModel):
    content: str
    source: str
    score: float
    category: Optional[str] = None


class SearchResponse(BaseModel):
    results: List[SearchResult]
    query: str
    total: int


class IngestResponse(BaseModel):
    ingested: int
    failed: int
    message: str = "OK"
