"""回复 handler。

Handler 决定「针对一条消息回什么」。本阶段用占位实现 EchoHandler
验证链路；以后接真实 agent（规则引擎 / LLM）时，新增一个 Handler
实现替换即可，Router 不动。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .message import Message
from .session import Session


class Handler(ABC):
    @abstractmethod
    def reply(self, msg: Message, session: Session) -> str | None:
        """返回要回复的文本；返回 None 表示不回。"""
        raise NotImplementedError


class EchoHandler(Handler):
    """占位实现：把用户内容回读一遍，用于验证全链路。"""

    def reply(self, msg: Message, session: Session) -> str | None:
        content = msg.content.strip()
        if not content:
            return None
        return f"收到：{content}"
