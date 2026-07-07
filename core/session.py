"""群会话状态。

按 chat_id 维护每个群/会话的最近上下文和简单状态。
本阶段内存实现；SessionStore 留了扩展点，以后可换 SQLite 持久化。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from .message import Message

MAX_CONTEXT = 20  # 每个会话保留的最近消息条数


@dataclass
class Session:
    chat_id: str
    history: Deque[Message] = field(default_factory=lambda: deque(maxlen=MAX_CONTEXT))
    state: dict = field(default_factory=dict)  # 预留：阶段标记 / 变量等

    def add(self, msg: Message) -> None:
        self.history.append(msg)


class SessionStore:
    """内存版会话存储。扩展点：换成 SQLite/Redis 只需替换这个类。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, chat_id: str) -> Session:
        s = self._sessions.get(chat_id)
        if s is None:
            s = Session(chat_id=chat_id)
            self._sessions[chat_id] = s
        return s
