"""SQLite 数据库初始化与连接管理。

任务3 陪伴工具服务 —— 所有陪伴统计数据与日程提醒都存储在本地的
companion.db（SQLite）中。本模块负责：
  1. 启动时自动建表（chat_stats / mood_history / topic_log / schedules）
  2. 提供统一的数据库连接（每次操作独立连接，避免多线程冲突）

companion.db 文件已被 .gitignore 中的 *.db 规则覆盖，不会被提交。
"""
import os
import sqlite3

# 数据库文件固定放在 services/companion/ 目录下
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "companion.db")


def get_db_path() -> str:
    """返回 SQLite 数据库文件的绝对路径。"""
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    """新建一个数据库连接（调用方负责关闭）。

    使用 row_factory 让查询结果可以通过列名访问，方便转成 dict。
    """
    conn = sqlite3.connect(_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """服务启动时调用：创建所有需要的表（IF NOT EXISTS，幂等）。"""
    conn = get_connection()
    try:
        cur = conn.cursor()

        # 开启 WAL 模式：提升并发读写性能，多 Worker 下减少锁冲突
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.fetchone()

        # 每日对话统计（由技术总监通过 /api/companion/stat 写入）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                msg_count INTEGER DEFAULT 0,
                proactive_count INTEGER DEFAULT 0
            )
        """)

        # 情绪历史（技术总监写入）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mood_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ts REAL NOT NULL,
                mood_score REAL NOT NULL,
                emotion_tag TEXT DEFAULT ''
            )
        """)

        # 话题日志（技术总监写入，张劭哲只读查询）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS topic_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ts REAL NOT NULL,
                topic TEXT NOT NULL,
                source TEXT DEFAULT 'user'
            )
        """)

        # 日程提醒（张劭哲的 CRUD 需要持久化存储）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                schedule_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                note TEXT DEFAULT '',
                status TEXT DEFAULT 'scheduled',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # 用户记忆事实库 (User Profile & Long-term Memories)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile_facts (
                fact_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                category TEXT DEFAULT 'general',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Seed initial memories if table is empty
        cur.execute("SELECT COUNT(*) as count FROM user_profile_facts")
        if cur.fetchone()["count"] == 0:
            seed_facts = [
                ("fact_101", 1001, 1, "identity", "用户称呼", "学弟"),
                ("fact_102", 1001, 1, "identity", "校园身份", "计算机专业应届毕业生"),
                ("fact_103", 1001, 1, "preference", "喜好游戏", "杀戮尖塔2、二次元手游"),
                ("fact_104", 1001, 1, "preference", "常用工具", "AIRI 桌面虚拟主播、BetterAgent"),
                ("fact_105", 1001, 1, "campus", "寝室编号", "海韵园区 4号楼 502"),
            ]
            cur.executemany(
                "INSERT INTO user_profile_facts (fact_id, chat_id, user_id, category, key, value) VALUES (?, ?, ?, ?, ?, ?)",
                seed_facts,
            )

        conn.commit()
    finally:
        conn.close()
