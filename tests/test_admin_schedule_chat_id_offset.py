"""Regression coverage for admin/backend's schedule proxy endpoints applying
the same WebNamespaceOffset convention that sessions/user-profile endpoints
already use (see admin.backend.main._to_web_chat_id).

Without this, a schedule created or queried through the admin panel with a
"friendly" chat_id (e.g. 1001) is stored/looked-up under a different key than
what the live web session actually uses (WEB_NAMESPACE_OFFSET + 1001) --
not only invisible in the Web UI, but companion's ScheduleService._fire also
misclassifies it as a Telegram-channel reminder when it fires, since it
decides the channel purely from whether chat_id >= WebNamespaceOffset.

These tests mock out admin.backend.main._forward (the thin httpx passthrough
to the companion service) so they don't need a live companion instance --
they only assert on what chat_id _forward actually gets called with.
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


def test_list_schedules_forwards_web_namespaced_chat_id(client):
    with patch("admin.backend.main._forward", new_callable=AsyncMock) as mock_forward:
        mock_forward.return_value = {"schedules": [], "total": 0}
        resp = client.get("/api/admin/schedules", params={"chat_id": 1001})
        assert resp.status_code == 200

    assert mock_forward.await_count == 1
    _, _, _, path = mock_forward.await_args.args
    assert "chat_id=9000000000001001" in path
    assert "chat_id=1001" not in path.replace("9000000000001001", "")


def test_list_schedules_default_chat_id_is_also_web_namespaced(client):
    with patch("admin.backend.main._forward", new_callable=AsyncMock) as mock_forward:
        mock_forward.return_value = {"schedules": [], "total": 0}
        resp = client.get("/api/admin/schedules")
        assert resp.status_code == 200

    _, _, _, path = mock_forward.await_args.args
    assert f"chat_id={_to_web_chat_id(1001)}" in path


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
