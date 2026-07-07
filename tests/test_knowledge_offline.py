"""知识模块离线测试。不依赖网络。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge import KnowledgeProvider, RagKnowledgeProvider, StaticKnowledgeProvider


def test_static_returns_file_content() -> None:
    """静态实现全量返回知识文件内容（忽略 query）。"""
    kp = StaticKnowledgeProvider()
    a = kp.retrieve("多少钱")
    b = kp.retrieve("完全无关的问题")
    assert a and a == b, "静态实现应忽略 query、每次返回同样的全量内容"


def test_rag_is_stub() -> None:
    """RAG 实现当前是 stub，应明确抛未实现。"""
    try:
        RagKnowledgeProvider().retrieve("x")
        raise AssertionError("RagKnowledgeProvider 应抛 NotImplementedError")
    except NotImplementedError:
        pass


def test_provider_is_pluggable() -> None:
    """自定义 Provider 可无缝替换（LLMHandler 只依赖 retrieve）。"""
    class FixedKP(KnowledgeProvider):
        def retrieve(self, query: str) -> str:
            return f"[知识:{query}]"

    from core.llm_handler import LLMHandler
    h = LLMHandler(transport=lambda m: "ok", knowledge=FixedKP())
    sp = h._compose_system("测试查询")
    assert "[知识:测试查询]" in sp, "LLMHandler 应使用注入的 KnowledgeProvider"


def main() -> None:
    for fn in (test_static_returns_file_content, test_rag_is_stub, test_provider_is_pluggable):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n知识模块测试全部通过！")


if __name__ == "__main__":
    main()
