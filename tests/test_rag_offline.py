"""RagKnowledgeProvider 离线测试。

不依赖网络和真实 API key，通过注入假 embed_fn 验证 RAG 全链路。
"""
from __future__ import annotations

import os
import sys
import tempfile

# 项目根在上一级（tests/ 的父目录）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge import RagKnowledgeProvider

# 合成知识库（30 个 chunk，超过默认小库阈值 15）
_SYNTHETIC_KB = """# 店铺
- 品牌：智销优选
- 主营：坚果、零食、果干
- 客服在线：9:00-21:00

# 当前主推 & 活动
- 【主推】每日坚果礼盒 30 包装：日常价 ¥129，老客价 ¥99
- 【活动】全店满 ¥199 减 ¥30；晒单返 ¥5 无门槛券

# 产品 & 价格
- 每日坚果礼盒 30 包：¥99（老客价）/ ¥129（日常）
- 每日坚果礼盒 7 天装：¥39
- 冻干榴莲 100g：¥45
- 冻干草莓 100g：¥42
- 鲜烤腰果 200g：¥59
- 夏威夷果 150g：¥68
- 碧根果 200g：¥55
- 巴旦木 250g：¥49
- 葡萄干 500g：¥35
- 芒果干 200g：¥38
- 山楂条 300g：¥29
- 海苔脆 100g：¥25

# 物流
- 默认顺丰；江浙沪次日达，其余 2–3 天
- 下单后 24 小时内发货，节假日顺延
- 新疆、西藏等偏远地区需额外 3–5 天

# 售后 / 退换
- 食品类未拆封 7 天无理由退换
- 已拆封如有质量问题：拍照走质量理赔
- 破损 / 漏发：拍照给客服，补发或退款
- 不支持货到付款

# 常见问答
- 保质期：坚果类 6 个月，冻干类 9 个月
- 开发票：需联系人工客服，提供抬头和税号
- 能不能再便宜：老客已是专享价，更多优惠看当期活动
- 支持货到付款吗：暂不支持，仅在线支付
- 如何查询物流：点击订单详情查看实时物流轨迹
- 过敏提示：部分坚果含花生、腰果，过敏者慎拍
"""

# 固定关键词表，用于构造确定性伪向量
_KEYWORDS = [
    "坚果", "每日坚果", "礼盒", "腰果", "榴莲", "冻干", "草莓",
    "物流", "顺丰", "发货", "快递", "包邮",
    "售后", "退换", "理赔", "退款", "质量",
    "活动", "优惠", "促销", "满减", "折扣", "特价",
    "价格", "多少钱", "怎么卖", "售价",
    "保质期", "发票", "过敏", "客服",
]


def _fake_embedder(texts: list[str]) -> list[list[float]]:
    """基于关键词表的计数向量，让含相同关键词的文本高余弦相似度。"""
    dim = len(_KEYWORDS)

    def _vec(t: str) -> list[float]:
        vec = [float(t.count(kw)) for kw in _KEYWORDS]
        norm = sum(x * x for x in vec) ** 0.5
        if not norm:
            return [0.0] * dim
        return [x / norm for x in vec]

    return [_vec(t) for t in texts]


def _zero_embedder(texts: list[str]) -> list[list[float]]:
    """返回全零向量，使语义通路失效，验证关键词/常驻通路兜底。"""
    dim = len(_KEYWORDS)
    return [[0.0] * dim for _ in texts]


def _make_provider(text: str, embed_fn=None, small_kb_max: int = 15, top_k: int = 4):
    """创建临时知识文件并返回 RagKnowledgeProvider。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as f:
        f.write(text)
        path = f.name
    return RagKnowledgeProvider(
        path=path,
        embed_fn=embed_fn,
        small_kb_max=small_kb_max,
        top_k=top_k,
        api_key="fake-key",
    )


def test_small_kb_bypass() -> None:
    """小库直通：chunk 数 ≤ small_kb_max 时直接返回全文，不调用 embed_fn。"""
    called = False

    def spy_embedder(texts: list[str]) -> list[list[float]]:
        nonlocal called
        called = True
        return _fake_embedder(texts)

    # 小文件（5 个 chunk）
    small_kb = "# 店铺\n- 品牌：A\n- 主营：B\n\n# 活动\n- 满减\n- 折扣"
    provider = _make_provider(small_kb, embed_fn=spy_embedder, small_kb_max=15)
    try:
        result = provider.retrieve("多少钱")
        assert not called, "小库场景不应调用 embed_fn"
        assert "品牌：A" in result, "应返回全量知识"
        assert "折扣" in result, "应返回全量知识"
    finally:
        os.unlink(provider._path)


def test_semantic_retrieval() -> None:
    """大库语义检索：注入假 embed_fn，query 与相关 chunk 高余弦，命中目标、排除无关项。"""
    provider = _make_provider(_SYNTHETIC_KB, embed_fn=_fake_embedder, small_kb_max=15, top_k=4)
    try:
        result = provider.retrieve("每日坚果礼盒多少钱")
        assert "每日坚果礼盒 30 包：¥99" in result, "语义检索应命中相关商品"
        assert "海苔脆 100g：¥25" not in result, "无关商品不应被召回"
    finally:
        os.unlink(provider._path)


def test_keyword_fallback() -> None:
    """语义通路失效时，关键词/子串 + bigram 仍能召回目标 chunk。"""
    provider = _make_provider(_SYNTHETIC_KB, embed_fn=_zero_embedder, small_kb_max=15, top_k=4)
    try:
        result = provider.retrieve("冻干榴莲")
        assert "冻干榴莲 100g：¥45" in result, "关键词通路应命中含'冻干榴莲'的 chunk"
    finally:
        os.unlink(provider._path)


def test_resident_chunks_always_included() -> None:
    """常驻关键块（活动/主推/优惠）无条件加入结果，即使 query 无关。"""
    provider = _make_provider(_SYNTHETIC_KB, embed_fn=_fake_embedder, small_kb_max=15, top_k=4)
    try:
        result = provider.retrieve("物流多久")
        assert "【活动】全店满 ¥199 减 ¥30" in result, "活动 chunk 应常驻召回"
        assert "【主推】每日坚果礼盒" in result, "主推 chunk 应常驻召回"
    finally:
        os.unlink(provider._path)


def test_result_sorted_by_original_index() -> None:
    """三路召回结果按 chunk 原始索引升序排列，不按相似度重排。"""
    provider = _make_provider(_SYNTHETIC_KB, embed_fn=_fake_embedder, small_kb_max=15, top_k=4)
    try:
        result = provider.retrieve("每日坚果礼盒多少钱")
        # 活动 chunk (索引 3,4) 应在产品 chunk (索引 5,6) 之前
        pos_activity = result.find("【活动】全店满 ¥199 减 ¥30")
        pos_product = result.find("每日坚果礼盒 30 包：¥99")
        assert pos_activity < pos_product, "结果应按原文顺序排列，活动在前、产品在后"
    finally:
        os.unlink(provider._path)


def test_error_fallback() -> None:
    """embed_fn 抛异常时，retrieve 回退为全量知识，stderr 打印错误（不泄漏 key）。"""
    old_stderr = sys.stderr

    class FakeStderr:
        def __init__(self) -> None:
            self.buf = ""
        def write(self, s: str) -> None:
            self.buf += s
        def flush(self) -> None:
            pass

    fake_err = FakeStderr()
    sys.stderr = fake_err

    try:
        def crash_embedder(texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding 服务爆炸")

        provider = _make_provider(_SYNTHETIC_KB, embed_fn=crash_embedder, small_kb_max=15, top_k=4)
        try:
            result = provider.retrieve("每日坚果礼盒多少钱")
            # 回退为全量知识 -> 应包含平时不会召回的无关项
            assert "海苔脆 100g：¥25" in result, "出错时应回退为全量知识"
            assert "[RagKnowledgeProvider]" in fake_err.buf, "stderr 应包含降级日志"
            assert "fake-key" not in fake_err.buf, "stderr 绝不能泄漏 api_key"
        finally:
            os.unlink(provider._path)
    finally:
        sys.stderr = old_stderr


def test_no_key_fallback() -> None:
    """不注入 embed_fn、也不配置 LLM_API_KEY 时，不崩溃，返回全量知识。"""
    old_key = os.environ.pop("LLM_API_KEY", None)
    try:
        provider = _make_provider(_SYNTHETIC_KB, embed_fn=None, small_kb_max=15, top_k=4)
        provider._api_key = ""  # 显式置空
        try:
            result = provider.retrieve("多少钱")
            assert "海苔脆 100g：¥25" in result, "无 key 时应回退为全量知识"
        finally:
            os.unlink(provider._path)
    finally:
        if old_key is not None:
            os.environ["LLM_API_KEY"] = old_key


def test_cache_rebuild_on_change() -> None:
    """内存缓存：同内容二次检索复用索引（只 embed query）；内容变更后自动重建（重新 embed chunks）。"""
    calls = {"n": 0}

    def counting_embedder(texts: list[str]) -> list[list[float]]:
        calls["n"] += 1
        return _fake_embedder(texts)

    provider = _make_provider(_SYNTHETIC_KB, embed_fn=counting_embedder, small_kb_max=15, top_k=4)
    try:
        provider.retrieve("每日坚果多少钱")   # 建索引：embed(chunks) + embed(query) = 2 次
        after_first = calls["n"]
        assert after_first == 2, f"首次检索应 embed chunks+query 共 2 次，实际 {after_first}"
        provider.retrieve("腰果多少钱")        # 内容未变 → 内存缓存命中 → 只 embed(query)
        assert calls["n"] == after_first + 1, "同内容二次检索应复用索引，只 embed query"
        # 改知识内容 → hash 变化 → 自动重建
        with open(provider._path, "a", encoding="utf-8") as f:
            f.write("\n- 新增：夏威夷果仁 300g：¥66\n")
        provider.retrieve("夏威夷果仁")        # 重建：embed(chunks) + embed(query) = 2 次
        assert calls["n"] == after_first + 3, "内容变更后应重建索引（重新 embed chunks）"
    finally:
        os.unlink(provider._path)


def main() -> None:
    for fn in (
        test_small_kb_bypass,
        test_semantic_retrieval,
        test_keyword_fallback,
        test_resident_chunks_always_included,
        test_result_sorted_by_original_index,
        test_error_fallback,
        test_no_key_fallback,
        test_cache_rebuild_on_change,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\nRAG 离线测试全部通过！")


if __name__ == "__main__":
    main()
