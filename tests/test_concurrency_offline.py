"""并发安全离线测试（CONV-B 全局审查修复的回归保护）。

覆盖 api_server 用 ThreadingHTTPServer 时共享 SessionStore/SqliteSessionStore 的场景：
并发 get 同一 chat_id 必须返回唯一 Session 对象（不能因 check-then-act 竞态分裂）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.session import SessionStore
from core.session_sqlite import SqliteSessionStore


def _concurrent_get_ids(store, chat_id: str, n: int = 32) -> set:
    """n 个线程用 Barrier 对齐后同时 get 同一 chat_id，返回拿到的 Session 对象 id 集合。"""
    barrier = threading.Barrier(n)
    results: list = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()  # 尽量让所有线程同时冲进 get()，放大竞态窗口
        s = store.get(chat_id)
        with lock:
            results.append(id(s))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return set(results)


def test_memory_store_concurrent_get_unique() -> None:
    """内存版 SessionStore：并发 get 同一 chat_id 只应产生一个 Session 对象。"""
    store = SessionStore()
    ids = _concurrent_get_ids(store, "race_user")
    assert len(ids) == 1, f"并发 get 应返回唯一 Session，实际出现 {len(ids)} 个"
    assert len(store.all()) == 1, "会话表里也应只有一个会话"


def test_sqlite_store_concurrent_get_unique() -> None:
    """SQLite 版 SqliteSessionStore：并发 get 同一 chat_id 只应产生一个 Session 对象。"""
    path = tempfile.mktemp(suffix=".db")
    store = SqliteSessionStore(path)
    try:
        ids = _concurrent_get_ids(store, "race_user")
        assert len(ids) == 1, f"并发 get 应返回唯一 Session，实际出现 {len(ids)} 个"
    finally:
        store.close()
        if os.path.exists(path):
            os.unlink(path)


def test_sqlite_persist_error_does_not_crash() -> None:
    """持久化遇到 DB 错误时应吞掉并降级（不上抛中断主链路）。"""
    from core.message import Message

    path = tempfile.mktemp(suffix=".db")
    store = SqliteSessionStore(path)
    session = store.get("u1")
    # 关掉底层连接，制造后续写库 sqlite3.Error
    store._conn.close()
    try:
        # add 会触发 _persist_message；不应抛异常
        session.add(Message(chat_id="u1", chat_type="single", msg_id="m1",
                            sender_id="u1", sender_name="u1", content="hi"))
        session.mark_needs_human("test")  # 触发 _persist_flags；同样不应抛
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"持久化失败不应上抛，实际抛了 {exc!r}")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def main() -> None:
    for fn in (
        test_memory_store_concurrent_get_unique,
        test_sqlite_store_concurrent_get_unique,
        test_sqlite_persist_error_does_not_crash,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n并发安全离线测试全部通过！")


if __name__ == "__main__":
    main()
