"""SQL Agent —— 自然语言查询陪伴数据（NL2SQL）。

用规则模板把自然语言问题转成 SQL，查询范围严格限定在
chat_stats / mood_history / topic_log 三张表，只允许 SELECT。

不使用外部 LLM，保证离线可测、结果稳定，符合契约测试要求。
"""
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services.companion.database import get_connection


class SQLAgent:
    """把自然语言问题翻译成受限的 SQL 并执行。"""

    # 允许查询的表白名单
    ALLOWED_TABLES = {"chat_stats", "mood_history", "topic_log"}

    def query(self, chat_id: int, natural_language_query: str) -> Dict[str, Any]:
        """执行一次 NL2SQL 查询。

        返回结构：
          { "answer": str, "sql_executed": str, "raw_result": List[dict] }
        若无法识别或非法，抛 ValueError。
        """
        q = (natural_language_query or "").strip().lower()
        if not q:
            raise ValueError("empty query")

        sql, answer_template = self._translate(chat_id, q)
        if sql is None:
            raise ValueError("unsupported query")

        rows = self._execute_select(sql, chat_id)
        answer = self._format_answer(answer_template, rows, q)
        return {
            "answer": answer,
            "sql_executed": sql,
            "raw_result": rows,
        }

    def _translate(self, chat_id: int, q: str) -> Tuple[Optional[str], str]:
        """规则匹配：返回 (SQL, 回答模板)。"""
        cid = int(chat_id)

        # 1. 本周聊了多少次 / 这周聊了多少次
        if ("这周" in q or "本周" in q) and ("聊" in q or "消息" in q or "次" in q):
            monday = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
            sql = (
                "SELECT COALESCE(SUM(msg_count), 0) AS total FROM chat_stats "
                f"WHERE chat_id={cid} AND date >= '{monday}'"
            )
            return sql, "sum_week"

        # 2. 今天聊了多少次
        if ("今天" in q or "今日" in q) and ("聊" in q or "消息" in q or "次" in q):
            today = datetime.now().strftime("%Y-%m-%d")
            sql = (
                "SELECT COALESCE(SUM(msg_count), 0) AS total FROM chat_stats "
                f"WHERE chat_id={cid} AND date = '{today}'"
            )
            return sql, "sum_today"

        # 3. 最近一次/当前情绪
        if "情绪" in q or "心情" in q or "mood" in q:
            sql = (
                "SELECT mood_score, emotion_tag, ts FROM mood_history "
                f"WHERE chat_id={cid} ORDER BY ts DESC LIMIT 1"
            )
            return sql, "mood"

        # 4. 最近聊了哪些话题
        if "话题" in q or "topic" in q or "聊了什么" in q:
            sql = (
                "SELECT topic, source, ts FROM topic_log "
                f"WHERE chat_id={cid} ORDER BY ts DESC LIMIT 5"
            )
            return sql, "topics"

        # 5. 主动搭话次数
        if "主动" in q and ("搭话" in q or "聊" in q or "次" in q):
            sql = (
                "SELECT COALESCE(SUM(proactive_count), 0) AS total FROM chat_stats "
                f"WHERE chat_id={cid}"
            )
            return sql, "proactive"

        # 6. 累计聊了多少次（默认兜底：总数）
        if "多少" in q or "几次" in q or "几次" in q or "总和" in q or "一共" in q or "总共" in q:
            sql = (
                "SELECT COALESCE(SUM(msg_count), 0) AS total FROM chat_stats "
                f"WHERE chat_id={cid}"
            )
            return sql, "sum_total"

        return None, ""

    def _execute_select(self, sql: str, chat_id: int) -> List[Dict[str, Any]]:
        """安全执行：只允许 SELECT（SQL 由内部规则生成，已限定白名单表）。"""
        stripped = sql.strip()
        if not re.match(r"^SELECT\b", stripped, re.IGNORECASE):
            raise ValueError("Only SELECT is allowed")

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _format_answer(self, template: str, rows: List[Dict[str, Any]],
                       question: str) -> str:
        """把查询结果转成猫娘口吻的回答。"""
        if template == "sum_week":
            total = self._first(rows, "total")
            return f"这周你一共和我聊了 {total} 次喵～"
        if template == "sum_today":
            total = self._first(rows, "total")
            return f"今天你已经和我聊了 {total} 次喵～"
        if template == "sum_total":
            total = self._first(rows, "total")
            return f"你一共和我聊了 {total} 次喵～"
        if template == "proactive":
            total = self._first(rows, "total")
            return f"我一共主动找过你 {total} 次喵～"
        if template == "mood":
            if not rows:
                return "最近还没有情绪记录喵～"
            r = rows[0]
            tag = r.get("emotion_tag") or "平静"
            score = r.get("mood_score")
            return f"最近我的情绪标签是 {tag}（评分 {score}）喵～"
        if template == "topics":
            if not rows:
                return "最近还没有话题记录喵～"
            topics = [r.get("topic") for r in rows if r.get("topic")]
            return "最近我们聊的话题有：" + "、".join(topics) + " 喵～"
        return "收到，但这个问题我还没学会怎么查喵～"

    @staticmethod
    def _first(rows: List[Dict[str, Any]], key: str, default: int = 0) -> int:
        if not rows:
            return default
        val = rows[0].get(key, default)
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return default
