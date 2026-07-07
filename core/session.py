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
    human_controlled: bool = False  # True 时人工已接管，agent 静默不自动回
    needs_human: bool = False       # agent 判定需转人工，待人工认领
    escalation_reason: str = ""      # 转人工原因（agent 或规则填）

    def add(self, msg: Message) -> None:
        self.history.append(msg)

    def take_over(self) -> None:
        """人工接管：agent 静默，清掉待认领标记。"""
        self.human_controlled = True
        self.needs_human = False

    def release(self) -> None:
        """人工交回：agent 恢复自动回复。"""
        self.human_controlled = False
        self.needs_human = False
        self.escalation_reason = ""


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

    def all(self) -> list[Session]:
        """返回所有会话（供管理后台/API 列会话用）。"""
        return list(self._sessions.values())
