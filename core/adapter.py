"""适配器抽象基类。

一个适配器负责把某个传输（mock / ntwork hook / ...）的消息收发，
映射到统一的 Message 模型上。核心（Router）只依赖这个抽象，
换传输不动核心。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from .message import Message

OnMessage = Callable[[Message], None]


class Adapter(ABC):
    @abstractmethod
    def start(self, on_message: OnMessage) -> None:
        """开始接收消息；每收到一条就调用 on_message(Message)。

        实现方可阻塞（如 mock 的 stdin 循环、ntwork 的事件循环）。
        """
        raise NotImplementedError

    @abstractmethod
    def send(self, chat_id: str, text: str) -> None:
        """往指定会话发一条文本消息。"""
        raise NotImplementedError
