"""路由核心。

on_message 是整条链路的入口：去重 → 触发判断 → 限流 → 调 handler → 回发。
只依赖 Adapter / Handler / SessionStore 抽象，与具体传输无关。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable

from .adapter import Adapter
from .handler import Handler
from .message import Message, BOT_SENDER_ID
from .session import Session, SessionStore

DEDUP_MAX = 5000          # 去重表最多记住多少个 msg_id
MIN_INTERVAL_SEC = 2.0    # 每个会话两次回复之间的最小间隔


def should_respond(msg: Message) -> bool:
    """触发策略：群里只在被 @ 时回（防刷屏），单聊全回。

    独立成函数，方便以后改成关键词 / 全回 / AI 判断。
    """
    if msg.msg_type != "text":
        return False
    if msg.is_group:
        return msg.is_at_bot
    return True


class Router:
    def __init__(self, adapter: Adapter, handler: Handler, sessions: SessionStore,
                 *, min_interval_sec: float = MIN_INTERVAL_SEC,
                 on_escalate: Callable[[Session], None] | None = None) -> None:
        self.adapter = adapter
        self.handler = handler
        self.sessions = sessions
        self.min_interval_sec = min_interval_sec
        self.on_escalate = on_escalate  # agent 判定需人工时回调（通知工作台等）
        self._seen: "OrderedDict[str, None]" = OrderedDict()  # 去重（有序，便于淘汰最旧）
        self._last_reply_at: dict[str, float] = {}            # chat_id -> 上次回复时间

    # --- 去重 ---
    def _is_duplicate(self, msg_id: str) -> bool:
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = None
        if len(self._seen) > DEDUP_MAX:
            self._seen.popitem(last=False)  # 淘汰最旧
        return False

    # --- 限流 ---
    def _rate_limited(self, chat_id: str, now: float) -> bool:
        last = self._last_reply_at.get(chat_id)
        return last is not None and (now - last) < self.min_interval_sec

    def on_message(self, msg: Message) -> None:
        # 1) 去重（重复推送直接丢）；msg_id 缺失时用 chat/sender/时间戳/内容合成兜底键，避免去重失效
        dedup_key = msg.msg_id or f"{msg.chat_id}|{msg.sender_id}|{msg.timestamp}|{msg.content}"
        if self._is_duplicate(dedup_key):
            return

        # 记入会话上下文（无论是否回复，都保留历史）
        session = self.sessions.get(msg.chat_id)
        session.add(msg)

        # 人工已接管：agent 静默，只记录消息、不自动回复
        if session.human_controlled:
            return

        # 2) 触发判断
        if not should_respond(msg):
            return

        # 3) 限流
        now = time.monotonic()
        if self._rate_limited(msg.chat_id, now):
            return

        # 4) 生成回复（handler 可能在 session 上标记 needs_human）
        reply = self.handler.reply(msg, session)
        if not reply:
            return

        # 5) 回发（先发成功，再更新限流时间戳 & 记入会话上下文）
        self.adapter.send(msg.chat_id, reply)
        self._last_reply_at[msg.chat_id] = now
        session.add(Message(
            chat_id=msg.chat_id,
            chat_type=msg.chat_type,
            msg_id=f"bot-reply-to-{msg.msg_id}",
            sender_id=BOT_SENDER_ID,
            sender_name="Bot",
            content=reply,
        ))

        # 6) 若本轮 agent 判定需人工，触发升级回调（通知工作台等）
        if session.needs_human and self.on_escalate is not None:
            try:
                self.on_escalate(session)
            except Exception:  # noqa: BLE001 - 回调失败不影响主链路
                pass
