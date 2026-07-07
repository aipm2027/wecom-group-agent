"""规范化消息模型。

屏蔽不同传输（mock / ntwork hook / 未来的其他适配器）的字段差异，
核心与 handler 只依赖这个统一结构。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


BOT_SENDER_ID = "__bot__"


@dataclass
class Message:
    chat_id: str                      # 群/会话唯一 id
    chat_type: str                    # "group" | "single"
    msg_id: str                       # 用于去重
    sender_id: str                    # 发送者 id
    sender_name: str                  # 发送者昵称
    content: str                      # 文本内容
    msg_type: str = "text"            # 先只处理 text
    is_at_bot: bool = False           # 群里是否 @ 了机器人
    timestamp: int = 0                # 毫秒
    raw: dict[str, Any] = field(default_factory=dict)  # 原始 payload，留存

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"
