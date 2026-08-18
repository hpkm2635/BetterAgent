"""
BetterAgent Admin Backend REST API (接口契约二: 后台管理系统).

Serves the B 端 admin console on port 8094. Responsibilities:
  * 人设 (Persona) management  -- list / detail / partial-update (ruamel round-trip)
  * 用户 (User) management     -- read-only + soft delete
  * 会话记录 (Session) viewer  -- read-only over Redis short-term history
  * 知识库 (Knowledge Base)    -- reverse proxy to the campus_kb service (:8093)
  * 日程提醒 (Schedule) management -- reverse proxy to the companion service (:8096)

This module is self-contained and must NOT import from core/ / shared/ so the
admin panel stays independent of the rest of BetterAgent.

Run:
    python main.py
or:
    uvicorn main:app --host 0.0.0.0 --port 8094
"""

import hmac
import json
import logging
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ruamel.yaml import YAML

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("admin_backend")

# Load REDIS_URL / REDIS_PASSWORD etc. from a local .env when present.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:  # python-dotenv is optional at runtime
    pass

# ---------------------------------------------------------------------------
# Paths & configuration (all overridable via env so the panel stays portable)
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent           # admin/backend
REPO_ROOT = BACKEND_DIR.parent.parent                   # repository root
PERSONA_DIR = Path(os.getenv("PERSONA_DIR", REPO_ROOT / "config" / "persona"))
DB_PATH = Path(os.getenv("ADMIN_DB_PATH", BACKEND_DIR / "admin.db"))

ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8094"))
CAMPUS_KB_URL = os.getenv("CAMPUS_KB_URL", "http://127.0.0.1:8093").rstrip("/")
COMPANION_URL = os.getenv("COMPANION_URL", "http://127.0.0.1:8096").rstrip("/")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# Admin API access token. When set (non-empty), every /api/admin/* endpoint
# requires a valid `X-Admin-Token: <secret>` or `Authorization: Bearer <secret>`
# header; when empty, the panel stays open for local development.
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")

# 2.1 人设字段白名单: 仅允许通过 PATCH 修改这 6 个字段。
PERSONA_ALLOWED_FIELDS = frozenset({
    "name",
    "appearance",
    "base_prompt",
    "sleepy_prompt",
    "knowledge_scope",
    "forbidden_topics",
})

# 会话历史 key 候选（契约文本为 short_term:{chat_id}，现有代码使用
# betteragent:short_term:{user_id}；两者都尝试，命中即返回）。
SESSION_KEY_TEMPLATES = ("short_term:{chat_id}", "betteragent:short_term:{chat_id}")

_VALID_PERSONA_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# ruamel loaders: 'safe' for read-only (plain dicts), round-trip for PATCH so
# comments and field order are preserved in-place (never rewrite the whole file
# with yaml.dump -- see 接口契约 2.1).
_yaml_safe = YAML(typ="safe")

# Round-trip loader for PATCH: must keep quotes/comments/order, and must not
# re-wrap long scalar lines (ruamel 0.19 defaults preserve_quotes=None and
# width=80, which would strip quotes and fold lines on a full rewrite).
_yaml_rt = YAML()
_yaml_rt.preserve_quotes = True
_yaml_rt.width = 4096

app = FastAPI(title="BetterAgent Admin Backend", version="1.0.0")

# Dev-only CORS: the Admin UI runs on :8095 and calls this API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error(status_code: int, message: str) -> JSONResponse:
    """Contract-consistent error envelope: {"error": "..."}."""
    return JSONResponse(status_code=status_code, content={"error": message})


@app.middleware("http")
async def enforce_admin_token(request: Request, call_next):
    """Gate /api/admin/* behind ADMIN_SECRET_KEY when it is configured.

    Accepts either `X-Admin-Token: <secret>` or `Authorization: Bearer <secret>`.
    Unset/empty ADMIN_SECRET_KEY disables the check (local dev default).
    """
    if ADMIN_SECRET_KEY and request.url.path.startswith("/api/admin"):
        token = request.headers.get("x-admin-token")
        if not token:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[len("Bearer "):].strip()
        if not token or not hmac.compare_digest(token, ADMIN_SECRET_KEY):
            return _error(401, "unauthorized")
    return await call_next(request)


# ---------------------------------------------------------------------------
# 2.1 人设 (Persona) management
# ---------------------------------------------------------------------------
def _persona_path(persona_id: str) -> Optional[Path]:
    """Resolve a persona id to its YAML path, refusing traversal attempts."""
    if not persona_id or not _VALID_PERSONA_ID.match(persona_id):
        return None
    return PERSONA_DIR / f"{persona_id}.yaml"


def _read_persona(persona_id: str) -> Optional[Dict[str, Any]]:
    path = _persona_path(persona_id)
    if path is None or not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = _yaml_safe.load(f)
    return data if isinstance(data, dict) else {}


@app.get("/api/admin/personas")
def list_personas():
    """列出所有人设，摘要字段（id/name/tts_provider/voice_id）。"""
    personas = []
    if PERSONA_DIR.exists():
        for path in sorted(PERSONA_DIR.glob("*.yaml")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = _yaml_safe.load(f)
            except Exception as exc:
                logger.warning(f"Failed to parse persona YAML {path}: {exc}")
                continue
            if not isinstance(data, dict):
                continue
            tts = data.get("tts")
            tts = tts if isinstance(tts, dict) else {}
            personas.append({
                "id": data.get("id") or path.stem,
                "name": data.get("name"),
                "tts_provider": tts.get("provider"),
                "voice_id": tts.get("voice_id"),
            })
    return {"personas": personas}


@app.get("/api/admin/personas/{persona_id}")
def get_persona(persona_id: str):
    """获取单个人设详情：完整 YAML 解析为 JSON，保留所有顶级字段。"""
    data = _read_persona(persona_id)
    if data is None:
        return _error(404, "not found")
    return data


@app.patch("/api/admin/personas/{persona_id}")
def patch_persona(persona_id: str, payload: Optional[Dict[str, Any]] = Body(None)):
    """部分更新人设字段。白名单校验 + ruamel 原地更新（保留注释与顺序）。"""
    path = _persona_path(persona_id)
    if path is None or not path.exists():
        return _error(404, "not found")

    if not payload:
        return _error(400, "empty body")

    # 1) 白名单校验 -- 任何白名单外的字段一律 400
    for field in payload:
        if field not in PERSONA_ALLOWED_FIELDS:
            return _error(400, f"Forbidden field: {field}")

    # 2) 类型校验 -- 契约规定这 6 个字段均为 string
    for field, value in payload.items():
        if not isinstance(value, str):
            return _error(400, f"Field '{field}' must be a string")

    # 3) 原地更新，保留注释与字段顺序；先写同目录临时文件再 os.replace 原子覆盖，
    #    避免写入中途崩溃把 YAML 留成半截文件。
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = _yaml_rt.load(f)
        if not isinstance(doc, dict):
            return _error(400, "invalid persona yaml")

        for field, value in payload.items():
            doc[field] = value

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                _yaml_rt.dump(doc, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
    except Exception as exc:
        logger.error(f"Failed to update persona YAML {path}: {exc}")
        return _error(500, "failed to update persona")

    return {"status": "ok", "id": persona_id}


# ---------------------------------------------------------------------------
# 2.2 用户 (User) management -- 只读 + 软删除
# ---------------------------------------------------------------------------
# 画像数据来自 Redis betteragent:profile:{user_id}（主服务写入的 key，兼容旧
# user_profile:{user_id}）；软删除标记落在一个独立 SQLite 表中（绝不修改
# Redis 的对话历史 key：short_term:{chat_id}）。
def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    try:
        with _db_conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    user_id      INTEGER PRIMARY KEY,
                    display_name TEXT,
                    known_facts  TEXT,      -- JSON array
                    last_seen    TEXT,
                    deleted      INTEGER NOT NULL DEFAULT 0
                )
                """
            )
    except Exception as exc:
        logger.error(f"Failed to init admin SQLite at {DB_PATH}: {exc}")


_redis_client: Any = None
_redis_checked = False


def _get_redis() -> Any:
    """Lazily connect to Redis; return None on any failure (never crash)."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    try:
        import redis
        client = redis.Redis.from_url(
            REDIS_URL,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        client.ping()
        _redis_client = client
        logger.info(f"Admin backend connected to Redis at {REDIS_URL}")
    except Exception as exc:
        logger.warning(f"Redis unavailable ({exc}); user/session endpoints return empty data")
        _redis_client = None
    return _redis_client


def _as_list(value: Any) -> list:
    """Coerce a profile fact value to a list.

    Redis hashes store every field as a string, so `likes`/`known_facts` arrive
    as JSON-encoded lists (e.g. '["篮球"]'). Parse those, pass lists through,
    and wrap bare scalars so callers always get a list.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return parsed
        return [value]
    return [value]


def _normalize_profile(user_id: int, raw: Any) -> Dict[str, Any]:
    """Normalize a Redis user-profile value into the contract shape."""
    if isinstance(raw, str):
        return {"user_id": user_id, "display_name": raw, "known_facts": [], "last_seen": None}
    if not isinstance(raw, dict):
        raw = {}

    known_facts = _as_list(raw.get("known_facts"))
    if not known_facts:
        known_facts = _as_list(raw.get("likes"))

    display_name = (raw.get("display_name") or raw.get("preferred_name")
                    or raw.get("name") or f"用户{user_id}")

    return {
        "user_id": user_id,
        "display_name": display_name,
        "known_facts": known_facts,
        "last_seen": raw.get("last_seen"),
    }


# 主服务 (services/memory/user_profile.py) 写入 betteragent:profile:{user_id}；
# user_profile:{user_id} 是接口契约中记录的旧前缀，作为兜底一并扫描。
# 后扫者覆盖先扫者，因此把真实前缀放在最后以优先。
USER_PROFILE_KEY_PREFIXES = ("user_profile:", "betteragent:profile:")


def _redis_user_profiles(r: Any) -> Dict[int, Dict[str, Any]]:
    """Scan user profile keys (betteragent:profile:* / user_profile:*) and parse each."""
    profiles: Dict[int, Dict[str, Any]] = {}
    try:
        for prefix in USER_PROFILE_KEY_PREFIXES:
            for key in r.scan_iter(f"{prefix}*"):
                try:
                    user_id = int(key.rsplit(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                raw: Any
                try:
                    if r.type(key) == "hash":
                        raw = r.hgetall(key)
                    else:
                        raw = r.get(key)
                        if raw is None:
                            continue
                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, dict):
                                raw = parsed
                        except (TypeError, ValueError):
                            pass
                except Exception as exc:
                    logger.warning(f"Failed to read Redis key {key}: {exc}")
                    continue
                profiles[user_id] = _normalize_profile(user_id, raw)
    except Exception as exc:
        logger.warning(f"Failed to scan Redis user profiles: {exc}")
    return profiles


def _load_sqlite_users() -> Dict[int, Dict[str, Any]]:
    rows: Dict[int, Dict[str, Any]] = {}
    try:
        with _db_conn() as conn:
            for row in conn.execute("SELECT * FROM admin_users").fetchall():
                try:
                    facts = json.loads(row["known_facts"]) if row["known_facts"] else []
                except (TypeError, ValueError):
                    facts = []
                if not isinstance(facts, list):
                    facts = []
                rows[row["user_id"]] = {
                    "user_id": row["user_id"],
                    "display_name": row["display_name"],
                    "known_facts": facts,
                    "last_seen": row["last_seen"],
                    "deleted": bool(row["deleted"]),
                }
    except Exception as exc:
        logger.warning(f"Failed to read admin SQLite users: {exc}")
    return rows


def _all_records() -> Dict[int, Dict[str, Any]]:
    """Merge Redis profiles (source of truth) with SQLite soft-delete flags."""
    r = _get_redis()
    redis_profiles = _redis_user_profiles(r) if r else {}
    sqlite_users = _load_sqlite_users()

    merged: Dict[int, Dict[str, Any]] = {}
    for uid, u in sqlite_users.items():
        merged[uid] = {
            "user_id": uid,
            "display_name": u["display_name"] or f"用户{uid}",
            "known_facts": u["known_facts"],
            "last_seen": u["last_seen"],
            "deleted": u["deleted"],
        }
    for uid, p in redis_profiles.items():
        prev = merged.get(uid)
        merged[uid] = {
            "user_id": uid,
            "display_name": p["display_name"],
            "known_facts": p["known_facts"],
            "last_seen": p["last_seen"],
            "deleted": prev["deleted"] if prev else False,
        }
    return merged


def _user_payload(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": rec["user_id"],
        "display_name": rec["display_name"],
        "known_facts": rec["known_facts"],
        "last_seen": rec["last_seen"],
    }


@app.get("/api/admin/users")
def list_users():
    records = _all_records()
    users = [_user_payload(rec) for uid, rec in sorted(records.items()) if not rec["deleted"]]
    return {"users": users, "total": len(users)}


@app.get("/api/admin/users/{user_id}")
def get_user(user_id: int):
    rec = _all_records().get(user_id)
    if rec is None or rec["deleted"]:
        return _error(404, "not found")
    return _user_payload(rec)


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int):
    rec = _all_records().get(user_id)
    if rec is None or rec["deleted"]:
        return _error(404, "not found")

    # 软删除：仅在 SQLite 打标记，绝不触碰 Redis 对话历史 key。
    try:
        with _db_conn() as conn:
            conn.execute(
                """
                INSERT INTO admin_users (user_id, display_name, known_facts, last_seen, deleted)
                VALUES (?, NULL, NULL, NULL, 1)
                ON CONFLICT(user_id) DO UPDATE SET deleted = 1
                """,
                (user_id,),
            )
    except Exception as exc:
        logger.error(f"Failed to soft-delete user {user_id}: {exc}")
        return _error(500, "failed to delete user")

    return {"status": "deleted", "user_id": user_id}


# ---------------------------------------------------------------------------
# 2.3 会话记录 (Session) viewer -- 只读
# ---------------------------------------------------------------------------
def _format_session_message(msg: Dict[str, Any], index: int) -> Dict[str, Any]:
    meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
    timestamp = meta.get("timestamp", msg.get("timestamp"))
    if timestamp is None:
        timestamp = float(index)
    try:
        timestamp = float(timestamp)
    except (TypeError, ValueError):
        timestamp = float(index)
    return {
        "message_id": meta.get("message_id", msg.get("message_id", index)),
        "role": msg.get("role", "unknown"),
        "content": msg.get("content", ""),
        "timestamp": timestamp,
    }


@app.get("/api/admin/sessions")
def list_sessions(
    chat_id: int = Query(...),
    limit: int = Query(50),
    offset: int = Query(0),
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    r = _get_redis()
    if r is None:
        return {"sessions": [], "total": 0, "chat_id": chat_id}

    items: list = []
    for template in SESSION_KEY_TEMPLATES:
        key = template.format(chat_id=chat_id)
        try:
            raw = r.lrange(key, 0, -1)
        except Exception as exc:
            logger.warning(f"Failed to read session key {key}: {exc}")
            continue
        for i, entry in enumerate(raw):
            msg: Any = entry
            if isinstance(entry, str):
                try:
                    msg = json.loads(entry)
                except (TypeError, ValueError):
                    msg = {"content": entry}
            if isinstance(msg, dict):
                items.append(_format_session_message(msg, i))
        if items:
            break  # 命中第一个有数据的 key 即返回，避免重复叠加

    total = len(items)
    return {"sessions": items[offset:offset + limit], "total": total, "chat_id": chat_id}


# ---------------------------------------------------------------------------
# 2.4 知识库 (Knowledge Base) proxy -- 透传 campus_kb，不重复实现逻辑
# ---------------------------------------------------------------------------
async def _forward(
    base_url: str,
    service: str,
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Forward a request to an upstream loopback service and relay its response."""
    url = f"{base_url}{path}"
    try:
        # trust_env=False: upstreams are loopback services -- never route them
        # through an ambient HTTP(S)_PROXY, otherwise a down service surfaces
        # as a proxy 502 instead of a clean 503.
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            resp = await client.request(method, url, json=body)
    except Exception as exc:  # noqa: BLE001 -- any upstream failure maps to 503
        logger.warning(f"{service} service unavailable at {base_url}: {exc}")
        return _error(503, f"{service} service unavailable")

    try:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except ValueError:
        return JSONResponse(status_code=resp.status_code, content={"error": resp.text})


async def _proxy(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> JSONResponse:
    return await _forward(CAMPUS_KB_URL, "campus_kb", method, path, body)


@app.post("/api/admin/kb/ingest")
async def kb_ingest(payload: Optional[Dict[str, Any]] = Body(None)):
    return await _proxy("POST", "/api/kb/ingest", payload)


@app.get("/api/admin/kb/search")
async def kb_search(
    query: str = Query(...),
    top_k: int = Query(5),
    category: Optional[str] = Query(None),
):
    # 契约要求：Admin 侧 GET，但 campus_kb 侧是 POST。
    body = {"query": query, "top_k": top_k, "category": category}
    return await _proxy("POST", "/api/kb/search", body)


# ---------------------------------------------------------------------------
# 2.5 健康检查
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "admin_backend"}


# ---------------------------------------------------------------------------
# 2.6 日程提醒 (Schedule) management -- 代理至 companion 服务，不重复实现逻辑
# ---------------------------------------------------------------------------
@app.get("/api/admin/schedules")
async def list_schedules(chat_id: int = Query(...)):
    """列出某 chat_id 下的所有日程（透传 companion /api/schedule/list）。"""
    return await _forward(
        COMPANION_URL, "companion", "GET", f"/api/schedule/list?chat_id={chat_id}"
    )


@app.post("/api/admin/schedules")
async def add_schedule(payload: Optional[Dict[str, Any]] = Body(None)):
    """新增日程（透传 companion /api/schedule/add）。

    由 companion 侧完成持久化 + APScheduler 到期注册，Admin 只做反向代理，
    直接写 companion.db 会导致 APScheduler 不知道新日程、提醒不会触发。
    """
    if not payload:
        return _error(400, "empty body")
    return await _forward(COMPANION_URL, "companion", "POST", "/api/schedule/add", payload)


@app.delete("/api/admin/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """删除日程（透传 companion /api/schedule/{schedule_id}）。"""
    return await _forward(
        COMPANION_URL, "companion", "DELETE", f"/api/schedule/{schedule_id}"
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    _init_db()
    uvicorn.run(app, host="0.0.0.0", port=ADMIN_PORT)


# Ensure SQLite schema exists even when launched via `uvicorn main:app`.
_init_db()
