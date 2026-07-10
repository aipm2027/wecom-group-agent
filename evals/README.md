# evals/ 评测集维护指南

> 完整设计文档见 [docs/技术文档/10-评测与质量门禁](../docs/技术文档/10-评测与质量门禁.md),
> 这里只放"加一个案例"的最短路径。

## 加一个评测案例(5 步)

1. 打开 `golden.json`,找一个同类案例整条复制(分类见文件内 `category` 取值);
2. 改 `id`(唯一)、`query`(客户原话)、断言、`notes`(写清**为什么这么断言**);
3. 断言怎么选:
   - 知识检索类 → `"provider"` + `"retrieval": {"must_include": [...]}`;
   - 转人工类 → `"escalation": {"expect": true/false, "label": "标签"}`(标签必须是 `core/llm_handler.py` 里 `REASON_*` 常量的值,写错会被校验拦下);
   - 合规类 → `"reply"` 断言 + 依赖 `--gate` 的 LLM-as-judge;
4. 验证:`python3 evals/run_evals.py --only 你的id`(离线)→ `--online --only 你的id` **跑两遍**(LLM 非确定,双跑全绿才算数);
5. 全量回归:`make eval`,转人工/合规类改动再 `make gate`。

## 三条断言红线(血泪教训,详见 docs/测试文档/06)

1. `must_not_include` **别写会出现在否定句里的短语**——"保证明天"会误杀合规回复"没法保证明天到";
2. `must_not` 只断言系统设计**保证**的事(结构化克数匹配是精确交集可断,RAG 召回边界慎断);
3. 在线断言必须双跑,单跑通过不算数。

## 文件说明

- `golden.json` — 金标案例集(35 案,每案 notes 自带说明)
- `run_evals.py` — 运行器(`--online` 在线 / `--gate` 合规门禁 / `--only` 过滤 / `-v` 看原文)
