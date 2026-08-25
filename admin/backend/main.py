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
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ruamel.yaml import YAML

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("admin_backend")

# ---------------------------------------------------------------------------
# Paths & configuration (all overridable via env so the panel stays portable)
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent           # admin/backend
REPO_ROOT = BACKEND_DIR.parent.parent                   # repository root

# Load REDIS_URL / REDIS_PASSWORD etc. from a local .env when present.
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:  # python-dotenv is optional at runtime
    pass

PERSONA_DIR = Path(os.getenv("PERSONA_DIR", REPO_ROOT / "config" / "persona"))
DB_PATH = Path(os.getenv("ADMIN_DB_PATH", BACKEND_DIR / "admin.db"))

ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8094"))
CAMPUS_KB_URL = os.getenv("CAMPUS_KB_URL", "http://127.0.0.1:8093").rstrip("/")
COMPANION_URL = os.getenv("COMPANION_URL", "http://127.0.0.1:8096").rstrip("/")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "betteragent_memories")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")

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
# 会话/短期记忆 key 前缀，用于在 Redis 中枚举出所有聊天历史。
SHORT_TERM_KEY_PREFIXES = ("short_term:", "betteragent:short_term:")

_WEB_NAMESPACE_OFFSET = 9_000_000_000_000_000


def _to_web_chat_id(chat_id: int) -> int:
    """Mirror core/webgateway WebNamespaceOffset for Redis/Qdrant lookups.

    WebGateway folds every explicit/random web chat_id into the 9e15+
    namespace; memory service and Go core store data under that namespaced id.
    Admin endpoints receive the base id from the frontend, so we re-apply the
    offset before hitting storage.
    """
    if chat_id <= 0:
        return chat_id
    if chat_id < _WEB_NAMESPACE_OFFSET:
        return chat_id + _WEB_NAMESPACE_OFFSET
    return chat_id


def _from_web_chat_id(chat_id: int) -> int:
    """De-namespace a WebGateway chat_id for display/frontend use."""
    if chat_id >= _WEB_NAMESPACE_OFFSET:
        return chat_id - _WEB_NAMESPACE_OFFSET
    return chat_id

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


def _get_active_persona_id() -> str:
    config_path = REPO_ROOT / "config" / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                doc = _yaml_safe.load(f)
            if isinstance(doc, dict):
                persona_sec = doc.get("persona")
                if isinstance(persona_sec, dict) and persona_sec.get("active"):
                    return str(persona_sec["active"])
        except Exception:
            pass
    return "catgirl"


@app.get("/api/admin/personas")
def list_personas():
    """列出所有人设，摘要字段（id/name/tts_provider/voice_id/is_active）。"""
    active_id = _get_active_persona_id()
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
            p_id = data.get("id") or path.stem
            personas.append({
                "id": p_id,
                "name": data.get("name"),
                "tts_provider": tts.get("provider"),
                "voice_id": tts.get("voice_id"),
                "is_active": (p_id == active_id),
            })
    return {"personas": personas, "active_id": active_id}


@app.post("/api/admin/personas/{persona_id}/activate")
async def activate_persona(persona_id: str):
    """在线将指定人设切换为当前系统全局活跃人设（更新 config.yaml 的 persona.active 节点，并广播 NATS 热重载）。"""
    path = _persona_path(persona_id)
    if path is None or not path.exists():
        return _error(404, "persona not found")

    config_path = REPO_ROOT / "config" / "config.yaml"
    if not config_path.exists():
        return _error(500, "config.yaml not found")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            doc = _yaml_rt.load(f)
        if not isinstance(doc, dict):
            return _error(500, "invalid config.yaml")

        if "persona" not in doc or not isinstance(doc["persona"], dict):
            doc["persona"] = {}
        doc["persona"]["active"] = persona_id

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=config_path.parent,
                prefix=".config.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                _yaml_rt.dump(doc, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, config_path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
    except Exception as exc:
        logger.error(f"Failed to update active persona in config.yaml: {exc}")
        return _error(500, f"failed to activate persona: {exc}")

    # No field patch here -- this is an activation-only update (persona_id
    # only), which PersonaLoader.handle_persona_update treats as "re-read
    # everything" rather than "empty/invalid". See patch_persona above for
    # why a publish failure must surface in the response instead of being
    # silently swallowed.
    hot_reloaded = await _publish_persona_update(persona_id, {})
    return {"status": "ok", "active_id": persona_id, "hot_reload": "ok" if hot_reloaded else "failed"}



@app.get("/api/admin/personas/{persona_id}")
def get_persona(persona_id: str):
    """获取单个人设详情：完整 YAML 解析为 JSON，保留所有顶级字段。"""
    data = _read_persona(persona_id)
    if data is None:
        return _error(404, "not found")
    return data


@app.patch("/api/admin/personas/{persona_id}")
async def patch_persona(persona_id: str, payload: Optional[Dict[str, Any]] = Body(None)):
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

    # YAML on disk is the source of truth and is already updated at this
    # point; the NATS push below only hot-reloads the running cognitive
    # service's in-memory cache. A failure here must not fail the PATCH --
    # but it must also not be silently swallowed, or the caller has no way
    # to know the edit hasn't taken effect yet (see PersonaLoader).
    hot_reloaded = await _publish_persona_update(persona_id, payload)
    return {
        "status": "ok",
        "id": persona_id,
        "hot_reload": "ok" if hot_reloaded else "failed",
    }


@app.post("/api/admin/personas")
def create_persona(payload: Optional[Dict[str, Any]] = Body(None)):
    """根据模板新建人设并生成 YAML 配置文件。"""
    if not payload:
        return _error(400, "empty body")

    persona_id = str(payload.get("id", "")).strip().lower()
    if not persona_id or not _VALID_PERSONA_ID.match(persona_id):
        return _error(400, "Invalid persona id (only alphanumeric, _ and - allowed)")

    path = _persona_path(persona_id)
    if path and path.exists():
        return _error(400, f"Persona '{persona_id}' already exists")

    name = str(payload.get("name", persona_id))
    appearance = str(payload.get("appearance", ""))
    base_prompt = str(payload.get("base_prompt", f"你叫 {name}，是一个AI助手。"))
    sleepy_prompt = str(payload.get("sleepy_prompt", f"你叫 {name}，现在有些犯困。"))
    knowledge_scope = str(payload.get("knowledge_scope", "日常陪伴"))
    forbidden_topics = str(payload.get("forbidden_topics", "违规及敏感话题"))

    tts_provider = str(payload.get("tts_provider", "gpt_sovits"))
    voice_id = str(payload.get("voice_id", f"{persona_id}_voice"))

    doc = {
        "id": persona_id,
        "name": name,
        "appearance": appearance,
        "base_prompt": base_prompt,
        "sleepy_prompt": sleepy_prompt,
        "knowledge_scope": knowledge_scope,
        "forbidden_topics": forbidden_topics,
        "tts": {
            "provider": tts_provider,
            "voice_id": voice_id,
        },
    }

    try:
        PERSONA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        target_path = PERSONA_DIR / f"{persona_id}.yaml"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=PERSONA_DIR,
            prefix=f".{persona_id}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            _yaml_rt.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception as exc:
        logger.error(f"Failed to create persona YAML: {exc}")
        return _error(500, "failed to create persona")

    return {"status": "ok", "id": persona_id}


@app.delete("/api/admin/personas/{persona_id}")
def delete_persona(persona_id: str):
    """删除指定人设 YAML 文件（禁止删除默认 catgirl 或当前活跃人设）。"""
    path = _persona_path(persona_id)
    if path is None or not path.exists():
        return _error(404, "not found")

    if persona_id == "catgirl":
        return _error(400, "Cannot delete default persona 'catgirl'")

    if persona_id == _get_active_persona_id():
        return _error(400, f"Cannot delete currently active persona '{persona_id}'")

    try:
        path.unlink()
    except Exception as exc:
        logger.error(f"Failed to delete persona YAML {path}: {exc}")
        return _error(500, "failed to delete persona")

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
            protocol=2,
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
                # Redis profile keys are namespaced by WebGateway; fold back to
                # the base id so they merge with companion.db / admin SQLite.
                base_user_id = _from_web_chat_id(user_id)
                # Ignore legacy mock test user IDs (e.g. 988776655443322, 555444333222111)
                if str(base_user_id).startswith("9887766") or str(base_user_id).startswith("555444") or str(base_user_id).startswith("987654"):
                    continue
                profiles[base_user_id] = _normalize_profile(base_user_id, raw)

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


def _load_companion_user_facts() -> Dict[int, Dict[str, Any]]:
    facts_map: Dict[int, Dict[str, Any]] = {}
    companion_db = REPO_ROOT / "services" / "companion" / "companion.db"
    if not companion_db.exists():
        return facts_map
    try:
        conn = sqlite3.connect(str(companion_db), timeout=2.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute("SELECT user_id, key, value, created_at FROM user_profile_facts ORDER BY created_at ASC").fetchall()
        for r in rows:
            uid = r["user_id"]
            if uid not in facts_map:
                facts_map[uid] = {
                    "user_id": uid,
                    "display_name": "主人" if uid in (1, 1001) else f"用户{uid}",
                    "known_facts": [],
                    "last_seen": r["created_at"],
                }
            fact_str = f"{r['key']}: {r['value']}"
            if fact_str not in facts_map[uid]["known_facts"]:
                facts_map[uid]["known_facts"].append(fact_str)
        conn.close()
    except Exception as exc:
        logger.warning(f"Failed to read companion.db user_profile_facts: {exc}")
    return facts_map


def _all_records() -> Dict[int, Dict[str, Any]]:
    """Merge companion SQLite facts, Redis profiles, and Admin soft-delete flags."""
    r = _get_redis()
    redis_profiles = _redis_user_profiles(r) if r else {}
    sqlite_users = _load_sqlite_users()
    companion_facts = _load_companion_user_facts()

    merged: Dict[int, Dict[str, Any]] = {}
    for uid, c in companion_facts.items():
        merged[uid] = {
            "user_id": uid,
            "display_name": c["display_name"],
            "known_facts": c["known_facts"],
            "last_seen": c["last_seen"],
            "deleted": False,
        }
    for uid, u in sqlite_users.items():
        prev = merged.get(uid)
        merged[uid] = {
            "user_id": uid,
            "display_name": u["display_name"] or (prev["display_name"] if prev else f"用户{uid}"),
            "known_facts": u["known_facts"] or (prev["known_facts"] if prev else []),
            "last_seen": u["last_seen"] or (prev["last_seen"] if prev else None),
            "deleted": u["deleted"],
        }
    for uid, p in redis_profiles.items():
        prev = merged.get(uid)
        merged[uid] = {
            "user_id": uid,
            "display_name": p["display_name"] or (prev["display_name"] if prev else f"用户{uid}"),
            "known_facts": p["known_facts"] or (prev["known_facts"] if prev else []),
            "last_seen": p["last_seen"] or (prev["last_seen"] if prev else None),
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
    chat_id: int = Query(0),
    limit: int = Query(50),
    offset: int = Query(0),
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    r = _get_redis()
    if r is None:
        return {"sessions": [], "total": 0, "chat_id": chat_id, "active_chats": []}

    # Discover all active chat_ids in Redis
    active_chats: list = []
    try:
        for prefix in ("betteragent:short_term:", "short_term:"):
            for k in r.scan_iter(f"{prefix}*"):
                try:
                    cid = int(k.split(":")[-1])
                    base_cid = _from_web_chat_id(cid)
                    if base_cid not in active_chats:
                        active_chats.append(base_cid)
                except ValueError:
                    pass
    except Exception:
        pass

    # Filter out obvious mock test IDs (e.g. 555444333222111, 987654321012345)
    real_chats = [c for c in active_chats if not (str(c).startswith("555444") or str(c).startswith("987654"))]

    # If chat_id is 0 or unspecified, pick the first real active_chat (or 1001 default)
    if not chat_id:
        chat_id = real_chats[0] if real_chats else (active_chats[0] if active_chats else 1001)


    storage_chat_id = _to_web_chat_id(chat_id)
    items: list = []
    for template in SESSION_KEY_TEMPLATES:
        key = template.format(chat_id=storage_chat_id)
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
    return {"sessions": items[offset:offset + limit], "total": total, "chat_id": chat_id, "active_chats": active_chats}


@app.get("/api/admin/sessions/overview")
def list_session_overview():
    """Enumerate every chat that has a Redis short-term history.

    `GET /api/admin/sessions` requires a concrete `chat_id`; a history page
    needs the reverse operation first (which chat_ids actually exist). This
    endpoint scans the two supported key prefixes and returns one lightweight
    summary per chat, newest first.
    """
    r = _get_redis()
    if r is None:
        return {"sessions": [], "total": 0}

    sessions: List[Dict[str, Any]] = []
    seen: set = set()
    for prefix in SHORT_TERM_KEY_PREFIXES:
        try:
            for key in r.scan_iter(f"{prefix}*"):
                chat_id = _chat_id_from_session_key(key)
                base_chat_id = _from_web_chat_id(chat_id) if chat_id is not None else None
                if base_chat_id is None or base_chat_id in seen:
                    continue
                seen.add(base_chat_id)

                count = 0
                preview = ""
                last_timestamp: Optional[float] = None
                try:
                    count = int(r.llen(key) or 0)
                    raw_tail = r.lrange(key, -1, -1)
                    if raw_tail:
                        last: Any = raw_tail[0]
                        if isinstance(last, str):
                            try:
                                last = json.loads(last)
                            except (TypeError, ValueError):
                                last = {"content": last}
                        if isinstance(last, dict):
                            preview = str(last.get("content", ""))[:120]
                            last_timestamp = _format_session_message(last, 0).get("timestamp")
                except Exception as exc:
                    logger.warning(f"Failed to summarize session key {key}: {exc}")

                sessions.append({
                    # Display the base id so the frontend stays consistent with
                    # the chat_id it knows (localStorage / URL query param).
                    "chat_id": _from_web_chat_id(chat_id),
                    "message_count": count,
                    "last_timestamp": last_timestamp,
                    "preview": preview,
                })
        except Exception as exc:
            logger.warning(f"Failed to scan session keys with prefix {prefix}: {exc}")

    sessions.sort(key=lambda item: item.get("last_timestamp") or 0, reverse=True)
    return {"sessions": sessions, "total": len(sessions)}


def _chat_id_from_session_key(key: str) -> Optional[int]:
    for prefix in SHORT_TERM_KEY_PREFIXES:
        if key.startswith(prefix):
            try:
                return int(key[len(prefix):])
            except ValueError:
                return None
    return None


def _read_short_term_list(user_id: int, r: Any) -> List[Dict[str, Any]]:
    """Read one user's short-term messages, trying both key spellings."""
    items: List[Dict[str, Any]] = []
    storage_user_id = _to_web_chat_id(user_id)
    for key in (f"betteragent:short_term:{storage_user_id}", f"short_term:{storage_user_id}"):
        try:
            raw = r.lrange(key, 0, -1)
        except Exception as exc:
            logger.warning(f"Failed to read short-term key {key}: {exc}")
            continue
        for index, entry in enumerate(raw):
            msg: Any = entry
            if isinstance(entry, str):
                try:
                    msg = json.loads(entry)
                except (TypeError, ValueError):
                    msg = {"content": entry}
            if isinstance(msg, dict):
                items.append(_format_session_message(msg, index))
        if items:
            break
    return items


# ---------------------------------------------------------------------------
# 2.3b 记忆管理 (Memory) -- 短期/长期/画像，面向 stage-web 设置页
# ---------------------------------------------------------------------------
@app.get("/api/admin/memory/short-term")
def get_short_term_memory(
    user_id: int = Query(...),
    limit: int = Query(50),
    offset: int = Query(0),
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    r = _get_redis()
    if r is None:
        return {"messages": [], "total": 0, "user_id": user_id}

    items = _read_short_term_list(user_id, r)
    total = len(items)
    return {"messages": items[offset:offset + limit], "total": total, "user_id": user_id}


@app.delete("/api/admin/memory/short-term")
def clear_short_term_memory(user_id: int = Query(...)):
    r = _get_redis()
    if r is None:
        return _error(503, "redis unavailable")

    storage_user_id = _to_web_chat_id(user_id)
    keys = (
        f"betteragent:short_term:{storage_user_id}",
        f"short_term:{storage_user_id}",
        f"betteragent:consolidate_cursor:{storage_user_id}",
    )
    for key in keys:
        try:
            r.delete(key)
        except Exception as exc:
            logger.warning(f"Failed to delete short-term key {key}: {exc}")
    return {"status": "cleared", "user_id": user_id}


@app.get("/api/admin/memory/long-term")
def get_long_term_memory(
    user_id: int = Query(...),
    query: Optional[str] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    try:
        storage_user_id = _to_web_chat_id(user_id)
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            headers = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else {}
            resp = client.post(
                f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll",
                headers=headers,
                json={
                    "filter": {"must": [{"key": "user_id", "match": {"value": int(storage_user_id)}}]},
                    "limit": limit,
                    "offset": offset,
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            if resp.status_code != 200:
                return {"memories": [], "total": 0, "user_id": user_id}
            points = (resp.json().get("result") or {}).get("points", [])
    except Exception as exc:
        logger.warning(f"Qdrant unavailable while listing long-term memory: {exc}")
        return {"memories": [], "total": 0, "user_id": user_id}

    memories: List[Dict[str, Any]] = []
    needle = (query or "").strip().lower()
    for point in points:
        payload = point.get("payload") or {}
        text = str(payload.get("text", ""))
        if needle and needle not in text.lower():
            continue
        memories.append({
            "id": point.get("id"),
            "text": text,
            "timestamp": payload.get("timestamp"),
            "metadata": payload.get("metadata") or {},
        })

    return {"memories": memories, "total": len(memories), "user_id": user_id}


@app.delete("/api/admin/memory/long-term/{point_id}")
def delete_long_term_memory(point_id: str):
    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            headers = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else {}
            resp = client.post(
                f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/delete",
                headers=headers,
                json={"points": [point_id]},
            )
            if resp.status_code == 200:
                return {"status": "deleted", "id": point_id}
            return _error(404, "not found")
    except Exception as exc:
        logger.warning(f"Qdrant unavailable while deleting long-term memory: {exc}")
        return _error(503, "qdrant unavailable")


class MemoryProfileUpdatePayload(BaseModel):
    display_name: Optional[str] = None
    known_facts: Optional[List[str]] = None


@app.get("/api/admin/memory/profile")
def get_memory_profile(user_id: int = Query(...)):
    rec = _all_records().get(user_id)
    if rec is None:
        rec = _normalize_profile(user_id, {})
    return {
        "user_id": user_id,
        "display_name": rec.get("display_name") or f"用户{user_id}",
        "known_facts": rec.get("known_facts") or [],
        "last_seen": rec.get("last_seen"),
    }


@app.put("/api/admin/memory/profile/{user_id}")
def update_memory_profile(user_id: int, payload: MemoryProfileUpdatePayload):
    r = _get_redis()
    if r is None:
        return _error(503, "redis unavailable")

    storage_user_id = _to_web_chat_id(user_id)
    key = f"betteragent:profile:{storage_user_id}"
    try:
        if payload.display_name is not None:
            r.hset(key, "preferred_name", payload.display_name)
            r.hset(key, "display_name", payload.display_name)
        if payload.known_facts is not None:
            encoded = json.dumps(payload.known_facts, ensure_ascii=False)
            # `known_facts` feeds the admin UI; `likes` mirrors it into the
            # memory service's own profile prompt so both readers stay in sync.
            r.hset(key, "known_facts", encoded)
            r.hset(key, "likes", encoded)
    except Exception as exc:
        logger.warning(f"Failed to update memory profile {user_id}: {exc}")
        return _error(500, "failed to update profile")

    return get_memory_profile(user_id)


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
async def list_schedules(chat_id: int = Query(0)):
    """列出某 chat_id 下的所有日程（透传 companion /api/schedule/list）。

    与 sessions/用户画像等端点同样的约定：管理界面里的 chat_id 是"友好的"
    原始编号，落到 companion 存储前要套上 WebNamespaceOffset（见
    _to_web_chat_id）。此前这里漏做了这一步，导致管理面板新建/查询的日程
    跟真实 Web 会话用的 chat_id 对不上——不只是面板看不到浏览器里创建的日程，
    companion 的 ScheduleService._fire 到点触发提醒时还会拿"< WebNamespaceOffset"
    误判成 Telegram 频道，把提醒推错地方（见 services/companion/schedule_service.py）。
    """
    if not chat_id:
        chat_id = 1001
    return await _forward(
        COMPANION_URL, "companion", "GET", f"/api/schedule/list?chat_id={_to_web_chat_id(chat_id)}"
    )


@app.post("/api/admin/schedules")
async def add_schedule(payload: Optional[Dict[str, Any]] = Body(None)):
    """新增日程（透传 companion /api/schedule/add）。

    由 companion 侧完成持久化 + APScheduler 到期注册，Admin 只做反向代理，
    直接写 companion.db 会导致 APScheduler 不知道新日程、提醒不会触发。

    chat_id 同 list_schedules 一样需要套上 WebNamespaceOffset 再转发。
    """
    if not payload:
        return _error(400, "empty body")
    if payload.get("chat_id"):
        payload = {**payload, "chat_id": _to_web_chat_id(payload["chat_id"])}
    return await _forward(COMPANION_URL, "companion", "POST", "/api/schedule/add", payload)


@app.delete("/api/admin/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """删除日程（透传 companion /api/schedule/{schedule_id}）。"""
    return await _forward(
        COMPANION_URL, "companion", "DELETE", f"/api/schedule/{schedule_id}"
    )


# ---------------------------------------------------------------------------
# 2.7 系统配置与 API 密钥管理 (BYOK 模式)
# ---------------------------------------------------------------------------
# 用户不应直接修改仓库根目录的 config/config.yaml 或 .env；API Key / 默认
# Provider / 网络代理统一通过 Admin Panel 维护。.env 与 config.yaml 的读写
# 逻辑全部封装在本节内，不触碰其它微服务边界（消费端热刷新由技术总监在各
# 服务内实现，Admin 只发布 agent.config.reloaded）。
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
CONFIG_EXAMPLE = REPO_ROOT / "config" / "config.yaml.example"
RELOAD_SUBJECT = "agent.config.reloaded"

# 面板管理的 LLM Provider 范围（与 .env.example / config.yaml.example 的 llm
# 段 / ProviderFactory 实际使用对齐）。cosyvoice（TTS）及 openai/deepseek/
# ollama/vllm 不在面板管理范围。
PROVIDER_DEFS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "env_key": "GEMINI_API_KEY",
        "model_path": "llm.gemini.model",
        "default_model": "gemini-2.5-flash",
    },
    "claude": {
        "env_key": "CLAUDE_API_KEY",
        "model_path": "llm.claude.model",
        "default_model": "claude-3-5-sonnet-20241022",
    },
    "qwen": {
        "env_key": "QWEN_API_KEY",
        "model_path": "llm.qwen.model",
        "default_model": None,
    },
}


def _atomic_write_text(path: Path, content: str) -> None:
    """Crash-safe text write: same-dir temp file + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
            f.write(content)
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


def _read_env(path: Path) -> Dict[str, str]:
    """Parse a KEY=VALUE .env file into a dict (no shell expansion)."""
    env: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    env[key] = value
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"Failed to read env file {path}: {exc}")
    return env


def _upsert_env(updates: Dict[str, str]) -> None:
    """Write API keys into the repo-root .env, preserving unrelated keys & comments.

    Seeds .env from .env.example when the file is missing so other services'
    required defaults (NATS creds, Redis password, ...) stay present. Both files
    are gitignored, so this never pollutes the PR diff.
    """
    if not ENV_PATH.exists():
        if ENV_EXAMPLE.exists():
            shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        else:
            _atomic_write_text(ENV_PATH, "")

    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as exc:
        logger.error(f"Failed to read .env for update: {exc}")
        raise

    pending = dict(updates)
    out: List[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}\n")
        else:
            out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={value}\n")

    _atomic_write_text(ENV_PATH, "".join(out))


def _read_config(safe: bool = True) -> Dict[str, Any]:
    """Parse config/config.yaml, falling back to the committed example."""
    path = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            loader = _yaml_safe if safe else _yaml_rt
            doc = loader.load(f)
        return doc if isinstance(doc, dict) else {}
    except Exception as exc:
        logger.error(f"Failed to parse config {path}: {exc}")
        return {}


def _get_dotted(doc: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _ensure_config_file() -> None:
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_EXAMPLE.exists():
        shutil.copyfile(CONFIG_EXAMPLE, CONFIG_PATH)
    else:
        _atomic_write_text(CONFIG_PATH, "")


def _write_config_rt(doc: Any) -> None:
    """Persist a ruamel round-trip doc back to config/config.yaml atomically."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix=".config.yaml.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            _yaml_rt.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _mask_key(key: str) -> Optional[str]:
    if not key or key.startswith("your_"):
        return None
    if len(key) <= 12:
        return "***"
    return f"{key[:6]}***{key[-3:]}"


def _get_provider_key(name: str) -> str:
    """Effective API key for a provider: root .env first, os.environ fallback."""
    env_key = PROVIDER_DEFS[name]["env_key"]
    value = _read_env(ENV_PATH).get(env_key) or os.getenv(env_key, "")
    if not value or value.startswith("your_"):
        return ""
    return value


async def _nats_publish(subject: str, payload: Dict[str, Any]) -> bool:
    """Best-effort NATS publish; never raises -- callers treat failure as non-fatal."""
    try:
        import nats  # local import: backend still runs if nats-py is missing
    except ImportError:
        logger.warning(f"nats-py not installed; {subject} not published")
        return False

    cfg = _read_config(safe=True)
    nats_url = os.getenv(
        "NATS_URL", _get_dotted(cfg, "infrastructure.nats_url", "nats://127.0.0.1:4222")
    )
    root_env = _read_env(ENV_PATH)
    nats_user = os.getenv("NATS_USER") or root_env.get("NATS_USER", "")
    nats_password = os.getenv("NATS_PASSWORD") or root_env.get("NATS_PASSWORD", "")
    if not nats_user or not nats_password:
        logger.warning(f"NATS_USER / NATS_PASSWORD not configured; {subject} not published")
        return False

    try:
        # NATS is always local (127.0.0.1, see docs/SECURITY.md §2.8) -- a
        # healthy broker responds in milliseconds, so a short timeout still
        # comfortably covers real connectivity while keeping the worst case
        # (broker down/unreachable) from blocking the calling PATCH request
        # for several seconds.
        nc = await nats.connect(
            nats_url,
            user=nats_user,
            password=nats_password,
            max_reconnect_attempts=1,
            connect_timeout=1,
        )
        envelope = {"subject": subject, "source": "admin_backend", "payload": payload}
        await nc.publish(subject, json.dumps(envelope, ensure_ascii=False).encode())
        await nc.flush(timeout=1)
        await nc.close()
        logger.info(f"Published {subject}")
        return True
    except Exception as exc:
        logger.warning(f"Failed to publish {subject}: {exc}")
        return False


async def _publish_config_reloaded(payload: Dict[str, Any]) -> bool:
    """Best-effort NATS publish of agent.config.reloaded; never blocks PATCH."""
    return await _nats_publish(RELOAD_SUBJECT, payload)


PERSONA_UPDATE_SUBJECT = "agent.persona.update"


async def _publish_persona_update(persona_id: str, patch: Dict[str, Any]) -> bool:
    """Best-effort NATS publish of agent.persona.update; never blocks PATCH.

    Consumed by shared.persona_loader.PersonaLoader.handle_persona_update on
    the cognitive service, which invalidates its in-memory persona cache so
    the YAML edit takes effect without a restart.
    """
    return await _nats_publish(PERSONA_UPDATE_SUBJECT, {"persona_id": persona_id, **patch})


@app.get("/api/admin/config")
def get_admin_config():
    """2.7 GET: 返回默认 Provider、网络代理与各 Provider 脱敏 key 状态。"""
    cfg = _read_config(safe=True)
    llm = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
    network = cfg.get("network") if isinstance(cfg.get("network"), dict) else {}
    default_provider = llm.get("default_provider") or "gemini"

    providers = []
    for name, meta in PROVIDER_DEFS.items():
        model = _get_dotted(cfg, meta["model_path"]) or meta["default_model"]
        key = _get_provider_key(name)
        providers.append({
            "name": name,
            "model": model,
            "key_masked": _mask_key(key),
            "key_set": bool(key),
        })

    return {
        "default_provider": default_provider,
        "network": {
            "http_proxy": network.get("http_proxy", ""),
            "https_proxy": network.get("https_proxy", ""),
        },
        "providers": providers,
    }


@app.patch("/api/admin/config")
async def patch_admin_config(payload: Optional[Dict[str, Any]] = Body(None)):
    """2.7 PATCH: 更新 Provider key/model、默认 Provider 与网络代理，写回 .env 与
    config/config.yaml，并向 NATS 发布 agent.config.reloaded（best-effort）。"""
    if not payload:
        return _error(400, "empty body")

    allowed_fields = {"default_provider", "network", "providers"}
    for field in payload:
        if field not in allowed_fields:
            return _error(400, f"Forbidden field: {field}")

    # 1) 白名单校验
    default_provider = payload.get("default_provider")
    if default_provider is not None and default_provider not in PROVIDER_DEFS:
        return _error(400, f"Unknown default_provider: {default_provider}")

    network = payload.get("network")
    if network is not None:
        if not isinstance(network, dict):
            return _error(400, "network must be an object")
        for key in network:
            if key not in ("http_proxy", "https_proxy"):
                return _error(400, f"Forbidden network field: {key}")
            if not isinstance(network[key], str):
                return _error(400, f"network.{key} must be a string")

    providers = payload.get("providers")
    if providers is not None:
        if not isinstance(providers, dict):
            return _error(400, "providers must be an object")
        for name, update in providers.items():
            if name not in PROVIDER_DEFS:
                return _error(400, f"Unknown provider: {name}")
            if not isinstance(update, dict):
                return _error(400, f"providers.{name} must be an object")
            for key in update:
                if key not in ("api_key", "model"):
                    return _error(400, f"Forbidden provider field: {name}.{key}")
                if not isinstance(update[key], str):
                    return _error(400, f"providers.{name}.{key} must be a string")

    # 2) 先写 .env（密钥是更关键的状态）
    if providers:
        env_updates = {
            PROVIDER_DEFS[name]["env_key"]: update["api_key"]
            for name, update in providers.items()
            if isinstance(update, dict) and "api_key" in update
        }
        if env_updates:
            try:
                _upsert_env(env_updates)
            except Exception as exc:
                logger.error(f"Failed to write .env: {exc}")
                return _error(500, "failed to update .env")

    # 3) 再写 config.yaml（ruamel round-trip 保留注释与字段顺序）
    try:
        doc = _read_config(safe=False)
        _ensure_config_file()
        if default_provider is not None:
            doc.setdefault("llm", {})["default_provider"] = default_provider
        if network is not None:
            net = doc.setdefault("network", {})
            for key, value in network.items():
                net[key] = value
        if providers:
            llm = doc.setdefault("llm", {})
            for name, update in providers.items():
                if isinstance(update, dict) and "model" in update:
                    llm.setdefault(name, {})["model"] = update["model"]
        _write_config_rt(doc)
    except Exception as exc:
        logger.error(f"Failed to write config.yaml: {exc}")
        return _error(500, "failed to update config.yaml")

    # 4) 发布热刷新信号（失败不阻断配置落盘）
    reloaded = False
    try:
        reloaded = await _publish_config_reloaded({
            "default_provider": default_provider,
            "network": network,
            "providers": providers,
        })
    except Exception as exc:
        logger.warning(f"Config reload publish failed: {exc}")

    return {"status": "ok", "reloaded": reloaded}


def _raise_http(resp: httpx.Response) -> None:
    """raise_for_status() with the upstream's error body included for readability."""
    if resp.status_code >= 400:
        detail = (resp.text or resp.reason_phrase or "").strip().replace("\n", " ")[:200]
        raise RuntimeError(
            f"HTTP {resp.status_code}: {detail}" if detail else f"HTTP {resp.status_code}"
        )


async def _probe_models(provider: str, api_key: str, proxy: Optional[str]) -> List[str]:
    """Call the provider's public models REST endpoint; return model ids."""
    kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(15.0), "trust_env": False}
    if proxy:
        kwargs["proxy"] = proxy

    if provider == "gemini":
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
            )
        _raise_http(resp)
        data = resp.json()
        return sorted(
            str(m["name"]).removeprefix("models/")
            for m in data.get("models", [])
            if isinstance(m, dict) and m.get("name")
        )

    if provider == "claude":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get("https://api.anthropic.com/v1/models", headers=headers)
        _raise_http(resp)
        data = resp.json()
        return sorted(
            str(m["id"])
            for m in data.get("data", [])
            if isinstance(m, dict) and m.get("id")
        )

    # qwen: OpenAI-compatible endpoint. Probe the base_url configured in
    # config.yaml -- the runtime may point at e.g. the Alibaba TokenPlan
    # endpoint (token-plan.cn-beijing.maas.aliyuncs.com), whose keys are
    # rejected by the standard DashScope host. Fall back to the standard
    # endpoint only when no base_url is configured.
    headers = {"Authorization": f"Bearer {api_key}"}
    cfg = _read_config(safe=True)
    base = (
        _get_dotted(cfg, "llm.qwen.base_url")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    url = base.rstrip("/") + "/models"
    async with httpx.AsyncClient(**kwargs) as client:
        resp = await client.get(url, headers=headers)
    _raise_http(resp)
    data = resp.json()
    return sorted(
        str(m["id"])
        for m in data.get("data", [])
        if isinstance(m, dict) and m.get("id")
    )


@app.post("/api/admin/config/test-key")
async def test_admin_key(payload: Optional[Dict[str, Any]] = Body(None)):
    """2.7 test-key: 连通性测试，返回 HTTP 延迟与可用模型列表（不保存任何配置）。"""
    if not payload or not isinstance(payload, dict):
        return _error(400, "empty body")

    provider = payload.get("provider")
    if provider not in PROVIDER_DEFS:
        return _error(400, f"Unknown provider: {provider}")

    api_key = payload.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        return _error(400, "api_key must be a string")
    key = (api_key or "").strip() or _get_provider_key(provider)
    if not key:
        return _error(400, f"no api key configured for provider: {provider}")

    cfg = _read_config(safe=True)
    network = cfg.get("network") if isinstance(cfg.get("network"), dict) else {}
    proxy = network.get("https_proxy") or network.get("http_proxy") or None

    start = time.perf_counter()
    try:
        models = await _probe_models(provider, key, proxy)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"provider": provider, "ok": True, "latency_ms": latency_ms, "models": models[:20]}
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        detail = str(exc) or type(exc).__name__
        return {"provider": provider, "ok": False, "latency_ms": latency_ms, "error": detail}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    _init_db()
    uvicorn.run(app, host="0.0.0.0", port=ADMIN_PORT)


# Ensure SQLite schema exists even when launched via `uvicorn main:app`.
_init_db()
