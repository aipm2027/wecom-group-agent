"""入口：按 MOCK 环境变量选适配器，装配 Router 后启动。

    MOCK=1 [MOCK_SCRIPT=sample.json] python3 main.py   # 本地跑通（Mac）
    python3 main.py                                     # 走真实 ntwork（仅 Windows）
"""
from __future__ import annotations

import os
import signal
import sys

from core.router import Router
from core.session import SessionStore


def build_adapter():
    if os.environ.get("ADAPTER") == "kf":
        # 微信客服（官方合规 1:1，公网回调）
        from adapters.wecom_kf import WecomKfAdapter
        return WecomKfAdapter()
    if os.environ.get("ADAPTER") == "aibot":
        # 智能机器人（官方长连接，企业内部成员单聊/群聊@bot，无需公网）
        from adapters.wecom_aibot import WecomAibotAdapter
        return WecomAibotAdapter()
    if os.environ.get("MOCK") == "1":
        from adapters.mock_cli import MockCliAdapter
        return MockCliAdapter(script_path=os.environ.get("MOCK_SCRIPT"))
    from adapters.ntwork_wecom import NtworkWecomAdapter
    return NtworkWecomAdapter()


def build_knowledge():
    """按 KNOWLEDGE_PROVIDER 选知识后端；返回 None 则沿用 LLMHandler 内置默认(Static)。

    取值：static（默认）| rag（向量检索）| structured（结构化商品库精确查价/规格）
    | hybrid（结构化商品 + 全量知识兜底）。
    换后端只动这里——LLMHandler 只依赖 KnowledgeProvider.retrieve(query)。
    """
    kind = os.environ.get("KNOWLEDGE_PROVIDER", "static").lower()
    if kind == "rag":
        from core.knowledge import RagKnowledgeProvider
        return RagKnowledgeProvider()
    if kind == "structured":
        from core.knowledge import StructuredKnowledgeProvider
        return StructuredKnowledgeProvider()
    if kind == "hybrid":
        from core.knowledge import (HybridKnowledgeProvider,
                                     StaticKnowledgeProvider,
                                     StructuredKnowledgeProvider)
        return HybridKnowledgeProvider(StructuredKnowledgeProvider(), StaticKnowledgeProvider())
    return None  # static / 未知 → 用 LLMHandler 默认的 StaticKnowledgeProvider


def build_sessions():
    """按 STORE 选会话存储：memory（默认，内存版）| sqlite（持久化，重启不丢）。

    与 api_server.py 的 build_app 保持一致：两进程设同一个 SQLITE_PATH 即可共享会话，
    否则主进程（本文件）与运营后台 API 各自读写不同存储，状态会不一致。
    """
    if os.environ.get("STORE") == "sqlite":
        from core.session_sqlite import SqliteSessionStore
        return SqliteSessionStore(os.environ.get("SQLITE_PATH", "data/sessions.db"))
    return SessionStore()


def _default_on_escalate(session) -> None:
    """默认升级回调：转人工时打印到 stderr，让主入口也能看到升级信号。

    生产可替换为写工单队列 / 调 webhook 通知工作台（见 docs 06 人工接管章节）。
    """
    reason = getattr(session, "escalation_reason", "") or "需人工介入"
    print(f"[escalate] 会话 {session.chat_id} 需人工介入：{reason}", file=sys.stderr)


def build_handler():
    handler_type = os.environ.get("HANDLER", "echo")
    if handler_type == "llm":
        from core.llm_handler import LLMHandler
        return LLMHandler(knowledge=build_knowledge())
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


_KEY_PLACEHOLDER = "在这里填你的密钥"  # .env.example 的模板占位值


def check_config(require_adapter: bool = True) -> list[str]:
    """启动自检（路线图 P0）：返回问题清单，空列表 = 通过。

    宁可拒绝启动，不进入静默降级——漏配 LLM_API_KEY 会静默变成固定兜底话术
    （运营者花着钱以为 AI 在工作）、Mac 上不设 MOCK/ADAPTER 会走 ntwork stub
    直接崩、ADAPTER=kf 漏企微配置则进程正常启动但收发永远静默失败。
    api_server 复用时传 require_adapter=False（其 adapter 本就可选）。
    """
    problems: list[str] = []
    env = os.environ.get
    root = os.path.dirname(os.path.abspath(__file__))

    if require_adapter:
        if env("ADAPTER") == "kf":
            missing = [k for k in ("WECOM_CORP_ID", "WECOM_KF_SECRET",
                                   "WECOM_CALLBACK_TOKEN", "WECOM_ENCODING_AES_KEY")
                       if not (env(k) or "").strip()]
            if missing:
                problems.append("ADAPTER=kf 缺少企微配置: " + "、".join(missing)
                                + " —— 到企微管理后台「微信客服」获取后填入 .env")
        elif env("ADAPTER") == "aibot":
            missing = [k for k in ("WECOM_AIBOT_ID", "WECOM_AIBOT_SECRET")
                       if not (env(k) or "").strip()]
            if missing:
                problems.append("ADAPTER=aibot 缺少配置: " + "、".join(missing)
                                + " —— 企微后台「智能机器人」API 模式(长连接)页复制后填入 .env")
        elif env("MOCK") != "1":
            problems.append("未选择可用适配器 —— 本地模拟请设 MOCK=1；接微信客服请设 ADAPTER=kf"
                            "（默认的企微群 hook 仅 Windows 且是未实现 stub）")
        script = env("MOCK_SCRIPT")
        if env("MOCK") == "1" and script and not os.path.exists(script):
            problems.append(f"MOCK_SCRIPT 文件不存在: {script}")

    if env("HANDLER", "echo") == "llm":
        key = (env("LLM_API_KEY") or "").strip()
        if not key or key == _KEY_PLACEHOLDER:
            problems.append("HANDLER=llm 但 LLM_API_KEY 未配置（会静默变成固定兜底话术）"
                            " —— 填 .env 的 LLM_API_KEY；或先用 HANDLER=echo 离线试跑")
        for var in ("LLM_PERSONA_FILE", "LLM_KNOWLEDGE_FILE"):
            p = env(var)
            if p and not os.path.exists(p if os.path.isabs(p) else os.path.join(root, p)):
                problems.append(f"{var} 文件不存在: {p}")

    kind = env("KNOWLEDGE_PROVIDER", "static").lower()
    if kind not in ("static", "rag", "structured", "hybrid"):
        problems.append(f"KNOWLEDGE_PROVIDER={kind!r} 无此取值 —— 可选 static|rag|structured|hybrid")

    store = env("STORE", "")
    if store not in ("", "memory", "sqlite"):
        problems.append(f"STORE={store!r} 无此取值 —— sqlite（持久化）或不设（内存）")

    return problems


def exit_if_misconfigured(require_adapter: bool = True) -> None:
    """自检不过则打印全部问题（含修复指引）并以退出码 2 终止。"""
    problems = check_config(require_adapter)
    if problems:
        print("[启动自检] 配置有问题，拒绝启动（修复后重试）：", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    load_env_file()
    exit_if_misconfigured()
    adapter = build_adapter()
    router = Router(adapter, build_handler(), build_sessions(), on_escalate=_default_on_escalate)

    def _term(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _term)  # 容器/systemd 停止(SIGTERM)走优雅退出
    except (ValueError, OSError):
        pass  # 非主线程等场景忽略
    try:
        adapter.start(router.on_message)
    except KeyboardInterrupt:
        print("[main] 收到终止信号，正在退出。", file=sys.stderr)


if __name__ == "__main__":
    main()
