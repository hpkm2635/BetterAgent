"""Regression coverage for /api/admin/sessions/overview's channel labeling.

The endpoint already discovered every chat_id with a Redis short-term
history (Telegram and Web alike -- WebGateway-namespaced ids get folded back
to their base id via _from_web_chat_id), but never exposed which channel a
given chat_id actually came from once folded, and the admin frontend never
called this endpoint at all -- an admin operator had no way to browse "who
is currently chatting" (Telegram sessions included), only to guess/type a
chat_id they already knew into the manual session-lookup box.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import admin.backend.main as admin_main
from admin.backend.main import app, _to_web_chat_id


@pytest.fixture()
def client():
    with patch.object(admin_main, "ADMIN_SECRET_KEY", ""):
        yield TestClient(app)


def _fake_redis(keys_and_lists: dict):
    """keys_and_lists: {redis_key: [json_str, ...]}, last item = most recent."""
    fake = MagicMock()

    def scan_iter(pattern):
        prefix = pattern.rstrip("*")
        return [k for k in keys_and_lists if k.startswith(prefix)]

    def llen(key):
        return len(keys_and_lists.get(key, []))

    def lrange(key, start, end):
        items = keys_and_lists.get(key, [])
        if start == -1 and end == -1:
            return items[-1:] if items else []
        return items

    fake.scan_iter.side_effect = scan_iter
    fake.llen.side_effect = llen
    fake.lrange.side_effect = lrange
    return fake


def test_session_overview_labels_telegram_and_web_channels_correctly(client):
    telegram_chat_id = 123456789
    web_base_chat_id = 1242398
    web_storage_chat_id = _to_web_chat_id(web_base_chat_id)

    fake_redis = _fake_redis({
        f"short_term:{telegram_chat_id}": [
            json.dumps({"role": "user", "content": "在吗", "metadata": {"timestamp": 100.0}}),
        ],
        f"betteragent:short_term:{web_storage_chat_id}": [
            json.dumps({"role": "user", "content": "hi", "metadata": {"timestamp": 200.0}}),
        ],
    })

    with patch.object(admin_main, "_get_redis", return_value=fake_redis):
        resp = client.get("/api/admin/sessions/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_chat_id = {s["chat_id"]: s for s in body["sessions"]}

    assert by_chat_id[telegram_chat_id]["channel"] == "telegram"
    assert by_chat_id[telegram_chat_id]["preview"] == "在吗"
    assert by_chat_id[web_base_chat_id]["channel"] == "web"
    assert by_chat_id[web_base_chat_id]["preview"] == "hi"
    # The web chat_id must be de-namespaced back to its base id, not the
    # raw 9e15+-offset storage id.
    assert web_base_chat_id != web_storage_chat_id
    assert web_base_chat_id in by_chat_id


def test_session_overview_empty_when_redis_unavailable(client):
    with patch.object(admin_main, "_get_redis", return_value=None):
        resp = client.get("/api/admin/sessions/overview")

    assert resp.status_code == 200
    assert resp.json() == {"sessions": [], "total": 0}
