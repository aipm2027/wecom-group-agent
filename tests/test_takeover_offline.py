"""人工接管 + 结构化转人工信号 离线测试。不依赖网络。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_handler import (ESCALATE_TAG, REASON_AFTER_SALES, REASON_ASK_HUMAN,
                              REASON_EMOTION, REASON_LLM_JUDGED, LLMHandler,
                              classify_escalation)
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


def test_classify_escalation_labels() -> None:
    """规则层分类（P1-3）：三类标签可区分、咨询类问题不误召。"""
    assert classify_escalation("转人工,我要找真人") == REASON_ASK_HUMAN
    assert classify_escalation("我要退款,东西不想要了") == REASON_AFTER_SALES
    assert classify_escalation("你们就是骗子,我要去12315举报") == REASON_EMOTION
    # 误召方向：政策咨询/查价/砍价都不该触发规则层
    assert classify_escalation("已经拆封了还能退吗") is None
    assert classify_escalation("每日坚果礼盒 30 包现在什么价?") is None
    assert classify_escalation("再便宜点,不然我不买了") is None


def test_rule_escalates_even_without_llm_tag() -> None:
    """漏召防线：客户消息命中规则关键词时，即使 LLM 忘打标记也硬触发转人工+结构化原因。"""
    h = LLMHandler(transport=lambda m: "亲,收到,马上帮您看看哈~")  # LLM 没打标记
    s = SessionStore().get("u1")
    s.add(_msg(content="我要退款,你们发的坚果发霉了"))
    h.reply(s.history[-1], s)
    assert s.needs_human is True, "规则层应硬触发转人工"
    label, _, detail = s.escalation_reason.partition(":")
    assert label == REASON_AFTER_SALES, f"应为售后标签,实际 {s.escalation_reason!r}"
    assert "命中「" in detail, f"reason 应含命中关键词详情(#10 格式约定),实际 {s.escalation_reason!r}"


def test_llm_tag_reason_refined_by_rule() -> None:
    """LLM 打了标记时：原因优先用规则标签细分,否则落兜底标签。"""
    h = LLMHandler(transport=lambda m: f"帮你转专属客服哈~\n{ESCALATE_TAG}")
    # 命中"点名人工"规则 → 细分标签(reason 为「标签:命中「关键词」」格式)
    s1 = SessionStore().get("u1")
    s1.add(_msg(content="别机器人了,给我转人工"))
    h.reply(s1.history[-1], s1)
    assert s1.escalation_reason.partition(":")[0] == REASON_ASK_HUMAN
    # 规则未命中 → 兜底标签
    s2 = SessionStore().get("u2")
    s2.add(_msg(chat_id="u2", content="我这个订单的情况有点复杂,你帮我看看"))
    h.reply(s2.history[-1], s2)
    assert s2.needs_human is True
    assert s2.escalation_reason == REASON_LLM_JUDGED


def main() -> None:
    for fn in (
        test_escalate_tag_stripped_and_flagged,
        test_router_silent_when_human_controlled,
        test_router_fires_escalation_callback,
        test_router_normal_reply_regression,
        test_take_over_and_release,
        test_classify_escalation_labels,
        test_rule_escalates_even_without_llm_tag,
        test_llm_tag_reason_refined_by_rule,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n人工接管测试全部通过！")


if __name__ == "__main__":
    main()
