"""REST API 层离线测试。直接测 ApiApp.handle()，无 socket、无网络。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_server import ApiApp
from core.handler import EchoHandler
from core.message import Message
from core.router import Router
from core.session import SessionStore

AUTH = {"authorization": "Bearer T"}


class SpyAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def start(self, on_message) -> None:
        pass

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _app(sessions=None, adapter=None):
    return ApiApp(sessions or SessionStore(), EchoHandler(), adapter=adapter, admin_token="T")


def _body(d) -> bytes:
    return json.dumps(d).encode("utf-8")


def test_health_no_auth() -> None:
    status, payload = _app().handle("GET", "/api/health", b"", {})
    assert status == 200 and payload["ok"] is True


def test_auth_required() -> None:
    app = _app()
    assert app.handle("GET", "/api/conversations", b"", {})[0] == 401, "无 token 应 401"
    assert app.handle("GET", "/api/conversations", b"", AUTH)[0] == 200, "带 token 应通过"


def test_conversations_and_messages() -> None:
    sessions = SessionStore()
    sessions.get("u1").add(Message(chat_id="u1", chat_type="single", msg_id="m1",
                                   sender_id="u1", sender_name="张三", content="你好"))
    app = _app(sessions)
    st, pl = app.handle("GET", "/api/conversations", b"", AUTH)
    assert st == 200 and pl["conversations"][0]["chat_id"] == "u1"
    st, pl = app.handle("GET", "/api/conversations/u1/messages", b"", AUTH)
    assert st == 200 and pl["messages"][0]["content"] == "你好"


def test_takeover_release_and_router_silence() -> None:
    """API 接管 → 同一 SessionStore 的 Router 静默；release 后恢复。"""
    sessions = SessionStore()
    app = _app(sessions)
    adapter = SpyAdapter()
    router = Router(adapter, EchoHandler(), sessions, min_interval_sec=0)

    assert app.handle("POST", "/api/conversations/u1/takeover", b"", AUTH)[0] == 200
    assert sessions.get("u1").human_controlled is True
    router.on_message(Message(chat_id="u1", chat_type="single", msg_id="m1",
                              sender_id="u1", sender_name="张三", content="在吗"))
    assert adapter.sent == [], "接管后 agent 不应自动回复"

    assert app.handle("POST", "/api/conversations/u1/release", b"", AUTH)[0] == 200
    router.on_message(Message(chat_id="u1", chat_type="single", msg_id="m2",
                              sender_id="u1", sender_name="张三", content="在吗"))
    assert adapter.sent == [("u1", "收到：在吗")], "交回后 agent 恢复回复"


def test_human_send() -> None:
    sessions = SessionStore()
    adapter = SpyAdapter()
    app = _app(sessions, adapter=adapter)
    st, pl = app.handle("POST", "/api/conversations/u1/messages", _body({"text": "亲这边人工帮你处理"}), AUTH)
    assert st == 200 and pl["sent"] is True
    hist = sessions.get("u1").history
    assert hist[-1].content == "亲这边人工帮你处理" and hist[-1].sender_id == "human-agent"
    assert adapter.sent == [("u1", "亲这边人工帮你处理")]


def test_agent_preview_not_persisted() -> None:
    sessions = SessionStore()
    app = _app(sessions)
    st, pl = app.handle("POST", "/api/agent/preview", _body({"text": "多少钱"}), AUTH)
    assert st == 200 and pl["reply"] == "收到：多少钱"
    assert sessions.all() == [], "试聊不应污染真实会话"


def test_config_no_key_leak() -> None:
    os.environ["LLM_API_KEY"] = "sk-should-not-leak"
    try:
        st, pl = _app().handle("GET", "/api/config", b"", AUTH)
        assert st == 200
        assert "sk-should-not-leak" not in json.dumps(pl), "配置接口绝不能泄漏密钥"
        assert "handler" in pl
    finally:
        os.environ.pop("LLM_API_KEY", None)


def test_metrics_and_404() -> None:
    sessions = SessionStore()
    sessions.get("u1").add(Message(chat_id="u1", chat_type="single", msg_id="m1",
                                   sender_id="u1", sender_name="a", content="hi"))
    sessions.get("u2").mark_needs_human("投诉")
    app = _app(sessions)
    st, pl = app.handle("GET", "/api/metrics", b"", AUTH)
    assert st == 200 and pl["conversations"] == 2 and pl["needs_human"] == 1
    assert app.handle("GET", "/api/nope", b"", AUTH)[0] == 404


def test_bad_json() -> None:
    st, _ = _app().handle("POST", "/api/agent/preview", b"{not json", AUTH)
    assert st == 400, "坏 JSON 应 400"


def test_queue_lists_needs_human_unclaimed() -> None:
    sessions = SessionStore()
    sessions.get("u1").mark_needs_human("投诉")             # 待接管 → 在队列
    sessions.get("u2").take_over()                           # 已接管 → 不在队列
    s3 = sessions.get("u3"); s3.mark_needs_human("退款"); s3.take_over()  # 接管后清标记 → 不在
    st, pl = _app(sessions).handle("GET", "/api/queue", b"", AUTH)
    assert st == 200
    ids = [c["chat_id"] for c in pl["queue"]]
    assert ids == ["u1"], f"队列只应含待接管的 u1，实际 {ids}"


def main() -> None:
    for fn in (
        test_health_no_auth,
        test_auth_required,
        test_conversations_and_messages,
        test_takeover_release_and_router_silence,
        test_human_send,
        test_agent_preview_not_persisted,
        test_config_no_key_leak,
        test_metrics_and_404,
        test_bad_json,
        test_queue_lists_needs_human_unclaimed,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\nREST API 测试全部通过！")


if __name__ == "__main__":
    main()
