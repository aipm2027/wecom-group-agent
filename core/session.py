"""群会话状态。

按 chat_id 维护每个群/会话的最近上下文和简单状态。
本阶段内存实现；SessionStore 留了扩展点，以后可换 SQLite 持久化。
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque

from .message import Message

MAX_CONTEXT = 20  # 每个会话保留的最近消息条数


@dataclass
class Session:
    chat_id: str
    history: Deque[Message] = field(default_factory=lambda: deque(maxlen=MAX_CONTEXT))
    state: dict = field(default_factory=dict)  # 预留：阶段标记 / 变量等
    human_controlled: bool = False  # True 时人工已接管，agent 静默不自动回
    needs_human: bool = False       # agent 判定需转人工，待人工认领
    escalation_reason: str = ""      # 转人工原因（agent 或规则填）
    # 写穿透钩子：持久化存储（如 SQLite）挂上后，消息/状态变更自动落库；内存版留 None
    on_message: Callable[["Session", Message], None] | None = field(
        default=None, repr=False, compare=False)
    on_flags: Callable[["Session"], None] | None = field(
        default=None, repr=False, compare=False)

    def add(self, msg: Message) -> None:
        self.history.append(msg)
        if self.on_message:
            self.on_message(self, msg)

    def mark_needs_human(self, reason: str = "") -> None:
        """agent 判定需人工介入。"""
        self.needs_human = True
        if reason and not self.escalation_reason:
            self.escalation_reason = reason
        if self.on_flags:
            self.on_flags(self)

    def take_over(self) -> None:
        """人工接管：agent 静默，清掉待认领标记。"""
        self.human_controlled = True
        self.needs_human = False
        if self.on_flags:
            self.on_flags(self)

    def release(self) -> None:
        """人工交回：agent 恢复自动回复。"""
        self.human_controlled = False
        self.needs_human = False
        self.escalation_reason = ""
        if self.on_flags:
            self.on_flags(self)


class SessionStore:
    """内存版会话存储。扩展点：换成 SQLite/Redis 只需替换这个类（见 core/session_sqlite.py）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()  # 多线程（如 api_server 的 ThreadingHTTPServer）下保护 get/all

    def get(self, chat_id: str) -> Session:
        with self._lock:
            s = self._sessions.get(chat_id)
            if s is None:
                s = Session(chat_id=chat_id)
                self._sessions[chat_id] = s
            return s

    def all(self, limit: int | None = None) -> list[Session]:
        """返回会话列表（供管理后台/API 列会话用）；limit 限制条数（分页钩子，默认全部）。"""
        with self._lock:
            vals = list(self._sessions.values())
        return vals[:limit] if limit else vals
