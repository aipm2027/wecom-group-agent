"""SQLite 持久化会话存储。

与内存版 SessionStore 同接口（get(chat_id) / all()），但会话状态与消息落库，
进程重启后可恢复。通过 Session 的 on_message/on_flags 写穿透钩子做透明持久化，
Router 无需改动——把 `SessionStore()` 换成 `SqliteSessionStore(path)` 即可。

纯标准库（sqlite3）。data/ 目录已在 .gitignore。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

from .message import Message
from .session import MAX_CONTEXT, Session

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    chat_id TEXT PRIMARY KEY,
    human_controlled INTEGER NOT NULL DEFAULT 0,
    needs_human INTEGER NOT NULL DEFAULT 0,
    escalation_reason TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'single',
    sender_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    msg_type TEXT NOT NULL DEFAULT 'text',
    is_at_bot INTEGER NOT NULL DEFAULT 0,
    timestamp INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    UNIQUE(chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
"""


class SqliteSessionStore:
    """SQLite 版会话存储，接口与内存版 SessionStore 一致。"""

    def __init__(self, path: str = "data/sessions.db") -> None:
        self._path = path if os.path.isabs(path) else os.path.join(_ROOT, path)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._lock = threading.Lock()  # http.server 多线程下串行化写
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._live: dict[str, Session] = {}  # 活跃会话对象缓存（与内存版语义一致）

    # --- 对外接口（与 SessionStore 相同）---
    def get(self, chat_id: str) -> Session:
        if chat_id in self._live:
            return self._live[chat_id]
        session = self._hydrate(chat_id)
        self._live[chat_id] = session
        return session

    def all(self) -> list[Session]:
        with self._lock:
            rows = self._conn.execute("SELECT chat_id FROM sessions ORDER BY updated_at DESC").fetchall()
        for (chat_id,) in rows:
            self.get(chat_id)
        return list(self._live.values())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- 内部：从库加载会话 + 挂写穿透钩子 ---
    def _hydrate(self, chat_id: str) -> Session:
        session = Session(chat_id=chat_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT human_controlled, needs_human, escalation_reason FROM sessions WHERE chat_id=?",
                (chat_id,)).fetchone()
            msg_rows = self._conn.execute(
                "SELECT chat_type, msg_id, sender_id, sender_name, content, msg_type, is_at_bot, timestamp "
                "FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                (chat_id, MAX_CONTEXT)).fetchall()
        if row:
            session.human_controlled = bool(row[0])
            session.needs_human = bool(row[1])
            session.escalation_reason = row[2] or ""
        # 按时间正序恢复最近 N 条历史（直接入 deque，不触发钩子）
        for r in reversed(msg_rows):
            session.history.append(Message(
                chat_id=chat_id, chat_type=r[0], msg_id=r[1], sender_id=r[2],
                sender_name=r[3], content=r[4], msg_type=r[5],
                is_at_bot=bool(r[6]), timestamp=r[7]))
        # 加载完再挂钩子，避免 hydrate 期间回写
        session.on_message = self._persist_message
        session.on_flags = self._persist_flags
        return session

    # --- 写穿透 ---
    def _persist_message(self, session: Session, msg: Message) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(chat_id, updated_at) VALUES(?, ?)",
                (session.chat_id, now))
            self._conn.execute(
                "INSERT OR IGNORE INTO messages"
                "(chat_id, msg_id, chat_type, sender_id, sender_name, content, msg_type, is_at_bot, timestamp, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (msg.chat_id, msg.msg_id, msg.chat_type, msg.sender_id, msg.sender_name,
                 msg.content, msg.msg_type, int(msg.is_at_bot), msg.timestamp, now))
            self._conn.execute("UPDATE sessions SET updated_at=? WHERE chat_id=?", (now, session.chat_id))
            self._conn.commit()

    def _persist_flags(self, session: Session) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(chat_id, human_controlled, needs_human, escalation_reason, updated_at) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET "
                "human_controlled=excluded.human_controlled, needs_human=excluded.needs_human, "
                "escalation_reason=excluded.escalation_reason, updated_at=excluded.updated_at",
                (session.chat_id, int(session.human_controlled), int(session.needs_human),
                 session.escalation_reason, now))
            self._conn.commit()
