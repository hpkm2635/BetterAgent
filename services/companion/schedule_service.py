"""日程提醒服务 —— 基于 APScheduler 的 CRUD 与到期触发。

职责：
  1. 提供日程的新增 / 查询 / 删除（持久化到 SQLite schedules 表）
  2. 每个日程用 APScheduler 注册一个一次性定时任务，到期时
     POST 到技术总监的 8097 内部端点（不直接操作 NATS）
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from services.companion.database import get_connection

logger = logging.getLogger("companion_schedule")

# 技术总监在 Go Core 侧监听的内部端口（约定值）
_TRIGGER_URL = "http://127.0.0.1:8097/internal/trigger_reminder"


class ScheduleService:
    """日程管理服务，内部持有一个 APScheduler 后台调度器。"""

    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        # 启动时把库里所有未触发的日程重新挂载到调度器（服务重启后仍能触发）
        self._restore_schedules()

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("ScheduleService scheduler started")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("ScheduleService scheduler stopped")

    def _restore_schedules(self) -> None:
        """把数据库中 status='scheduled' 的日程重新注册到调度器。"""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT schedule_id, title, remind_at, note, chat_id FROM schedules WHERE status='scheduled'"
            )
            rows = cur.fetchall()
            for row in rows:
                self._register_job(
                    row["schedule_id"], row["title"], row["remind_at"],
                    row["note"] or "", row["chat_id"],
                )
        except Exception as e:
            logger.warning(f"Failed to restore schedules: {e}")
        finally:
            conn.close()

    def _register_job(self, schedule_id: str, title: str, remind_at: str,
                      note: str, chat_id: int) -> None:
        """根据 ISO 时间注册一次性触发任务。"""
        try:
            run_time = datetime.fromisoformat(remind_at)
        except ValueError:
            logger.warning(f"Invalid remind_at '{remind_at}', skip scheduling")
            return
        # 已过期的时间不再调度
        if run_time.timestamp() <= datetime.now().timestamp():
            return
        self.scheduler.add_job(
            self._fire,
            trigger=DateTrigger(run_date=run_time),
            args=[schedule_id, title, note, chat_id],
            id=schedule_id,
            replace_existing=True,
        )

    def _fire(self, schedule_id: str, title: str, note: str, chat_id: int) -> None:
        """到期回调：POST 到技术总监的内部端点，并标记该日程已完成。"""
        payload = {"chat_id": chat_id, "title": title, "note": note}
        try:
            resp = requests.post(_TRIGGER_URL, json=payload, timeout=5)
            logger.info(
                f"Triggered reminder '{title}' for chat_id={chat_id} "
                f"-> {resp.status_code}"
            )
        except Exception as e:
            logger.warning(f"Failed to trigger reminder '{title}': {e}")
        finally:
            self._mark_done(schedule_id)

    def _mark_done(self, schedule_id: str) -> None:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE schedules SET status='fired' WHERE schedule_id=?",
                (schedule_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def add(self, chat_id: int, user_id: int, title: str,
            remind_at: str, note: str = "") -> Dict[str, Any]:
        """新增日程，返回 schedule_id。"""
        schedule_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO schedules (schedule_id, chat_id, user_id, title, remind_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (schedule_id, chat_id, user_id, title, remind_at, note),
            )
            conn.commit()
        finally:
            conn.close()

        self._register_job(schedule_id, title, remind_at, note, chat_id)
        return {"schedule_id": schedule_id, "status": "scheduled"}

    def list(self, chat_id: int) -> List[Dict[str, Any]]:
        """查询某 chat_id 下所有日程。"""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT schedule_id, title, remind_at, note, status FROM schedules "
                "WHERE chat_id=? ORDER BY remind_at",
                (chat_id,),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete(self, schedule_id: str) -> bool:
        """删除日程，返回是否成功。"""
        # 先从调度器移除
        job = self.scheduler.get_job(schedule_id)
        if job:
            job.remove()

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM schedules WHERE schedule_id=?", (schedule_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
