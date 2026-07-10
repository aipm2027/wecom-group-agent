"""内容完整性护栏(离线)。

面向"内容文件由非专业维护者(含较弱的 AI)修改"的场景:文档挡不住不读文档的编辑者,
但 `make check` 挡得住。本套测试把 prompts/ 内容区的**硬性约束**变成红灯:

- persona.md 的关键机制不可删(转人工标记契约/合规红线/防注入/输出格式);
- knowledge.md 的结构不可破(RAG 按 `# 标题`+`- 条目` 切块)、售后/物流等核心章节必须在;
- products.json 与 knowledge.md 的**价格必须一致**(两边不一致 AI 会各说各话);
- .env 永不入库;golden.json 的转人工标签必须来自 core 常量集。

改内容后跑 `make check` 即可覆盖本套。修改指南见 prompts/README.md 与根目录 CLAUDE.md。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.llm_handler import (ESCALATE_TAG, REASON_AFTER_SALES, REASON_ASK_HUMAN,
                              REASON_EMOTION, REASON_LLM_JUDGED)


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_persona_invariants() -> None:
    """persona 的机制性内容不可删:删了转人工/红线/防注入,系统行为直接残废。"""
    p = _read("prompts/persona.md")
    for must, why in (
        (ESCALATE_TAG, "转人工控制标记契约(core/llm_handler.py 靠它剥离与打标)"),
        ("转人工触发", "转人工触发条款节"),
        ("合规红线", "合规红线节(make gate 的行为基础)"),
        ("防提示注入", "安全防注入节"),
        ("输出格式", "输出格式节(禁 markdown/无前缀等)"),
        ("Markdown", "纯文本铁则(微信不渲染 markdown,真机踩过)"),
    ):
        assert must in p, f"persona.md 缺失关键内容 {must!r} —— {why}。若为有意重构,请同步更新本测试并在 docs/技术文档/09 记录"


def test_knowledge_structure() -> None:
    """knowledge.md 的格式就是功能:RAG 按 '# 标题'+'- 条目' 切块;核心章节不可缺。"""
    k = _read("prompts/knowledge.md")
    sections = re.findall(r"^# (.+)$", k, re.M)
    assert len(sections) >= 4, f"知识库应保持 ≥4 个 '# 章节'(现 {len(sections)}),别把结构压平"
    joined = " ".join(sections)
    for kw in ("售后", "物流"):
        assert kw in joined, f"知识库缺少『{kw}』章节——persona 规定政策咨询由知识库作答,删了会把咨询转人工"
    assert len(re.findall(r"^- ", k, re.M)) >= 10, "知识条目应保持 '- ' 列表格式(RAG 切块依据)"
    assert "¥" in k, "知识库应包含价格信息(带 ¥)"


def test_price_consistency_products_vs_knowledge() -> None:
    """products.json 与 knowledge.md 价格一致性:同一商品两边价格不同,AI 会各说各话。"""
    products = json.loads(_read("prompts/products.json"))
    k_lines = _read("prompts/knowledge.md").splitlines()
    problems = []
    for p in products:
        name, price = p.get("name", ""), p.get("price")
        if not name or price is None:
            continue
        # 找 knowledge 里提到该商品名的行;行内若带 ¥ 价格,商品价必须在其中
        for line in k_lines:
            if name.replace(" ", "") in line.replace(" ", ""):
                prices_on_line = re.findall(r"¥\s*(\d+)", line)
                if prices_on_line and str(int(price)) not in prices_on_line:
                    problems.append(f"{name}: products.json=¥{price},knowledge.md 该行只有 ¥{'/'.join(prices_on_line)} → {line.strip()[:60]}")
    assert not problems, "价格不一致(改价要两边一起改):\n  " + "\n  ".join(problems)


def test_env_never_tracked() -> None:
    """.env(真实密钥)绝不能进 git;.gitignore 必须保有忽略规则。"""
    out = subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True,
                         cwd=_ROOT).stdout.strip()
    assert out == "", ".env 被 git 跟踪了!立刻 git rm --cached .env 并轮换密钥"
    assert ".env" in _read(".gitignore"), ".gitignore 丢了 .env 规则"
    assert "在这里填你的密钥" in _read(".env.example"), ".env.example 的占位值被换掉了(疑似真实密钥写进模板,检查!)"


def test_golden_labels_and_ids() -> None:
    """golden.json:转人工标签必须来自 core 常量集;案例 id 唯一。"""
    cases = json.loads(_read("evals/golden.json"))
    known = {REASON_ASK_HUMAN, REASON_AFTER_SALES, REASON_EMOTION, REASON_LLM_JUDGED}
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"golden.json 存在重复 id: {[i for i in ids if ids.count(i) > 1]}"
    for c in cases:
        label = (c.get("escalation") or {}).get("label", "")
        assert not label or label in known, \
            f"案例 {c['id']} 的标签 {label!r} 不在常量集 {sorted(known)}(与 core/llm_handler.py 同源,别手写变体)"


def main() -> None:
    for fn in (
        test_persona_invariants,
        test_knowledge_structure,
        test_price_consistency_products_vs_knowledge,
        test_env_never_tracked,
        test_golden_labels_and_ids,
    ):
        fn()
        print(f"通过: {fn.__name__}")
    print("\n内容完整性护栏全部通过！")


if __name__ == "__main__":
    main()
