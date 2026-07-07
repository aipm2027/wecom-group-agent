"""知识模块（可插拔）。

把"给 agent 提供业务知识"抽象成 KnowledgeProvider，延续 Adapter/Handler 的可插拔
思路：换知识后端不改 agent —— LLMHandler 只依赖 retrieve(query)。

- StaticKnowledgeProvider（当前默认）：读知识文件**全量**返回（忽略 query），等于把整份
  知识塞进 prompt。适合小知识库（几十个 SKU + 简单 FAQ）。零依赖。
- RagKnowledgeProvider（规模化，stub）：知识切块 + 向量检索，按 query 只取相关片段。
  适合上百 SKU / 大量 FAQ。需 embedding + 向量检索（可选依赖），要点见类注释。
- （电商可选，见文档）结构化商品库 + 属性/关键词精确检索，可与 RAG 组合：
  结构化查"价/规格"，RAG 查语义 FAQ。

设计详见 docs/技术文档/05-知识模块设计.md。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class KnowledgeProvider(ABC):
    @abstractmethod
    def retrieve(self, query: str) -> str:
        """返回与本次 query 相关的知识文本（会拼进 system prompt 的"店铺知识"部分）。

        query 是当前客户消息。静态实现可忽略它（返回全量）；检索实现据它取 top-k。
        """
        raise NotImplementedError


class StaticKnowledgeProvider(KnowledgeProvider):
    """全量返回知识文件内容（忽略 query）。当前默认，适合小知识库。"""

    def __init__(self, path: str = "prompts/knowledge.md") -> None:
        self._path = path if os.path.isabs(path) else os.path.join(_ROOT, path)

    def retrieve(self, query: str) -> str:
        try:
            with open(self._path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""


class RagKnowledgeProvider(KnowledgeProvider):
    """检索式知识（stub，规模化再实现）。

    实现要点：
    1) 索引期：知识按条目（商品/FAQ）切块 → 调 embedding 接口存 [向量, 原文]。
    2) 查询期：query 向量化 → 余弦 top-k → 拼相关片段返回。
    3) 依赖：embedding（可复用 StepFun/OpenAI 兼容接口）+ 向量存储（小规模内存/JSON，
       大规模用向量库）。作为可选依赖 guard import，不污染零依赖核心。
    详见 docs/技术文档/05-知识模块设计.md。
    """

    def retrieve(self, query: str) -> str:
        raise NotImplementedError(
            "RagKnowledgeProvider 尚未实现（规模化再上）；当前请用 StaticKnowledgeProvider。"
        )
