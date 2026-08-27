"""Regression coverage for admin/backend's schedule proxy endpoints applying
the same WebNamespaceOffset convention that sessions/user-profile endpoints
already use (see admin.backend.main._to_web_chat_id).

A user_id/chat_id is ambiguous: it may be a Telegram user id (stored as-is,
e.g. chat_id=777111) or a web session's base id (stored under
WEB_NAMESPACE_OFFSET + base, e.g. chat_id=9000000000001001). The admin panel
must therefore query BOTH interpretations and merge by schedule_id, so that
schedules created in the user frontend (web, namespaced chat_id) and Telegram
(raw chat_id) both show up when looking a user up by user_id.

Without the offset handling, a schedule created or queried through the admin
panel with a "friendly" chat_id (e.g. 1001) is stored/looked-up under a
different key than what the live web session actually uses
(WEB_NAMESPACE_OFFSET + 1001) -- not only invisible in the Web UI, but
companion's ScheduleService._fire also misclassifies it as a Telegram-channel
reminder when it fires, since it decides the channel purely from whether
chat_id >= WebNamespaceOffset.

These tests mock out the admin backend's httpx passthrough
(_companion_schedule_json / _forward) so they don't need a live companion
instance -- they only assert on what queries actually get issued.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import admin.backend.main as admin_main
from admin.backend.main import app, _to_web_chat_id


@pytest.fixture()
def client():
    # These tests are about the chat_id offset logic, not auth -- disable
    # enforce_admin_token regardless of whatever ADMIN_SECRET_KEY happens to
    # be set in this environment's admin/backend/.env.
    with patch.object(admin_main, "ADMIN_SECRET_KEY", ""):
        yield TestClient(app)


def test_to_web_chat_id_is_idempotent_and_leaves_negative_or_zero_alone():
    assert _to_web_chat_id(1001) == 9_000_000_000_001_001
    # Already-namespaced input must pass through unchanged.
    assert _to_web_chat_id(_to_web_chat_id(1001)) == _to_web_chat_id(1001)
    assert _to_web_chat_id(0) == 0
    assert _to_web_chat_id(-5) == -5


def test_list_schedules_queries_both_web_and_raw_chat_id_candidates(client):
    with patch("admin.backend.main._companion_schedule_json", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        resp = client.get("/api/admin/schedules", params={"chat_id": 1001})
        assert resp.status_code == 200

    queries = [call.args[0] for call in mock_fetch.await_args_list]
    assert f"?chat_id={_to_web_chat_id(1001)}" in queries
    assert "?chat_id=1001" in queries


def test_list_schedules_by_user_id_queries_user_id_and_chat_id_candidates(client):
    with patch("admin.backend.main._companion_schedule_json", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        resp = client.get("/api/admin/schedules", params={"user_id": 777000})
        assert resp.status_code == 200

    queries = [call.args[0] for call in mock_fetch.await_args_list]
    # user_id column, raw chat_id (Telegram), and web-namespaced chat_id all tried.
    assert "?user_id=777000" in queries
    assert "?chat_id=777000" in queries
    assert "?chat_id=9000000000777000" in queries


def test_list_schedules_merges_duplicate_schedules_by_id(client):
    dup = {
        "schedule_id": "abc",
        "chat_id": 9000000000001001,
        "user_id": 1001,
        "title": "赶火车回学校",
        "remind_at": "2026-08-30 20:30:00",
        "status": "scheduled",
    }
    with patch("admin.backend.main._companion_schedule_json", new_callable=AsyncMock) as mock_fetch:
        # user_id query and the namespaced chat_id query both return the same row.
        mock_fetch.side_effect = [[dup], [], [dup]]
        resp = client.get("/api/admin/schedules", params={"user_id": 1001})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["schedules"]) == 1
        assert body["schedules"][0]["schedule_id"] == "abc"


def test_list_schedules_without_ids_forwards_list_all(client):
    with patch("admin.backend.main._forward", new_callable=AsyncMock) as mock_forward:
        mock_forward.return_value = {"schedules": [], "total": 0}
        resp = client.get("/api/admin/schedules")
        assert resp.status_code == 200

    assert mock_forward.await_count == 1
    _, _, _, path = mock_forward.await_args.args
    assert path == "/api/schedule/list"


def test_add_schedule_rewrites_payload_chat_id_before_forwarding(client):
    with patch("admin.backend.main._forward", new_callable=AsyncMock) as mock_forward:
        mock_forward.return_value = {"schedule_id": "abc123", "status": "scheduled"}
        resp = client.post(
            "/api/admin/schedules",
            json={
                "chat_id": 1001,
                "user_id": 1,
                "title": "赶火车回学校",
                "remind_at": "2026-08-30 20:30:00",
                "note": "带身份证",
            },
        )
        assert resp.status_code == 200

    assert mock_forward.await_count == 1
    _, _, _, _, sent_body = mock_forward.await_args.args
    assert sent_body["chat_id"] == 9_000_000_000_001_001
    # Every other field must pass through unchanged.
    assert sent_body["title"] == "赶火车回学校"
    assert sent_body["user_id"] == 1


def test_add_schedule_without_chat_id_does_not_crash(client):
    # Defensive: a malformed payload missing chat_id shouldn't raise inside
    # the endpoint -- it should just forward whatever it got.
    with patch("admin.backend.main._forward", new_callable=AsyncMock) as mock_forward:
        mock_forward.return_value = {"error": "missing chat_id"}
        resp = client.post("/api/admin/schedules", json={"title": "no chat id"})
        assert resp.status_code == 200

    _, _, _, _, sent_body = mock_forward.await_args.args
    assert "chat_id" not in sent_body
