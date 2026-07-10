"""结构化商品库与混合知识离线测试。

不依赖网络、不依赖真实文件，通过注入 products 与假 provider 验证全链路。
"""
from __future__ import annotations

import os
import sys
import tempfile

# 项目根在上一级（tests/ 的父目录）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge import (
    KnowledgeProvider,
    StaticKnowledgeProvider,
    RagKnowledgeProvider,
    StructuredKnowledgeProvider,
    HybridKnowledgeProvider,
)


_SAMPLE_PRODUCTS = [
    {
        "product_id": "SKU001",
        "name": "每日坚果礼盒 30 包",
        "category": "坚果礼盒",
        "price": 99,
        "spec": "30 包/盒，每包 25g",
        "stock": 500,
        "promotion": "老客私域专享复购价 ¥99",
        "keywords": ["每日坚果", "坚果礼盒", "礼盒", "30 包"],
        "status": "on_sale",
    },
    {
        "product_id": "SKU002",
        "name": "冻干榴莲 100g",
        "category": "冻干零食",
        "price": 45,
        "spec": "100g/袋",
        "stock": 120,
        "promotion": "",
        "keywords": ["冻干", "榴莲", "水果干"],
        "status": "on_sale",
    },
    {
        "product_id": "SKU003",
        "name": "鲜烤腰果 200g",
        "category": "坚果炒货",
        "price": 59,
        "spec": "200g/罐",
        "stock": 80,
        "promotion": "",
        "keywords": ["腰果", "鲜烤", "坚果"],
        "status": "on_sale",
    },
    {
        "product_id": "SKU004",
        "name": "碧根果 200g",
        "category": "坚果炒货",
        "price": 55,
        "spec": "200g/罐",
        "stock": 0,
        "promotion": "",
        "keywords": ["碧根果", "坚果"],
        "status": "out_of_stock",
    },
    {
        "product_id": "SKU005",
        "name": "海苔脆 100g",
        "category": "休闲零食",
        "price": 25,
        "spec": "100g/盒",
        "stock": 0,
        "promotion": "",
        "keywords": ["海苔", "海苔脆"],
        "status": "off_sale",
    },
]


class FakeFallbackProvider(KnowledgeProvider):
    """返回固定文本的 fallback provider。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def retrieve(self, query: str) -> str:
        return self._text


class CrashProvider(KnowledgeProvider):
    """故意抛出异常的 provider，用于验证兜底。"""

    def retrieve(self, query: str) -> str:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# StructuredKnowledgeProvider
# ---------------------------------------------------------------------------

def test_hit_by_name_or_keyword() -> None:
    """命中（经名称或关键词子串）：query 含商品关键词/名称，结果含该商品价格/规格，且不含无关商品。"""
    kp = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    result = kp.retrieve("每日坚果礼盒多少钱")
    assert "每日坚果礼盒 30 包" in result, "应命中名称含子串的商品"
    assert "价格：¥99" in result, "应包含精确价格"
    assert "规格：30 包/盒" in result, "应包含规格"
    assert "冻干榴莲" not in result, "不应召回无关商品"
    assert "海苔脆" not in result, "不应召回无关商品"


def test_keyword_match() -> None:
    """关键词命中：query 包含某 keyword，应召回对应商品。"""
    kp = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    result = kp.retrieve("我想买水果干")
    assert "冻干榴莲" in result, "keywords 含 '水果干' 应命中"


def test_no_match_returns_empty() -> None:
    """无命中：query 与任何商品无关，返回空字符串。"""
    kp = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    result = kp.retrieve("今天天气怎么样")
    assert result == "", "无命中时应返回空串"


def test_out_of_stock_marked() -> None:
    """售罄商品命中时，文本里明确标注状态。"""
    kp = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    result = kp.retrieve("碧根果还有吗")
    assert "碧根果 200g" in result
    assert "已售罄" in result, "售罄商品应标注已售罄"


def test_off_sale_marked() -> None:
    """下架商品命中时，文本里明确标注状态。"""
    kp = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    result = kp.retrieve("海苔脆")
    assert "海苔脆 100g" in result
    assert "已下架" in result, "下架商品应标注已下架"


def test_file_missing_returns_empty() -> None:
    """文件不存在时，retrieve 返回空串且不抛异常。"""
    kp = StructuredKnowledgeProvider(path="/nonexistent/products.json")
    result = kp.retrieve("每日坚果")
    assert result == "", "文件缺失时应返回空串"


def test_corrupted_json_returns_empty() -> None:
    """JSON 损坏时，retrieve 返回空串且不抛异常。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as f:
        f.write("this is not json")
        path = f.name
    try:
        kp = StructuredKnowledgeProvider(path=path)
        result = kp.retrieve("每日坚果")
        assert result == "", "JSON 损坏时应返回空串"
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# HybridKnowledgeProvider
# ---------------------------------------------------------------------------

def test_hybrid_both_hit() -> None:
    """primary 命中商品 + fallback 命中 FAQ 时，结果应同时包含两者。"""
    primary = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    fallback = FakeFallbackProvider("这是 FAQ 内容")
    hybrid = HybridKnowledgeProvider(primary, fallback)
    result = hybrid.retrieve("每日坚果礼盒多少钱")
    assert "命中商品(精确查询,以此为准)" in result, "应包含商品命中结果"
    assert "这是 FAQ 内容" in result, "应包含 fallback 结果"
    # primary 在前
    assert result.index("命中商品") < result.index("这是 FAQ 内容"), "primary 内容应在前"


def test_hybrid_only_fallback() -> None:
    """primary 不命中时，结果应等于 fallback 文本。"""
    primary = StructuredKnowledgeProvider(products=_SAMPLE_PRODUCTS)
    fallback = FakeFallbackProvider("FAQ 兜底")
    hybrid = HybridKnowledgeProvider(primary, fallback)
    result = hybrid.retrieve("今天天气怎么样")
    assert result == "FAQ 兜底", "primary 不命中时应返回 fallback 文本"


def test_hybrid_primary_crash() -> None:
    """primary 抛异常时，仍返回 fallback 文本，不崩溃。"""
    fallback = FakeFallbackProvider("FAQ 兜底")
    hybrid = HybridKnowledgeProvider(CrashProvider(), fallback)
    result = hybrid.retrieve("随便问")
    assert result == "FAQ 兜底", "primary 异常时应返回 fallback 文本"


# ---------------------------------------------------------------------------
# 冒烟：确认既有类未破坏
# ---------------------------------------------------------------------------

def test_smoke_static_and_rag() -> None:
    """StaticKnowledgeProvider 与 RagKnowledgeProvider 仍可 import 并实例化。"""
    s = StaticKnowledgeProvider()
    assert s is not None
    r = RagKnowledgeProvider()
    assert r is not None


# ---------------------------------------------------------------------------
# schema 校验（P2-7）
# ---------------------------------------------------------------------------

def test_validate_products_schema() -> None:
    """坏条目逐条报错并跳过,好条目保留;根不是数组/重复 ID/status 未知都有提示。"""
    from core.knowledge import validate_products
    good = {"product_id": "S1", "name": "A", "price": 10}
    valid, problems = validate_products([
        good,
        {"name": "缺ID", "price": 1},                       # 缺 product_id
        {"product_id": "S2", "name": "价格是串", "price": "9"},  # price 类型错
        "不是对象",                                          # 非 dict
        {"product_id": "S1", "name": "重复ID", "price": 2},   # 重复 ID(保留但提示)
        {"product_id": "S3", "name": "怪状态", "price": 3, "status": "sold_out"},  # 未知 status
        {"product_id": "S4", "name": "坏关键词", "price": 4, "keywords": "不是数组"},  # keywords 类型错
    ])
    ids = [p["product_id"] for p in valid]
    assert ids == ["S1", "S1", "S3", "S4"], f"好条目应保留,实际 {ids}"
    assert len(problems) == 6, f"应报 6 条问题(缺ID/价格类型/非对象/重复ID/未知status/坏keywords),实际 {len(problems)}: {problems}"
    assert valid[3]["keywords"] == [], "坏 keywords 应被清空而非整条丢弃"
    # 根不是数组
    valid2, problems2 = validate_products({"product_id": "x"})
    assert valid2 == [] and problems2, "根不是数组应整体报错"


def test_validate_products_file_real_sample() -> None:
    """仓库自带的样例 products.json 必须零问题(样例即标杆)。"""
    from core.knowledge import validate_products_file
    assert validate_products_file() == [], "样例商品文件不应有 schema 问题"


def main() -> None:
    for fn in (
        test_hit_by_name_or_keyword,
        test_keyword_match,
        test_no_match_returns_empty,
        test_out_of_stock_marked,
        test_off_sale_marked,
        test_file_missing_returns_empty,
        test_corrupted_json_returns_empty,
        test_hybrid_both_hit,
        test_hybrid_only_fallback,
        test_hybrid_primary_crash,
        test_smoke_static_and_rag,
        test_validate_products_schema,
        test_validate_products_file_real_sample,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n结构化商品库离线测试全部通过！")


if __name__ == "__main__":
    main()
