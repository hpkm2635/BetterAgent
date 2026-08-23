"""Regression tests for services/companion/recommendation.py.

Covers bug #5: a schedule row with a timezone-aware remind_at used to crash
get_recommendations() with an uncaught TypeError (naive datetime.now() minus
an aware datetime), and a naive remind_at was compared against the host's
local timezone instead of the fixed Asia/Shanghai(+08:00) the rest of the
companion service uses.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.companion import database, recommendation

CST = timezone(timedelta(hours=8))


@pytest.fixture()
def companion_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "companion_test.db")
    monkeypatch.setattr(database, "_DB_PATH", db_path)
    database.init_db()
    yield db_path


def _insert_schedule(chat_id: int, title: str, remind_at: str) -> None:
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO schedules (schedule_id, chat_id, user_id, title, remind_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'scheduled')",
            (f"sched_{title}", chat_id, chat_id, title, remind_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_recommendations_handles_timezone_aware_remind_at(companion_db):
    """A tz-aware remind_at (e.g. from an ISO8601 string with +08:00) must not
    crash the endpoint with a naive-vs-aware datetime subtraction TypeError.
    """
    chat_id = 9001
    remind_at = (datetime.now(CST) + timedelta(hours=5)).isoformat()
    _insert_schedule(chat_id, "复习高数", remind_at)

    recs = recommendation.get_recommendations(chat_id)

    assert any("复习高数" in r and "今天" in r for r in recs)


def test_recommendations_naive_remind_at_uses_shanghai_offset(companion_db):
    """A naive remind_at must be interpreted as Asia/Shanghai, matching
    schedule_service.py's write/list paths -- not the host's local tz.

    Picks an offset (5h) that only falls inside the function's 24h "upcoming"
    window under the correct +08:00 interpretation: mis-parsing this as a
    different offset would push remind_ts out of that window (dropped
    silently) or crash, either of which fails this assertion.
    """
    chat_id = 9002
    remind_at_cst = datetime.now(CST) + timedelta(hours=5)
    naive_remind_at = remind_at_cst.replace(tzinfo=None).isoformat()
    _insert_schedule(chat_id, "英语四级报名", naive_remind_at)

    recs = recommendation.get_recommendations(chat_id)

    assert any("英语四级报名" in r for r in recs)


def test_recommendations_no_upcoming_schedule_returns_study_prompt_only(companion_db):
    chat_id = 9003
    recs = recommendation.get_recommendations(chat_id)
    assert recs == ["今天还没聊过学习话题哦，要一起复习吗？"]
