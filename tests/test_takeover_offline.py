"""人工接管 + 结构化转人工信号 离线测试。不依赖网络。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_handler import ESCALATE_TAG, LLMHandler
from core.message import Message
from core.router import Router
from core.session import SessionStore


class SpyAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def start(self, on_message) -> None:  # noqa: D401 - 测试不接收
        pass

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _msg(chat_id: str = "u1", content: str = "你好", mid: str = "m1") -> Message:
    return Message(chat_id=chat_id, chat_type="single", msg_id=mid,
                   sender_id=chat_id, sender_name="客户", content=content)


def test_escalate_tag_stripped_and_flagged() -> None:
    """回复含 [[转人工]] 时：标记被剥离、客户看不到，session.needs_human 置真。"""
    h = LLMHandler(transport=lambda m: f"好的，这边帮你转专属客服跟进哈~\n{ESCALATE_TAG}")
    store = SessionStore()
    s = store.get("u1")
    s.add(_msg())
    out = h.reply(s.history[-1], s)
    assert ESCALATE_TAG not in out, "控制标记必须被剥离"
    assert "转专属客服" in out, "安抚话术应保留"
    assert s.needs_human is True, "应标记需人工"


def test_router_silent_when_human_controlled() -> None:
    """人工已接管：agent 不自动回、也不调用 handler，但消息仍被记录。"""
    adapter = SpyAdapter()
    called = {"n": 0}

    def spy(m):
        called["n"] += 1
        return "自动回复"

    store = SessionStore()
    store.get("u1").take_over()  # 人工接管
    r = Router(adapter, LLMHandler(transport=spy), store, min_interval_sec=0)
    r.on_message(_msg())
    assert adapter.sent == [], "接管后 agent 不应自动回复"
    assert called["n"] == 0, "接管后不应调用 handler"
    assert len(store.get("u1").history) == 1, "消息仍应被记录供人工查看"


def test_router_fires_escalation_callback() -> None:
    """agent 判定需人工时：标记被剥离后发出，on_escalate 回调被触发。"""
    adapter = SpyAdapter()
    fired = {"s": None}
    h = LLMHandler(transport=lambda m: f"帮你转专属客服哈~\n{ESCALATE_TAG}")
    store = SessionStore()
    r = Router(adapter, h, store, min_interval_sec=0,
               on_escalate=lambda s: fired.__setitem__("s", s))
    r.on_message(_msg())
    assert adapter.sent, "应正常发出安抚回复"
    assert ESCALATE_TAG not in adapter.sent[0][1], "发给客户的文本不含控制标记"
    assert fired["s"] is not None and fired["s"].needs_human is True, "升级回调应被触发"


def test_router_normal_reply_regression() -> None:
    """普通回复（无标记）：正常发出，不误置 needs_human。"""
    adapter = SpyAdapter()
    r = Router(adapter, LLMHandler(transport=lambda m: "亲，在的~"), SessionStore(), min_interval_sec=0)
    r.on_message(_msg())
    assert adapter.sent == [("u1", "亲，在的~")]
    assert r.sessions.get("u1").needs_human is False


def test_take_over_and_release() -> None:
    """take_over/release 语义正确。"""
    s = SessionStore().get("u1")
    s.needs_human = True
    s.take_over()
    assert s.human_controlled and not s.needs_human
    s.release()
    assert not s.human_controlled and not s.needs_human


def main() -> None:
    for fn in (
        test_escalate_tag_stripped_and_flagged,
        test_router_silent_when_human_controlled,
        test_router_fires_escalation_callback,
        test_router_normal_reply_regression,
        test_take_over_and_release,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n人工接管测试全部通过！")


if __name__ == "__main__":
    main()
