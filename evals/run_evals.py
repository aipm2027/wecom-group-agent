"""回复质量评测(evals)。

两种模式:
- 离线(默认,CI 跑这个):只评**知识检索层** —— 各 KnowledgeProvider 对金标 query
  的召回是否包含/排除指定内容。RAG 通路注入**确定性本地 embedding**(字符 bigram
  哈希装桶),不联网、跨平台可复现,真实走"切块→向量→三路召回→并集"全链路,
  而不是只测降级路径。
- 在线(--online,需 .env 的 LLM_API_KEY):额外把 query 喂给真实 LLMHandler,
  对最终回复做 must_include_any / must_not_include 断言(合规红线/转人工等
  只有回复层才能测的案例,离线模式下记为跳过)。

金标集在 evals/golden.json,案例结构见其中注释字段 notes。

    python3 evals/run_evals.py               # 离线(默认)
    python3 evals/run_evals.py --online      # 离线 + 在线回复层
    python3 evals/run_evals.py --only rag-   # 只跑 id 含 "rag-" 的案例
    python3 evals/run_evals.py -v            # 失败时打印召回原文/回复原文
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from core.knowledge import (HybridKnowledgeProvider, KnowledgeProvider,
                            RagKnowledgeProvider, StaticKnowledgeProvider,
                            StructuredKnowledgeProvider)

_GOLDEN_PATH = os.path.join(_HERE, "golden.json")
_EMBED_DIM = 4096  # 桶多碰撞少,bigram 重叠的排序保真度更高(纯本地,维度大也不慢)


def _local_embed(texts: list[str]) -> list[list[float]]:
    """确定性本地 embedding:字符 bigram 经 md5 哈希装进 _EMBED_DIM(4096)个桶计数。

    只用于离线评测——不衡量真实语义,但中文 FAQ 场景下 bigram 重叠与相关性
    高度相关,足以离线验证 RAG 的切块/相似度/三路召回逻辑是否工作。
    """
    out: list[list[float]] = []
    for t in texts:
        v = [0.0] * _EMBED_DIM
        for i in range(len(t) - 1):
            bg = t[i:i + 2]
            if bg.strip() != bg or not bg.strip():
                continue  # 跳过含空白的 bigram,降低标点/排版噪声
            h = int(hashlib.md5(bg.encode("utf-8")).hexdigest()[:8], 16) % _EMBED_DIM
            v[h] += 1.0
        out.append(v)
    return out


def build_provider(kind: str) -> KnowledgeProvider:
    if kind == "static":
        return StaticKnowledgeProvider()
    if kind == "structured":
        return StructuredKnowledgeProvider()
    if kind == "rag":
        # small_kb_max=1 强制走检索(默认阈值下样例知识库会直通全量,测不到检索);
        # embed_model 用独立名字隔离磁盘缓存,绝不污染真实 embedding 的 rag_cache。
        return RagKnowledgeProvider(embed_fn=_local_embed, small_kb_max=1,
                                    embed_model=f"eval-local-bigram-{_EMBED_DIM}")
    if kind == "hybrid":
        # 与 main.build_knowledge 的 hybrid 组装保持一致:结构化商品 + 全量知识兜底
        return HybridKnowledgeProvider(StructuredKnowledgeProvider(),
                                       StaticKnowledgeProvider())
    raise ValueError(f"未知 provider: {kind}")


def _check(text: str, spec: dict) -> list[str]:
    """按 must_include / must_include_any / must_not_include 断言,返回失败原因列表。"""
    problems: list[str] = []
    for s in spec.get("must_include", []):
        if s not in text:
            problems.append(f"缺少必含内容: {s!r}")
    any_list = spec.get("must_include_any", [])
    if any_list and not any(s in text for s in any_list):
        problems.append(f"必含其一均未出现: {any_list}")
    for s in spec.get("must_not_include", []):
        if s in text:
            problems.append(f"出现禁含内容: {s!r}")
    return problems


def _build_llm_reply_fn():
    """在线模式:构造真实 LLMHandler(hybrid 知识,与生产推荐形态一致)。"""
    try:
        from main import load_env_file
        load_env_file()
    except Exception:
        pass
    if not os.environ.get("LLM_API_KEY"):
        print("[eval] --online 需要 LLM_API_KEY(可放 .env),已中止", file=sys.stderr)
        sys.exit(2)

    from core.llm_handler import LLMHandler
    from core.message import Message
    from core.session import Session

    handler = LLMHandler(knowledge=build_provider("hybrid"))

    def reply_fn(case_id: str, query: str) -> str:
        session = Session(chat_id=f"eval-{case_id}")
        msg = Message(chat_id=session.chat_id, chat_type="group",
                      msg_id=f"eval-{case_id}-m1", sender_id="eval-user",
                      sender_name="评测客户", content=query)
        session.add(msg)
        return handler.reply(msg, session) or ""

    return reply_fn


def main() -> int:
    parser = argparse.ArgumentParser(description="金标评测:知识检索(离线)+ 回复质量(在线)")
    parser.add_argument("--online", action="store_true", help="额外评测真实 LLM 回复(需 LLM_API_KEY)")
    parser.add_argument("--only", default="", help="只跑 id 包含此子串的案例")
    parser.add_argument("-v", "--verbose", action="store_true", help="失败时打印召回/回复原文")
    args = parser.parse_args()

    with open(_GOLDEN_PATH, encoding="utf-8") as f:
        cases = json.load(f)
    if args.only:
        cases = [c for c in cases if args.only in c["id"]]

    providers: dict[str, KnowledgeProvider] = {}
    reply_fn = _build_llm_reply_fn() if args.online else None

    passed = failed = skipped = 0
    for case in cases:
        cid, query = case["id"], case["query"]
        problems: list[str] = []
        ran_anything = False

        # ── 检索层(离线) ──
        kind = case.get("provider")
        if kind and case.get("retrieval"):
            if kind not in providers:
                providers[kind] = build_provider(kind)
            retrieved = providers[kind].retrieve(query)
            ps = _check(retrieved, case["retrieval"])
            if ps and args.verbose:
                print(f"    [召回原文]\n{retrieved}\n", file=sys.stderr)
            problems += [f"检索({kind}): {p}" for p in ps]
            ran_anything = True

        # ── 回复层(在线) ──
        if reply_fn is not None and case.get("reply"):
            reply = reply_fn(cid, query)
            if not reply.strip():
                problems.append("回复: 为空")
            else:
                ps = _check(reply, case["reply"])
                if ps and args.verbose:
                    print(f"    [回复原文] {reply}\n", file=sys.stderr)
                problems += [f"回复: {p}" for p in ps]
            ran_anything = True

        if not ran_anything:
            skipped += 1
            print(f"  SKIP {cid} [{case['category']}](仅在线可测)")
        elif problems:
            failed += 1
            print(f"✗ FAIL {cid} [{case['category']}]")
            for p in problems:
                print(f"    {p}")
        else:
            passed += 1
            print(f"✓ PASS {cid} [{case['category']}]")

    total = passed + failed + skipped
    mode = "在线+离线" if args.online else "离线"
    print(f"\n评测({mode}): {passed} 通过 / {failed} 失败 / {skipped} 跳过(共 {total})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
