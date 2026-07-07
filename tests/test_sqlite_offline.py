"""SqliteSessionStore 持久化离线测试。纯 stdlib，用临时 db 文件。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_handler import LLMHandler
from core.message import Message
from core.router import Router
from core.session_sqlite import SqliteSessionStore


class SpyAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def start(self, on_message) -> None:
        pass

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _msg(chat_id: str, content: str, mid: str) -> Message:
    return Message(chat_id=chat_id, chat_type="single", msg_id=mid,
                   sender_id=chat_id, sender_name="客户", content=content)


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # 让 SqliteSessionStore 自己建
    return path


def test_messages_persist_across_restart() -> None:
    db = _tmp_db()
    try:
        store = SqliteSessionStore(path=db)
        s = store.get("u1")
        s.add(_msg("u1", "你好", "m1"))
        s.add(_msg("u1", "在吗", "m2"))
        store.close()

        store2 = SqliteSessionStore(path=db)  # 模拟重启
        contents = [m.content for m in store2.get("u1").history]
        assert contents == ["你好", "在吗"], f"重启后应恢复消息，实际 {contents}"
        store2.close()
    finally:
        os.path.exists(db) and os.unlink(db)


def test_flags_persist_across_restart() -> None:
    db = _tmp_db()
    try:
        store = SqliteSessionStore(path=db)
        store.get("u1").take_over()
        store.get("u2").mark_needs_human("投诉退款")
        store.close()

        store2 = SqliteSessionStore(path=db)
        assert store2.get("u1").human_controlled is True, "接管状态应持久化"
        s2 = store2.get("u2")
        assert s2.needs_human is True and s2.escalation_reason == "投诉退款", "转人工状态应持久化"
        store2.close()
    finally:
        os.path.exists(db) and os.unlink(db)


def test_message_dedup() -> None:
    db = _tmp_db()
    try:
        store = SqliteSessionStore(path=db)
        s = store.get("u1")
        s.add(_msg("u1", "你好", "m1"))
        s.add(_msg("u1", "你好", "m1"))  # 相同 msg_id
        store.close()

        store2 = SqliteSessionStore(path=db)
        assert len(store2.get("u1").history) == 1, "相同 msg_id 应去重（DB UNIQUE）"
        store2.close()
    finally:
        os.path.exists(db) and os.unlink(db)


def test_all_lists_sessions() -> None:
    db = _tmp_db()
    try:
        store = SqliteSessionStore(path=db)
        store.get("u1").add(_msg("u1", "a", "m1"))
        store.get("u2").add(_msg("u2", "b", "m2"))
        store.close()

        store2 = SqliteSessionStore(path=db)
        ids = {s.chat_id for s in store2.all()}
        assert ids == {"u1", "u2"}, f"all() 应列出所有会话，实际 {ids}"
        store2.close()
    finally:
        os.path.exists(db) and os.unlink(db)


def test_router_integration_persists() -> None:
    """Router + SqliteSessionStore：一轮对话后重启，用户消息与 bot 回复都在。"""
    db = _tmp_db()
    try:
        store = SqliteSessionStore(path=db)
        adapter = SpyAdapter()
        r = Router(adapter, LLMHandler(transport=lambda m: "亲，在的~"), store, min_interval_sec=0)
        r.on_message(_msg("u1", "你好", "m1"))
        assert adapter.sent == [("u1", "亲，在的~")]
        store.close()

        store2 = SqliteSessionStore(path=db)
        contents = [m.content for m in store2.get("u1").history]
        assert "你好" in contents and "亲，在的~" in contents, f"应持久化对话，实际 {contents}"
        store2.close()
    finally:
        os.path.exists(db) and os.unlink(db)


def main() -> None:
    for fn in (
        test_messages_persist_across_restart,
        test_flags_persist_across_restart,
        test_message_dedup,
        test_all_lists_sessions,
        test_router_integration_persists,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\nSQLite 持久化测试全部通过！")


if __name__ == "__main__":
    main()
