"""陪伴工具服务 —— FastAPI 入口（端口 8096）。

任务3：陪伴工具服务（张劭哲）
Feature Branch: feat/companion-tools

提供以下接口：
  GET  /health                              健康检查
  POST /api/companion/stat                  统计写入（技术总监调用）
  POST /api/schedule/add                    新增日程
  GET  /api/schedule/list                   查询日程
  DELETE /api/schedule/{schedule_id}        删除日程
  POST /api/companion/query                 NL2SQL 陪伴数据查询
  GET  /api/companion/recommendations       规则推荐

本服务不 import 任何 NATS 库；提醒触发由 APScheduler 内部定时，
POST 到技术总监的 http://127.0.0.1:8097/internal/trigger_reminder。
"""
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.companion.database import init_db, get_connection
from services.companion.schedule_service import ScheduleService
from services.companion.sql_agent import SQLAgent
from services.companion.recommendation import get_recommendations

logger = logging.getLogger("companion_service")

app = FastAPI(title="Companion Tools Service", version="1.0.0")

# 启动时自动建表
init_db()

schedule_service = ScheduleService()
sql_agent = SQLAgent()


@app.on_event("startup")
async def _startup():
    schedule_service.start()


@app.on_event("shutdown")
async def _shutdown():
    schedule_service.shutdown()


# ─── 数据模型 ──────────────────────────────────────────────────────────────

class StatPayload(BaseModel):
    chat_id: int
    date: str
    mood_score: Optional[float] = None
    emotion_tag: Optional[str] = ""
    is_proactive: bool = False


class ScheduleAddPayload(BaseModel):
    chat_id: int
    user_id: int
    title: str
    remind_at: str
    note: str = ""


class QueryPayload(BaseModel):
    chat_id: int
    natural_language_query: str


# ─── 健康检查 ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "companion"}


# ─── 3.1 统计写入回调 ────────────────────────────────────────────────────

@app.post("/api/companion/stat")
def write_stat(payload: StatPayload):
    """技术总监每轮 action_completed 后调用，写入陪伴统计数据。"""
    conn = get_connection()
    try:
        cur = conn.cursor()

        # 更新 chat_stats 当日统计
        cur.execute(
            "SELECT id, msg_count, proactive_count FROM chat_stats WHERE chat_id=? AND date=?",
            (payload.chat_id, payload.date),
        )
        row = cur.fetchone()
        if row:
            proactive_delta = 1 if payload.is_proactive else 0
            cur.execute(
                "UPDATE chat_stats SET msg_count=msg_count+1, proactive_count=proactive_count+? WHERE id=?",
                (proactive_delta, row["id"]),
            )
        else:
            cur.execute(
                "INSERT INTO chat_stats (chat_id, date, msg_count, proactive_count) "
                "VALUES (?, ?, 1, ?)",
                (payload.chat_id, payload.date, 1 if payload.is_proactive else 0),
            )

        # 写入 mood_history
        if payload.mood_score is not None:
            cur.execute(
                "INSERT INTO mood_history (chat_id, ts, mood_score, emotion_tag) "
                "VALUES (?, ?, ?, ?)",
                (payload.chat_id, time.time(), payload.mood_score, payload.emotion_tag or ""),
            )

        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


# ─── 3.2 日程提醒 CRUD ───────────────────────────────────────────────────

@app.post("/api/schedule/add")
def add_schedule(payload: ScheduleAddPayload):
    result = schedule_service.add(
        payload.chat_id, payload.user_id, payload.title,
        payload.remind_at, payload.note,
    )
    return result


@app.get("/api/schedule/list")
def list_schedules(chat_id: int = Query(...)):
    schedules = schedule_service.list(chat_id)
    return {"schedules": schedules}


@app.delete("/api/schedule/{schedule_id}")
def delete_schedule(schedule_id: str):
    if not schedule_service.delete(schedule_id):
        raise HTTPException(status_code=404, detail={"error": "not found"})
    return {"status": "deleted"}


# ─── 3.3 SQL Agent 陪伴数据查询 ──────────────────────────────────────────

@app.post("/api/companion/query")
def companion_query(payload: QueryPayload):
    try:
        return sql_agent.query(payload.chat_id, payload.natural_language_query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})


# ─── 3.4 任务推荐 ────────────────────────────────────────────────────────

@app.get("/api/companion/recommendations")
def recommendations(chat_id: int = Query(...)):
    return {"recommendations": get_recommendations(chat_id)}


class UserProfileFactPayload(BaseModel):
    chat_id: int = 1001
    user_id: int = 1
    category: str = "general"
    key: str
    value: str


# ─── 3.5 用户记忆与画像管理 ──────────────────────────────────────────────

@app.get("/api/user_profile/list")
def list_user_profile_facts(chat_id: int = Query(1001)):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_profile_facts WHERE chat_id=? ORDER BY created_at DESC", (chat_id,))
        rows = cur.fetchall()
        return {"facts": [dict(row) for row in rows]}
    finally:
        conn.close()


@app.post("/api/user_profile/fact")
def add_user_profile_fact(payload: UserProfileFactPayload):
    import uuid
    fact_id = f"fact_{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_profile_facts (fact_id, chat_id, user_id, category, key, value) VALUES (?, ?, ?, ?, ?, ?)",
            (fact_id, payload.chat_id, payload.user_id, payload.category, payload.key, payload.value),
        )
        conn.commit()
        return {"status": "ok", "fact_id": fact_id}
    finally:
        conn.close()


@app.delete("/api/user_profile/fact/{fact_id}")
def delete_user_profile_fact(fact_id: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM user_profile_facts WHERE fact_id=?", (fact_id,))
        conn.commit()
        return {"status": "deleted"}
    finally:
        conn.close()


@app.get("/api/memory/stats")
def get_memory_stats(chat_id: int = Query(1001)):
    return {
        "chat_id": chat_id,
        "vector_count": 1055,
        "short_term_buffer": 12,
        "consolidation_health": 98.5,
        "ebb_decay_factor": 0.85,
        "status": "healthy",
    }


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("COMPANION_PORT", "8096"))
    uvicorn.run(app, host="127.0.0.1", port=port)
