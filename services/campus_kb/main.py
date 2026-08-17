"""FastAPI entrypoint for the Campus KB RAG service (port 8093)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from shared.logger import setup_logger
from services.campus_kb.embedding import create_embedder
from services.campus_kb.schemas import IngestRequest, SearchRequest
from services.campus_kb.vector_store import KnowledgeStore


load_dotenv()
logger = setup_logger("campus_kb")
store = KnowledgeStore(create_embedder())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("starting campus_kb service")
    await store.start()
    yield
    await store.close()
    logger.info("campus_kb service stopped")


app = FastAPI(title="Campus KB RAG Service", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "campus_kb"}


@app.post("/api/kb/ingest")
async def ingest(request: IngestRequest):
    try:
        ingested, failed = await store.ingest(request.documents)
    except Exception as exc:
        logger.exception("campus_kb ingest endpoint failed")
        return JSONResponse(
            status_code=500,
            content={
                "error": str(exc),
                "ingested": 0,
                "failed": len(request.documents),
                "message": "ERROR",
            },
        )
    return {"ingested": ingested, "failed": failed, "message": "OK"}


@app.post("/api/kb/search")
async def search(request: SearchRequest):
    top_k = max(1, min(request.top_k, 20))
    try:
        results = await store.search(request.query, top_k, request.category)
    except Exception as exc:
        logger.exception("campus_kb search endpoint failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "results": [], "total": 0, "query": request.query},
        )
    return {
        "results": [result.model_dump() for result in results],
        "query": request.query,
        "total": len(results),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8093, reload=False)
