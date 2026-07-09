"""main.py 装配层离线测试：build_adapter / build_knowledge / build_sessions / build_handler 各分支。

之前 main.py 作为入口装配层完全无测试——任何装配/分支错误都要到启动时才暴露。
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from core.handler import EchoHandler
from core.knowledge import HybridKnowledgeProvider, RagKnowledgeProvider, StructuredKnowledgeProvider
from core.llm_handler import LLMHandler
from core.session import SessionStore
from core.session_sqlite import SqliteSessionStore

# 合法的 43 位 EncodingAESKey（base64 解出 32 字节），供 ADAPTER=kf 分支构造用
_VALID_AES = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"


@contextlib.contextmanager
def env(**kv):
    """临时设置/清除环境变量，退出恢复原值。"""
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_build_knowledge_branches() -> None:
    with env(KNOWLEDGE_PROVIDER="static"):
        assert main.build_knowledge() is None, "static → None（由 LLMHandler 兜底默认 Static）"
    with env(KNOWLEDGE_PROVIDER=None):
        assert main.build_knowledge() is None, "未设 → 默认 static → None"
    with env(KNOWLEDGE_PROVIDER="rag"):
        assert isinstance(main.build_knowledge(), RagKnowledgeProvider)
    with env(KNOWLEDGE_PROVIDER="structured"):
        assert isinstance(main.build_knowledge(), StructuredKnowledgeProvider)
    with env(KNOWLEDGE_PROVIDER="hybrid"):
        assert isinstance(main.build_knowledge(), HybridKnowledgeProvider)


def test_build_sessions_branches() -> None:
    with env(STORE=None):
        assert isinstance(main.build_sessions(), SessionStore)
    path = tempfile.mktemp(suffix=".db")
    try:
        with env(STORE="sqlite", SQLITE_PATH=path):
            s = main.build_sessions()
            assert isinstance(s, SqliteSessionStore)
            s.close()
    finally:
        for p in (path, path + "-wal", path + "-shm"):
            if os.path.exists(p):
                os.unlink(p)


def test_build_handler_branches() -> None:
    with env(HANDLER="echo"):
        assert isinstance(main.build_handler(), EchoHandler)
    with env(HANDLER=None):
        assert isinstance(main.build_handler(), EchoHandler), "默认 echo"
    with env(HANDLER="llm", KNOWLEDGE_PROVIDER="static"):
        assert isinstance(main.build_handler(), LLMHandler)


def test_build_adapter_branches() -> None:
    from adapters.mock_cli import MockCliAdapter
    from adapters.ntwork_wecom import NtworkWecomAdapter
    from adapters.wecom_kf import WecomKfAdapter
    with env(ADAPTER=None, MOCK="1"):
        assert isinstance(main.build_adapter(), MockCliAdapter)
    with env(ADAPTER=None, MOCK=None):
        assert isinstance(main.build_adapter(), NtworkWecomAdapter)
    # ADAPTER=kf 需合法 EncodingAESKey；用临时 cursor 路径避免污染 data/
    cur = tempfile.mktemp(suffix=".cursor")
    try:
        with env(ADAPTER="kf", WECOM_ENCODING_AES_KEY=_VALID_AES,
                 WECOM_CORP_ID="c", WECOM_CALLBACK_TOKEN="t"):
            a = main.build_adapter()
            assert isinstance(a, WecomKfAdapter)
    finally:
        if os.path.exists(cur):
            os.unlink(cur)


def test_load_env_file_no_override() -> None:
    """load_env_file 不覆盖已存在的环境变量（os.environ.setdefault 语义）。"""
    with env(HANDLER="llm"):
        main.load_env_file()  # .env 里 HANDLER=llm，但已存在则不覆盖
        assert os.environ["HANDLER"] == "llm"


def main_() -> None:
    for fn in (
        test_build_knowledge_branches,
        test_build_sessions_branches,
        test_build_handler_branches,
        test_build_adapter_branches,
        test_load_env_file_no_override,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\nmain.py 装配层离线测试全部通过！")


if __name__ == "__main__":
    main_()
