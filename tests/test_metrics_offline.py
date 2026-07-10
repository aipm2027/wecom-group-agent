"""可观测骨架(P2-8)离线测试:handler 计数器落盘 + /api/metrics 聚合与合并。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_server import ApiApp
from core.handler import EchoHandler
from core.llm_handler import ESCALATE_TAG, LLMHandler
from core.message import Message
from core.session import SessionStore


def _msg(chat_id: str, content: str, mid: str) -> Message:
    return Message(chat_id=chat_id, chat_type="single", msg_id=mid,
                   sender_id=chat_id, sender_name="客户", content=content)


def test_handler_stats_written() -> None:
    """配置 AGENT_STATS_FILE 后:回复量/降级/转人工/延迟计数原子落盘。"""
    tf = tempfile.mktemp(suffix=".json")
    os.environ["AGENT_STATS_FILE"] = tf
    try:
        calls = {"n": 0}

        def transport(m):
            calls["n"] += 1
            if calls["n"] == 2:
                return ""  # 空回复 → 兜底(降级+1)
            if calls["n"] == 3:
                return f"帮你转专属客服哈~\n{ESCALATE_TAG}"  # 转人工+1
            return "正常回复"

        h = LLMHandler(transport=transport)
        store = SessionStore()
        for i in range(3):
            s = store.get(f"u{i}")
            s.add(_msg(f"u{i}", "在吗", f"m{i}"))
            h.reply(s.history[-1], s)

        with open(tf, encoding="utf-8") as f:
            stats = json.load(f)
        assert stats["replies_total"] == 3, stats
        assert stats["fallback_total"] == 1, "空回复应计一次降级"
        assert stats["escalation_marks_total"] == 1, "打标记应计一次转人工"
        assert stats["latency_count"] == 3 and stats["latency_ms_total"] >= 0
        assert stats["updated_at"] > 0
    finally:
        os.environ.pop("AGENT_STATS_FILE", None)
        if os.path.exists(tf):
            os.unlink(tf)


def test_handler_stats_off_by_default() -> None:
    """未配置 AGENT_STATS_FILE:不落盘、零副作用(内存计数仍工作)。"""
    os.environ.pop("AGENT_STATS_FILE", None)
    h = LLMHandler(transport=lambda m: "ok")
    s = SessionStore().get("u1")
    s.add(_msg("u1", "你好", "m1"))
    h.reply(s.history[-1], s)
    assert h._stats["replies_total"] == 1
    assert h._stats_path == ""


def test_metrics_reason_aggregation_and_agent_merge() -> None:
    """/api/metrics:escalation_reason 按前缀聚合;stats 文件存在时合并 agent 段。"""
    tf = tempfile.mktemp(suffix=".json")
    with open(tf, "w", encoding="utf-8") as f:
        json.dump({"replies_total": 10, "fallback_total": 2, "escalation_marks_total": 3,
                   "latency_ms_total": 5000, "latency_count": 10, "updated_at": 1}, f)
    os.environ["AGENT_STATS_FILE"] = tf
    try:
        store = SessionStore()
        s1 = store.get("u1")
        s1.mark_needs_human("售后退款/理赔:命中「退款」")
        s2 = store.get("u2")
        s2.mark_needs_human("售后退款/理赔:命中「投诉」")
        s3 = store.get("u3")
        s3.mark_needs_human("客户点名找人工:命中「转人工」")
        app = ApiApp(store, EchoHandler(), adapter=None, admin_token="")
        status, data = app.handle("GET", "/api/metrics", b"", {})
        assert status == 200
        assert data["escalation_by_reason"] == {"售后退款/理赔": 2, "客户点名找人工": 1}, data
        assert data["agent"]["replies_total"] == 10
        assert data["agent"]["avg_latency_ms"] == 500
    finally:
        os.environ.pop("AGENT_STATS_FILE", None)
        os.unlink(tf)


def test_metrics_agent_absent_when_no_file() -> None:
    """stats 文件不存在:metrics 不带 agent 段,四个既有计数语义不变。"""
    os.environ.pop("AGENT_STATS_FILE", None)
    app = ApiApp(SessionStore(), EchoHandler(), adapter=None, admin_token="")
    status, data = app.handle("GET", "/api/metrics", b"", {})
    assert status == 200
    assert "agent" not in data
    assert data["conversations"] == 0 and data["messages"] == 0


def main() -> None:
    for fn in (
        test_handler_stats_written,
        test_handler_stats_off_by_default,
        test_metrics_reason_aggregation_and_agent_merge,
        test_metrics_agent_absent_when_no_file,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n可观测骨架离线测试全部通过！")


if __name__ == "__main__":
    main()
