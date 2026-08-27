"""Regression coverage for ScheduleService firing a proactive turn instead
of a fixed-template chat message.

Before this change, a due reminder published a hardcoded-text
ActionDecisionPayload directly to agent.action.{channel}.{chat_id}. Now it
publishes a lightweight agent.schedule.fired event that Go Core's
WebGateway turns into a real proactive LLM turn (engine.PublishProactiveTurn)
-- the catgirl generates the reminder message herself, in character, instead
of a robotic template. These tests only cover the Python side (does
ScheduleService publish the right envelope to the right subject with the
right payload); the Go-side handler is covered by go test in
core/internal/webgateway.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.companion.database as database
from shared.subjects import SUBJECT_SCHEDULE_FIRED


@pytest.fixture()
def schedule_service(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_DB_PATH", str(tmp_path / "companion_test.db"))
    database.init_db()
    monkeypatch.setenv("NATS_USER", "test_user")
    monkeypatch.setenv("NATS_PASSWORD", "test_password")

    from services.companion.schedule_service import ScheduleService
    return ScheduleService()


def _insert_schedule(chat_id: int, title: str = "赶火车回学校", note: str = "带身份证") -> str:
    schedule_id = "sched-test-1"
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO schedules (schedule_id, chat_id, user_id, title, remind_at, note, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'scheduled')",
            (schedule_id, chat_id, 1, title, "2026-08-30 20:30:00", note),
        )
        conn.commit()
    finally:
        conn.close()
    return schedule_id


def test_fire_publishes_schedule_fired_instead_of_action_decision(schedule_service):
    schedule_id = _insert_schedule(chat_id=1001, title="赶火车回学校", note="带身份证")

    fake_nc = AsyncMock()
    fake_nc.publish = AsyncMock()
    fake_nc.flush = AsyncMock()
    fake_nc.close = AsyncMock()

    with patch("services.companion.schedule_service.nats.connect", AsyncMock(return_value=fake_nc)):
        schedule_service._fire(schedule_id, "赶火车回学校", "带身份证", 1001)

    fake_nc.publish.assert_awaited_once()
    published_subject, published_bytes = fake_nc.publish.await_args.args
    assert published_subject == SUBJECT_SCHEDULE_FIRED == "agent.schedule.fired"

    envelope = json.loads(published_bytes.decode())
    assert envelope["subject"] == SUBJECT_SCHEDULE_FIRED
    assert envelope["payload"] == {"chat_id": 1001, "title": "赶火车回学校", "note": "带身份证"}


def test_fire_marks_schedule_done_after_publishing(schedule_service):
    schedule_id = _insert_schedule(chat_id=1001)

    fake_nc = AsyncMock()
    with patch("services.companion.schedule_service.nats.connect", AsyncMock(return_value=fake_nc)):
        schedule_service._fire(schedule_id, "赶火车回学校", "", 1001)

    conn = database.get_connection()
    try:
        row = conn.execute("SELECT status FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "fired"


def test_fire_skips_already_claimed_schedule(schedule_service):
    schedule_id = _insert_schedule(chat_id=1001)
    # Simulate another worker having already claimed it.
    conn = database.get_connection()
    try:
        conn.execute("UPDATE schedules SET status='firing' WHERE schedule_id=?", (schedule_id,))
        conn.commit()
    finally:
        conn.close()

    with patch("services.companion.schedule_service.nats.connect", AsyncMock()) as mock_connect:
        schedule_service._fire(schedule_id, "赶火车回学校", "", 1001)

    mock_connect.assert_not_called()
