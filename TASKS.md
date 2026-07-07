# TASKS —— 多对话协作任务板

> 两个独立 Claude 对话**没有实时通道**，靠这个文件 + git 异步协作。本文件是唯一的"任务板"。

## 协作规则（务必遵守，防冲突）
1. **开工前**：`git pull --rebase origin main`。
2. **认领任务**：把下表该任务 `Owner` 填上你的标识、`Status` 改 `doing`，**先单独提交这一行改动并 push**，再动手写代码。
3. **只碰你任务列出的文件**。`main.py` 是公共文件——非必要不动；要动先 `pull --rebase`、改动尽量小。
4. **提交前**必须跑测试全绿（见下"测试"）。小步提交、每步 push。
5. 完成把 `Status` 改 `done`。冲突用 `git pull --rebase` 解决，**不要 force push**。
6. 谁也别改别人 `doing` 中任务涉及的文件。

## 测试（提交前跑）
```bash
python3 tests/test_llm_offline.py
python3 tests/test_knowledge_offline.py
python3 tests/test_rag_offline.py
# 你新增的测试也一并跑
```

## 任务

| ID | 任务 | Owner | Status | 主要文件（你的边界） |
|----|------|-------|--------|--------------------|
| T1 | 结构化转人工信号 + 人工接管状态（接管时 agent 静默） | CONV-A | doing | `core/router.py` `core/session.py` `core/llm_handler.py` `prompts/persona.md` `tests/test_takeover_offline.py` |
| T2 | SQLite 持久化会话/消息（SqliteSessionStore，与内存版同接口） | CONV-A | todo | `core/session_sqlite.py`(新) `tests/test_sqlite_offline.py` |
| T3 | REST API 层（会话/预览/接管/配置，stdlib http.server，独立入口） | CONV-A | todo | `api_server.py`(新) `tests/test_api_offline.py` |
| T4 | 微信客服适配器（官方合规 1:1；回调验签+AES、sync_msg 拉取、send_msg） | OPEN | todo | `adapters/wecom_kf.py`(新) `adapters/wecom_crypto.py`(新) `tests/test_kf_offline.py`；`main.py` 仅加 `ADAPTER=kf` 分支（改前 pull） |
| T5 | GitHub Actions CI（push 跑 tests）+ LICENSE | OPEN | todo | `.github/workflows/ci.yml`(新) `LICENSE`(新) |
| T6 | 结构化商品库 StructuredKnowledgeProvider（精确查价/规格） | OPEN | todo | `core/knowledge.py`(新增类；CONV-A 不动此文件) `tests/test_structured_kb_offline.py` |

> CONV-A（当前主对话）承接 **T1→T2→T3**（核心后端，集中改 core/，避免多人冲突）。
> 第二个对话请从 **T4 / T5 / T6** 认领——这三块与 T1-T3 文件基本不重叠。
> 参考：T4 见 `docs/技术文档/04-适配器与部署.md`；T6 见 `docs/技术文档/05-知识模块设计.md`；API 契约见 `docs/技术文档/06-系统架构与API.md`。

## 约束（全项目通用）
- 核心 `core/` 纯 Python 标准库、零第三方依赖；可选依赖（如 KF 的 pycryptodome）只在对应适配器内 guard import。
- 密钥只进 `.env`（已 gitignore），**绝不提交**；日志不打印 key。
- 所有网络/检索/对外调用都要有出错兜底，不让 agent 抛异常崩掉。
