"""日程提醒服务 —— 基于 APScheduler 的 CRUD 与到期触发。

职责：
  1. 提供日程的新增 / 查询 / 删除（持久化到 SQLite schedules 表）
  2. 每个日程用 APScheduler 注册一个一次性定时任务，到期时把
     ActionDecision 发布到 NATS 的 agent.action.{channel}.{chat_id}，
     由 Go Core 的 WebGateway / GotdAdapter 推送到 Web 页面或 Telegram。
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import nats
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from services.companion.database import get_connection
from shared.subjects import action_decision_subject

logger = logging.getLogger("companion_schedule")

# WebGateway 会话命名空间偏移（镜像 core/internal/idspace/idspace.go 的
# WebNamespaceOffset）。chat_id >= 该值属于 Web 会话，否则为 Telegram 会话。
_WEB_NAMESPACE_OFFSET = 9_000_000_000_000_000


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
            # 崩溃残留的 firing 状态重置回 scheduled，避免漏触发
            cur.execute("UPDATE schedules SET status='scheduled' WHERE status='firing'")
            conn.commit()
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
            # 兼容 ISO 时间字符串中的 'Z'（UTC）时区标识
            normalized = remind_at.strip()
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            run_time = datetime.fromisoformat(normalized)
            # 无时区的 naive 时间统一按 Asia/Shanghai(+08:00) 解释，与 APScheduler 的
            # timezone 一致；否则过期判断和触发时刻会因机器本地时区不同而偏差数小时。
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=timezone(timedelta(hours=8)))
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
        """到期回调：先原子抢占，再推送提醒到目标会话并标记完成。

        多 Worker 模式下，同一 schedule_id 只会有一个实例抢占成功，
        从而避免重复触发提醒。提醒不再 POST 到不存在的 8097 内部端点，
        而是直接发布到 NATS 的 agent.action.{channel}.{chat_id} 主题。
        """
        if not self._try_claim(schedule_id):
            logger.info(f"Reminder '{title}' already claimed by another worker, skip")
            return

        chat_id = int(chat_id)
        channel = "web" if chat_id >= _WEB_NAMESPACE_OFFSET else "telegram"
        text = f"⏰ 主人，日程提醒来啦：{title}"
        if note:
            text += f"（{note}）"

        payload = {
            "chat_id": chat_id,
            "generation_id": 0,  # 0 跳过 WebGateway 的陈旧 generation 过滤
            "source_channel": channel,
            "action_type": "send_message",
            "text_content": text,
            "is_final": True,
        }

        try:
            asyncio.run(self._publish_reminder_async(channel, chat_id, payload))
            logger.info(f"Triggered reminder '{title}' for chat_id={chat_id} (channel={channel})")
        except Exception as e:
            logger.warning(f"Failed to trigger reminder '{title}': {e}")
        finally:
            self._mark_done(schedule_id)

    async def _publish_reminder_async(self, channel: str, chat_id: int, payload: Dict[str, Any]) -> None:
        """把提醒 ActionDecision 发布到 NATS 的 agent.action.{channel}.{chat_id}。

        在 `_fire`（APScheduler 线程）里通过 asyncio.run 调用；提醒触发频率极低，
        每次新建一条 NATS 连接即可，无需常驻连接。
        """
        nats_url = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
        nats_user = os.getenv("NATS_USER")
        nats_password = os.getenv("NATS_PASSWORD")
        if not nats_user or not nats_password:
            raise RuntimeError("NATS_USER / NATS_PASSWORD not set; cannot deliver reminder")

        nc = await nats.connect(nats_url, user=nats_user, password=nats_password)
        try:
            subject = action_decision_subject(channel, chat_id)
            envelope = {
                "id": str(uuid.uuid4()),
                "subject": subject,
                "source": "companion_service",
                "payload": payload,
            }
            await nc.publish(subject, json.dumps(envelope).encode())
            await nc.flush()
        finally:
            await nc.close()

    def _try_claim(self, schedule_id: str) -> bool:
        """原子抢占：仅当状态仍为 scheduled 时改成 firing。

        返回 True 表示本实例抢到触发权；False 表示已被其他 Worker 抢占。
        """
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE schedules SET status='firing' "
                "WHERE schedule_id=? AND status='scheduled'",
                (schedule_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

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

    def list(self, chat_id: Optional[int] = None, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """查询未到期的日程（已触发 / 已过期的不返回）。

        可按 chat_id 或 user_id 过滤；两者都不给时返回全部未到期日程
        （管理面板"同步用户前端日程"依赖全量模式）。行内同时带出 user_id，
        方便管理端按用户展示。
        """
        conn = get_connection()
        try:
            cur = conn.cursor()
            sql = (
                "SELECT schedule_id, chat_id, user_id, title, remind_at, note, status "
                "FROM schedules"
            )
            where: List[str] = []
            params: List[Any] = []
            if chat_id is not None:
                where.append("chat_id=?")
                params.append(int(chat_id))
            elif user_id is not None:
                where.append("user_id=?")
                params.append(int(user_id))
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY remind_at"
            rows = cur.execute(sql, tuple(params)).fetchall()
            now = datetime.now(timezone(timedelta(hours=8)))
            upcoming: List[Dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                # 只保留“未触发且未到期”的日程
                if item.get("status") != "scheduled":
                    continue
                try:
                    rt = datetime.fromisoformat((item.get("remind_at") or "").strip())
                    if rt.tzinfo is None:
                        rt = rt.replace(tzinfo=timezone(timedelta(hours=8)))
                except (ValueError, TypeError):
                    continue
                if rt > now:
                    upcoming.append(item)
            return upcoming
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
