"""入口：按 MOCK 环境变量选适配器，装配 Router 后启动。

    MOCK=1 [MOCK_SCRIPT=sample.json] python3 main.py   # 本地跑通（Mac）
    python3 main.py                                     # 走真实 ntwork（仅 Windows）
"""
from __future__ import annotations

import os

from core.router import Router
from core.session import SessionStore


def build_adapter():
    if os.environ.get("MOCK") == "1":
        from adapters.mock_cli import MockCliAdapter
        return MockCliAdapter(script_path=os.environ.get("MOCK_SCRIPT"))
    from adapters.ntwork_wecom import NtworkWecomAdapter
    return NtworkWecomAdapter()


def build_handler():
    handler_type = os.environ.get("HANDLER", "echo")
    if handler_type == "llm":
        from core.llm_handler import LLMHandler
        return LLMHandler()
    from core.handler import EchoHandler
    return EchoHandler()


def load_env_file(path: str = ".env") -> None:
    """极简 .env 加载：把 KEY=VALUE 读进 os.environ（不覆盖已存在的）。纯 stdlib。"""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(here):
        return
    with open(here, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def main() -> None:
    load_env_file()
    adapter = build_adapter()
    router = Router(adapter, build_handler(), SessionStore())
    adapter.start(router.on_message)


if __name__ == "__main__":
    main()
