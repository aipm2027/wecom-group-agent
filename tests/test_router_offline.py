"""Router 核心行为离线测试：限流、触发策略、去重（含空 msg_id）、接管静默。

这些是之前的测试盲区：所有旧测试都用 min_interval_sec=0，从未验证限流；
should_respond 的 False 分支、接管静默、空 msg_id 去重也无覆盖。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.handler import Handler
from core.message import Message
from core.router import Router, should_respond
from core.session import SessionStore


class SpyAdapter:
    def __init__(self) -> None:
        self.sent: list = []

    def start(self, on_message) -> None:  # 演示用，不接收
        pass

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class FixedHandler(Handler):
    def reply(self, msg: Message, session) -> str:
        return "回复"


def _msg(chat_id="c1", content="你好", msg_id="m1", chat_type="single",
         is_at_bot=False, msg_type="text", sender_id="u1", timestamp=0) -> Message:
    return Message(chat_id=chat_id, chat_type=chat_type, msg_id=msg_id, sender_id=sender_id,
                   sender_name=sender_id, content=content, msg_type=msg_type,
                   is_at_bot=is_at_bot, timestamp=timestamp)


def test_rate_limit_blocks_second() -> None:
    """同会话 2 秒内第二条应被限流，不发送。"""
    adapter = SpyAdapter()
    r = Router(adapter, FixedHandler(), SessionStore(), min_interval_sec=2.0)
    r.on_message(_msg(msg_id="m1"))
    r.on_message(_msg(msg_id="m2"))
    assert len(adapter.sent) == 1, f"限流应只发 1 条，实际 {len(adapter.sent)}"


def test_rate_limit_allows_when_zero() -> None:
    """min_interval_sec=0 时不限流，两条都回。"""
    adapter = SpyAdapter()
    r = Router(adapter, FixedHandler(), SessionStore(), min_interval_sec=0.0)
    r.on_message(_msg(msg_id="m1"))
    r.on_message(_msg(msg_id="m2"))
    assert len(adapter.sent) == 2


def test_should_respond_non_text() -> None:
    """非 text 消息不回。"""
    assert should_respond(_msg(msg_type="image")) is False
    adapter = SpyAdapter()
    r = Router(adapter, FixedHandler(), SessionStore(), min_interval_sec=0.0)
    r.on_message(_msg(msg_type="image", msg_id="i1"))
    assert adapter.sent == []


def test_should_respond_group_without_at() -> None:
    """群聊未 @bot 不回；@bot 才回。"""
    assert should_respond(_msg(chat_type="group", is_at_bot=False)) is False
    assert should_respond(_msg(chat_type="group", is_at_bot=True)) is True
    adapter = SpyAdapter()
    r = Router(adapter, FixedHandler(), SessionStore(), min_interval_sec=0.0)
    r.on_message(_msg(chat_type="group", is_at_bot=False, msg_id="g1"))
    assert adapter.sent == []


def test_dedup_same_msgid() -> None:
    """相同 msg_id 第二条去重。"""
    adapter = SpyAdapter()
    r = Router(adapter, FixedHandler(), SessionStore(), min_interval_sec=0.0)
    r.on_message(_msg(msg_id="dup"))
    r.on_message(_msg(msg_id="dup"))
    assert len(adapter.sent) == 1


def test_dedup_empty_msgid() -> None:
    """空 msg_id 且内容/发送者/时间戳相同的重复推送，靠合成键也能去重。"""
    adapter = SpyAdapter()
    r = Router(adapter, FixedHandler(), SessionStore(), min_interval_sec=0.0)
    r.on_message(_msg(msg_id="", content="X", timestamp=100))
    r.on_message(_msg(msg_id="", content="X", timestamp=100))
    assert len(adapter.sent) == 1, "空 msg_id 的重复消息应被合成键去重"


def test_takeover_silent() -> None:
    """人工接管态 agent 静默不回，但消息仍记入历史。"""
    adapter = SpyAdapter()
    store = SessionStore()
    store.get("c1").take_over()
    r = Router(adapter, FixedHandler(), store, min_interval_sec=0.0)
    r.on_message(_msg(chat_id="c1", msg_id="m1"))
    assert adapter.sent == [], "接管态应静默"
    assert len(store.get("c1").history) == 1, "消息仍应记入历史"


def main() -> None:
    for fn in (
        test_rate_limit_blocks_second,
        test_rate_limit_allows_when_zero,
        test_should_respond_non_text,
        test_should_respond_group_without_at,
        test_dedup_same_msgid,
        test_dedup_empty_msgid,
        test_takeover_silent,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\nRouter 离线测试全部通过！")


if __name__ == "__main__":
    main()
