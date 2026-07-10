"""知识模块（可插拔）。

把"给 agent 提供业务知识"抽象成 KnowledgeProvider，延续 Adapter/Handler 的可插拔
思路：换知识后端不改 agent —— LLMHandler 只依赖 retrieve(query)。

- StaticKnowledgeProvider（当前默认）：读知识文件**全量**返回（忽略 query），等于把整份
  知识塞进 prompt。适合小知识库（几十个 SKU + 简单 FAQ）。零依赖。
- RagKnowledgeProvider（规模化）：知识切块 + 混合检索，按 query 只取相关片段。
  适合中知识库。纯 stdlib，embedding 走 HTTP 或可注入 embed_fn。
- （电商可选，见文档）结构化商品库 + 属性/关键词精确检索，可与 RAG 组合：
  结构化查"价/规格"，RAG 查语义 FAQ。

设计详见 docs/技术文档/05-知识模块设计.md。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
import unicodedata
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from abc import ABC, abstractmethod

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# RAG 常驻关键块关键词（命中即无条件纳入结果）与结构化商品状态中文映射；
# 提到模块级，避免每次检索/格式化时重复创建。
_RESIDENT_KEYWORDS = ("活动", "主推", "优惠", "促销", "满减", "折扣", "特价", "限时")
_STATUS_MAP = {"on_sale": "在售", "off_sale": "已下架", "out_of_stock": "已售罄"}

# ── 商品库 schema 校验（P2-7）──────────────────────────────────
# 轻量逐条校验：坏条目跳过并逐条报错（含下标/ID），好条目照常服务——
# 不再"一条脏数据无从发现"，也绝不因个别条目损坏而整库不可用。
_PRODUCT_REQUIRED = (("product_id", str), ("name", str), ("price", (int, float)))


def validate_products(data) -> "tuple[list[dict], list[str]]":
    """校验商品数组，返回 (有效条目, 问题清单)。供加载路径与启动自检复用。"""
    problems: list[str] = []
    if not isinstance(data, list):
        return [], ["JSON 根不是数组（应为商品对象的列表）"]
    valid: list[dict] = []
    seen_ids: set = set()
    for i, item in enumerate(data):
        where = f"第 {i + 1} 条"
        if not isinstance(item, dict):
            problems.append(f"{where}: 不是对象，已跳过")
            continue
        pid = item.get("product_id")
        if isinstance(pid, str) and pid:
            where = f"第 {i + 1} 条({pid})"
        bad = False
        for field, typ in _PRODUCT_REQUIRED:
            v = item.get(field)
            if v is None or v == "" or not isinstance(v, typ) or isinstance(v, bool):
                problems.append(f"{where}: 必填字段 {field} 缺失或类型不对，已跳过")
                bad = True
        if bad:
            continue
        kws = item.get("keywords")
        if kws is not None and (not isinstance(kws, list)
                                or any(not isinstance(k, str) for k in kws)):
            problems.append(f"{where}: keywords 应为字符串数组，该字段已忽略")
            item = dict(item, keywords=[])
        status = item.get("status", "")
        if status and status not in _STATUS_MAP:
            problems.append(f"{where}: status={status!r} 不在已知取值 {sorted(_STATUS_MAP)}（保留原样，展示为原文）")
        if item["product_id"] in seen_ids:
            problems.append(f"{where}: product_id 重复（两条都会参与检索，请检查数据源）")
        seen_ids.add(item["product_id"])
        valid.append(item)
    return valid, problems


def validate_products_file(path: str = "prompts/products.json") -> "list[str]":
    """校验商品文件，返回问题清单（空=干净）。给 main.check_config 启动自检用。"""
    real = _resolve_path(path)
    try:
        with open(real, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        return [f"商品文件读不到: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"商品文件 JSON 语法错误（整库将不可用）: {exc}"]
    return validate_products(data)[1]


# ── 规格标准化（P2-7）──────────────────────────────────────────
# 客户口语与商品数据的单位写法常不一致（"半斤"↔"250g"、"２５０ｇ"全角、"0.5 千克"），
# 子串匹配对这类变体召回≈0。归一到克数集合后求交集，作为 _is_hit 的补充通路。
_UNIT_GRAMS = (("千克", 1000.0), ("公斤", 1000.0), ("kg", 1000.0),
               ("斤", 500.0), ("两", 50.0), ("克", 1.0), ("g", 1.0))
_GRAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(千克|公斤|kg|斤|两|克|g)")


def _normalize_text(text: str) -> str:
    """全角→半角、统一小写（unicodedata.NFKC 顺带把 ２５０ｇ → 250g）。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def _extract_grams(text: str) -> "set[float]":
    """提取文本中的重量表达并归一为克数集合（识别 半斤/N斤/N两/N克/Ng/N千克/Nkg）。"""
    t = _normalize_text(text)
    grams = {float(num) * unit
             for num, unit_name in _GRAM_RE.findall(t)
             for u_name, unit in _UNIT_GRAMS if u_name == unit_name}
    if "半斤" in t:
        grams.add(250.0)
    if "半公斤" in t or "半千克" in t:
        grams.add(500.0)
    return grams


def _resolve_path(path: str) -> str:
    """相对路径按项目根解析，绝对路径原样返回。"""
    return path if os.path.isabs(path) else os.path.join(_ROOT, path)


def _normalize(v: list[float]) -> list[float]:
    """L2 归一化向量（纯 Python）。"""
    norm = sum(x * x for x in v) ** 0.5
    if not norm:
        return [0.0] * len(v)
    return [x / norm for x in v]


def _extract_tokens(text: str) -> list[str]:
    """提取 query 中长度 ≥2 的字符 token，用于子串匹配。"""
    return [t for t in re.findall(r"[a-zA-Z0-9一-鿿]+", text) if len(t) >= 2]


def _char_bigrams(text: str) -> set[str]:
    """生成字符 2-gram 集合（Chinese 友好）。"""
    chars = list(text)
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


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
        self._path = _resolve_path(path)

    def retrieve(self, query: str) -> str:
        try:
            with open(self._path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""


class RagKnowledgeProvider(KnowledgeProvider):
    """检索式知识（RAG 优化版）。

    设计要点：
    1) 纯 stdlib：向量化走 HTTP embedding 接口（urllib，OpenAI 兼容 /embeddings），
       余弦相似度纯 Python 实现；embedding 函数可注入（embed_fn）。
    2) 逻辑切块：按 Markdown 小节+列表项切成语义块，保留章节标题做上下文。
    3) 小库直通：chunk 数 ≤ RAG_SMALL_KB_MAX 时直接返回全文，不检索、不耗 embedding。
    4) 混合检索：语义 top-k ∪ 关键词/子串匹配 ∪ 关键块常驻（活动/主推/优惠等）。
    5) 出错必回退：任何异常 → 打印 stderr（过滤 key）→ 返回全量知识。
    6) 索引缓存：按知识内容 SHA256 缓存，知识变了自动重建。
    """

    def __init__(self, path: str = "prompts/knowledge.md",
                 embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
                 top_k: int | None = None,
                 small_kb_max: int | None = None,
                 timeout: int | None = None,
                 embed_url: str | None = None,
                 embed_model: str | None = None,
                 api_key: str | None = None) -> None:
        self._path = _resolve_path(path)
        self._embed_fn = embed_fn
        self._top_k = top_k if top_k is not None else int(os.environ.get("RAG_TOP_K", "4"))
        self._small_kb_max = small_kb_max if small_kb_max is not None else int(os.environ.get("RAG_SMALL_KB_MAX", "15"))
        self._timeout = timeout if timeout is not None else int(os.environ.get("RAG_TIMEOUT", "30"))
        # 语义通路相对阈值:丢弃分数 < top1×ratio 的凑数 chunk;0 = 关闭回到纯 top-k(P2-7)
        self._score_ratio = float(os.environ.get("RAG_SCORE_RATIO", "0.6"))
        self._embed_url = (embed_url
                           or os.environ.get("LLM_EMBED_URL")
                           or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self._embed_model = embed_model or os.environ.get("LLM_EMBED_MODEL", "text-embedding-3-small")
        self._api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self._cache_dir = os.path.join(_ROOT, "data", "rag_cache")

        # 惰性构建的内部状态
        self._chunks: list[str] = []
        self._embeddings: list[list[float]] = []
        self._content_hash: str = ""

    def _chunk_knowledge(self, text: str) -> list[str]:
        """按 Markdown 语义切块：ATX 标题为章节，列表项每行独立 chunk，段落独立 chunk。"""
        chunks: list[str] = []
        current_section = ""
        for raw_line in text.splitlines():
            if "样例数据" in raw_line:
                continue
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("# "):
                current_section = line
                continue
            if line.startswith("- "):
                body = line[2:].strip()
                if current_section:
                    chunks.append(f"{current_section}\n- {body}")
                else:
                    chunks.append(f"\n- {body}")
                continue
            # 段落正文
            if current_section:
                chunks.append(f"{current_section}\n{line}")
            else:
                chunks.append(line)
        return chunks

    def _cache_path(self, content_hash: str) -> str:
        # 缓存键含 embedding 模型名：换模型会命中不同缓存，避免加载旧维度向量导致点积静默截断
        safe_model = re.sub(r"[^A-Za-z0-9_.-]", "_", self._embed_model)
        return os.path.join(self._cache_dir, f"{content_hash}-{safe_model}.json")

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本 embedding。优先使用注入的 embed_fn，否则走 HTTP。"""
        if self._embed_fn is not None:
            return self._embed_fn(texts)

        if not self._api_key:
            raise RuntimeError("LLM_API_KEY 未配置")

        url = f"{self._embed_url}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "wecom-group-agent/1.0",
        }
        payload = {
            "input": texts,
            "model": self._embed_model,
            "encoding_format": "float",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=body, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                resp_body = resp.read().decode("utf-8")
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise RuntimeError(f"embedding HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"embedding 网络错误: {exc.__class__.__name__}") from exc

        try:
            data = json.loads(resp_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"embedding 响应 JSON 解析失败: {exc}") from exc

        embeddings: list[list[float] | None] = [None] * len(texts)
        for item in data.get("data", []):
            idx = item.get("index")
            emb = item.get("embedding")
            if emb is not None and isinstance(idx, int) and 0 <= idx < len(texts):
                embeddings[idx] = emb

        if any(e is None for e in embeddings):
            raise RuntimeError("embedding 返回缺失部分结果")

        # 校验维度一致性
        dim = len(embeddings[0])  # type: ignore[arg-type]
        for e in embeddings:
            if len(e) != dim:  # type: ignore[arg-type]
                raise RuntimeError("embedding 维度不一致")

        return embeddings  # type: ignore[return-value]

    def _search(self, query: str, full_text: str) -> str:
        """三路召回后取并集、去重、按原文顺序输出。结果为空则回退全文。"""
        # 语义通路：top-k 内再按相对阈值过滤（P2-7）——分数 ≤0 或低于 top1×RAG_SCORE_RATIO
        # 的 chunk 视为凑数噪声丢弃。小库上不足 k 个真相关时不再硬凑；全体皆噪声（top1≈0）
        # 时语义通路自然为空，关键词/常驻/全文回退三条防线兜底，行为不劣化。
        query_emb = self._embed_batch([query])[0]
        q = _normalize(query_emb)
        scores = [sum(a * b for a, b in zip(q, emb)) for emb in self._embeddings]
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        top1 = indexed_scores[0][1] if indexed_scores else 0.0
        floor = top1 * self._score_ratio
        semantic_indices = {idx for idx, sc in indexed_scores[:self._top_k]
                            if sc > 0 and sc >= floor}

        # 关键词/子串通路
        keyword_indices: set[int] = set()
        query_tokens = _extract_tokens(query)
        query_bigrams = _char_bigrams(query)
        for i, chunk in enumerate(self._chunks):
            hit = False
            for token in query_tokens:
                if token in chunk:
                    keyword_indices.add(i)
                    hit = True
                    break
            if not hit:
                if len(query_bigrams & _char_bigrams(chunk)) >= 2:
                    keyword_indices.add(i)

        # 常驻关键块通路
        resident_indices: set[int] = set()
        for i, chunk in enumerate(self._chunks):
            for kw in _RESIDENT_KEYWORDS:
                if kw in chunk:
                    resident_indices.add(i)
                    break

        result_indices = sorted(semantic_indices | keyword_indices | resident_indices)
        if not result_indices:
            return full_text

        retrieved = "\n\n".join(self._chunks[i] for i in result_indices)
        # never-worse 兜底：检索结果与 query 几乎无词面重叠（疑似漏召被问内容）→ 回退全文，
        # 宁可多给也不漏答，守住"绝不比全量塞更差"的底线。
        if query_bigrams and not (query_bigrams & _char_bigrams(retrieved)):
            return full_text
        return retrieved

    def retrieve(self, query: str) -> str:
        try:
            with open(self._path, encoding="utf-8") as f:
                text = f.read()

            chunks = self._chunk_knowledge(text)
            if len(chunks) <= self._small_kb_max:
                return text

            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            # 内存缓存命中
            if self._content_hash == content_hash and self._chunks and self._embeddings:
                return self._search(query, text)

            # 磁盘缓存命中（仅 HTTP embedding 路径用；注入 embed_fn 时走内存、隔离且防串味）
            cache_path = self._cache_path(content_hash)
            if self._embed_fn is None and os.path.exists(cache_path):
                try:
                    with open(cache_path, encoding="utf-8") as f:
                        cache = json.load(f)
                    if cache.get("hash") == content_hash:
                        cached_chunks = cache.get("chunks", [])
                        cached_embeddings = cache.get("embeddings", [])
                        if (len(cached_chunks) == len(chunks) and
                                cached_chunks == chunks and
                                len(cached_embeddings) == len(chunks) and
                                cached_embeddings and
                                all(len(e) == len(cached_embeddings[0]) for e in cached_embeddings)):
                            self._chunks = chunks
                            self._embeddings = [_normalize(e) for e in cached_embeddings]
                            self._content_hash = content_hash
                            return self._search(query, text)
                except Exception:
                    pass  # 缓存损坏，继续重建

            # 重建索引
            raw_embeddings = self._embed_batch(chunks)
            self._chunks = chunks
            self._embeddings = [_normalize(e) for e in raw_embeddings]
            self._content_hash = content_hash

            # 写入缓存（仅 HTTP embedding 路径）
            if self._embed_fn is None:
                try:
                    os.makedirs(self._cache_dir, exist_ok=True)
                    cache_data = {
                        "hash": content_hash,
                        "model": self._embed_model,
                        "chunks": chunks,
                        "embeddings": raw_embeddings,
                    }
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache_data, f, ensure_ascii=False)
                except Exception:
                    pass  # 缓存写入失败不影响功能

            return self._search(query, text)

        except Exception as exc:
            # 绝对不回退 key
            msg = str(exc)
            if self._api_key and self._api_key in msg:
                msg = msg.replace(self._api_key, "***")
            print(f"[RagKnowledgeProvider] RAG 检索失败，已降级为全量知识: {msg}", file=sys.stderr)
            try:
                with open(self._path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return ""


class StructuredKnowledgeProvider(KnowledgeProvider):
    """结构化商品库：按关键词/属性精确匹配（非向量语义），适合查价/规格/库存。"""

    def __init__(self, path: str = "prompts/products.json", products: list[dict] | None = None) -> None:
        self._path = _resolve_path(path)
        if products is not None:
            self._products = products
        else:
            self._products = self._load_from_file()

    def _load_from_file(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            valid, problems = validate_products(data)
            for p in problems:
                print(f"[StructuredKnowledgeProvider] {p}", file=sys.stderr)
            return valid
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[StructuredKnowledgeProvider] 读取商品文件失败，已降级为空列表: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[StructuredKnowledgeProvider] 未知错误，已降级为空列表: {exc}", file=sys.stderr)
        return []

    def _is_hit(self, product: dict, query: str) -> bool:
        """判断商品是否命中 query。

        两条通路（任一命中即真）：
        1) 子串：name / product_id / category / keywords 任一作为子串出现（全角/大小写归一）；
        2) 克数：query 中的重量表达（半斤/250 克/0.5kg…）与商品 name/spec/keywords
           中的重量归一后有交集——解决"单位写法不一致召回≈0"（P2-7）。
        """
        q = _normalize_text(query)
        for field in ("name", "product_id", "category"):
            val = product.get(field, "")
            if isinstance(val, str) and val and _normalize_text(val) in q:
                return True
        for kw in (product.get("keywords") or []):
            if isinstance(kw, str) and kw and _normalize_text(kw) in q:
                return True
        query_grams = _extract_grams(query)
        if query_grams:
            product_text = " ".join(
                [str(product.get("name", "")), str(product.get("spec", ""))]
                + [k for k in (product.get("keywords") or []) if isinstance(k, str)])
            if query_grams & _extract_grams(product_text):
                return True
        return False

    def _format_product(self, product: dict) -> str:
        """将单条商品格式化为结构化文本。"""
        lines = [
            f"名称：{product.get('name', '')}",
            f"价格：¥{product.get('price', '')}",
            f"规格：{product.get('spec', '')}",
            f"库存：{product.get('stock', '')}",
        ]
        promo = product.get("promotion", "")
        if promo:
            lines.append(f"促销：{promo}")
        status = product.get("status", "")
        status_cn = _STATUS_MAP.get(status, status)
        if status_cn:
            lines.append(f"状态：{status_cn}")
        return " | ".join(lines)

    def retrieve(self, query: str) -> str:
        try:
            hits = [p for p in self._products if self._is_hit(p, query)]
            if not hits:
                return ""
            lines = ["# 命中商品(精确查询,以此为准)"]
            for p in hits:
                lines.append(self._format_product(p))
            return "\n".join(lines)
        except Exception as exc:
            print(f"[StructuredKnowledgeProvider] 检索异常，已降级: {exc}", file=sys.stderr)
            return ""


class HybridKnowledgeProvider(KnowledgeProvider):
    """混合知识：先结构化查商品，再 fallback 语义/全量查 FAQ。"""

    def __init__(self, primary: KnowledgeProvider, fallback: KnowledgeProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    def retrieve(self, query: str) -> str:
        primary_text = ""
        fallback_text = ""
        try:
            primary_text = self._primary.retrieve(query)
        except Exception as exc:
            print(f"[HybridKnowledgeProvider] primary 检索失败: {exc}", file=sys.stderr)
        try:
            fallback_text = self._fallback.retrieve(query)
        except Exception as exc:
            print(f"[HybridKnowledgeProvider] fallback 检索失败: {exc}", file=sys.stderr)

        if primary_text and fallback_text:
            return f"{primary_text}\n\n{fallback_text}"
        if primary_text:
            return primary_text
        return fallback_text
