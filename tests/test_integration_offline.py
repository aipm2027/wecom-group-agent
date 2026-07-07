"""端到端集成测试：串起 T1(接管/转人工) + T2(SQLite持久化) + T3(REST API)。

模拟一条完整私域链路，全离线（假 transport + 临时 db + spy adapter）：
agent 自动回 → 转人工升级 → 进队列 → 人工接管 → agent 静默 → 人工发消息
→ 重启后状态/历史都在 → 交回后 agent 恢复。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_server import ApiApp
from core.llm_handler import ESCALATE_TAG, LLMHandler
from core.message import Message
from core.router import Router
from core.session_sqlite import SqliteSessionStore

CID = "cust1"


class SpyAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def start(self, on_message) -> None:
        pass

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _transport(messages):
    """假 LLM：客户提到"退款"就升级转人工，否则普通回复。"""
    last = messages[-1]["content"]
    if "退款" in last:
        return f"这边帮你转专属客服跟进哈~\n{ESCALATE_TAG}"
    return "亲，在的~"


def _cust_msg(content: str, mid: str) -> Message:
    return Message(chat_id=CID, chat_type="single", msg_id=mid,
                   sender_id=CID, sender_name="老客", content=content)


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run() -> None:
    db = _tmp_db()
    try:
        # ---------- 阶段 1：store A ----------
        store = SqliteSessionStore(path=db)
        adapter = SpyAdapter()
        handler = LLMHandler(transport=_transport)
        escalated: list = []
        router = Router(adapter, handler, store, min_interval_sec=0,
                        on_escalate=lambda s: escalated.append(s.chat_id))
        app = ApiApp(store, handler, adapter=adapter, admin_token="T")
        auth = {"authorization": "Bearer T"}

        # 1) 普通消息 → agent 自动回
        router.on_message(_cust_msg("你好", "m1"))
        assert adapter.sent[-1] == (CID, "亲，在的~"), "agent 应自动回复"

        # 2) 提到退款 → 升级转人工（标记被剥离、回调触发）
        router.on_message(_cust_msg("我买的坏了要退款", "m2"))
        assert ESCALATE_TAG not in adapter.sent[-1][1], "控制标记不应发给客户"
        assert store.get(CID).needs_human is True, "应标记需人工"
        assert escalated == [CID], "升级回调应触发"

        # 3) 进人工队列
        st, pl = app.handle("GET", "/api/queue", b"", auth)
        assert st == 200 and [c["chat_id"] for c in pl["queue"]] == [CID], "会话应在待接管队列"

        # 4) 人工接管
        assert app.handle("POST", f"/api/conversations/{CID}/takeover", b"", auth)[0] == 200
        assert store.get(CID).human_controlled is True

        # 5) 接管后 agent 静默
        before = len(adapter.sent)
        router.on_message(_cust_msg("那你们赔我多少", "m3"))
        assert len(adapter.sent) == before, "接管后 agent 不应自动回"

        # 6) 人工通过 API 发消息
        human_text = "亲这边专属客服帮你处理，已安排退款~"
        st, pl = app.handle("POST", f"/api/conversations/{CID}/messages",
                            ('{"text":"%s"}' % human_text).encode("utf-8"), auth)
        assert st == 200 and adapter.sent[-1][1] == human_text

        store.close()

        # ---------- 阶段 2：重启（store B，同一 db）----------
        store2 = SqliteSessionStore(path=db)
        s = store2.get(CID)
        contents = [m.content for m in s.history]
        assert "你好" in contents and "亲，在的~" in contents, "重启后历史应恢复"
        assert any("退款" in c for c in contents), "客户退款消息应在"
        assert any("专属客服帮你处理" in c for c in contents), "人工消息应持久化"
        assert s.human_controlled is True, "接管状态应持久化"

        # 7) 仍接管中 → agent 静默
        adapter2 = SpyAdapter()
        router2 = Router(adapter2, handler, store2, min_interval_sec=0)
        router2.on_message(_cust_msg("在吗", "m4"))
        assert adapter2.sent == [], "重启后仍接管，agent 应静默"

        # 8) 人工交回 → agent 恢复
        app2 = ApiApp(store2, handler, adapter=adapter2, admin_token="T")
        assert app2.handle("POST", f"/api/conversations/{CID}/release", b"", auth)[0] == 200
        router2.on_message(_cust_msg("你好", "m5"))
        assert adapter2.sent[-1] == (CID, "亲，在的~"), "交回后 agent 应恢复自动回复"
        store2.close()
    finally:
        os.path.exists(db) and os.unlink(db)


def main() -> None:
    run()
    print("通过: 端到端集成流程（接管/转人工/持久化/API/交回）")
    print("\n集成测试通过！")


if __name__ == "__main__":
    main()
