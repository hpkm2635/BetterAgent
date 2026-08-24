"""规则推荐逻辑 —— 纯规则，不需要 LLM。

推荐策略：
  1. 查今日 topic_log，若缺少 "study" 话题 → 推荐学习类提示
  2. 查未来 24 小时内的 schedule 项 → 生成"快到期提醒"文案
  3. 无数据时返回空列表，不崩溃
"""
from datetime import datetime, timedelta, timezone
from typing import List

from services.companion.database import get_connection


def get_recommendations(chat_id: int) -> List[str]:
    """返回针对某 chat_id 的推荐文案列表。"""
    recommendations: List[str] = []

    today = datetime.now().strftime("%Y-%m-%d")
    # 与 schedule_service.py 的 list()/_register_job() 保持一致：naive 时间
    # 统一按 Asia/Shanghai(+08:00) 解释，否则跨时区部署时判断会偏差数小时。
    cst = timezone(timedelta(hours=8))
    now_cst = datetime.now(cst)
    now_ts = now_cst.timestamp()
    future_ts = now_ts + 24 * 3600

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 策略 1：今日是否聊过学习话题
        cur.execute(
            "SELECT COUNT(*) AS c FROM topic_log WHERE chat_id=? AND topic='study' AND ts>=?",
            (chat_id, now_ts - 24 * 3600),
        )
        study_count = cur.fetchone()["c"]
        if study_count == 0:
            recommendations.append("今天还没聊过学习话题哦，要一起复习吗？")

        # 策略 2：未来 24 小时内是否有日程快到期
        cur.execute(
            "SELECT title, remind_at FROM schedules "
            "WHERE chat_id=? AND status='scheduled' ORDER BY remind_at",
            (chat_id,),
        )
        for row in cur.fetchall():
            try:
                remind_dt = datetime.fromisoformat(row["remind_at"])
                if remind_dt.tzinfo is None:
                    remind_dt = remind_dt.replace(tzinfo=cst)
                remind_ts = remind_dt.timestamp()
                days = (remind_dt - now_cst).days
            except (ValueError, TypeError):
                continue
            if now_ts <= remind_ts <= future_ts:
                day_text = "今天" if days <= 0 else f"还有 {days} 天"
                recommendations.append(
                    f"{row['title']} {day_text}就要到了，要不要定一个复习提醒？"
                )
                break  # 只取最近一个快到期的日程

        return recommendations
    finally:
        conn.close()
