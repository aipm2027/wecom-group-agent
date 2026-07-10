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
| T1 | 结构化转人工信号 + 人工接管状态（接管时 agent 静默） | CONV-A | done | `core/router.py` `core/session.py` `core/llm_handler.py` `prompts/persona.md` `tests/test_takeover_offline.py` |
| T2 | SQLite 持久化会话/消息（SqliteSessionStore，与内存版同接口） | CONV-A | done | `core/session_sqlite.py`(新) `core/session.py`(写穿透钩子) `tests/test_sqlite_offline.py` |
| T3 | REST API 层（会话/预览/接管/配置，stdlib http.server，独立入口） | CONV-A | done | `api_server.py`(新) `tests/test_api_offline.py` |
| T4 | 微信客服适配器（官方合规 1:1；回调验签+AES、sync_msg 拉取、send_msg） | CONV-B | done | `adapters/wecom_kf.py`(新) `adapters/wecom_crypto.py`(新) `tests/test_kf_offline.py`；`main.py` 仅加 `ADAPTER=kf` 分支（改前 pull） |
| T5 | GitHub Actions CI（push 跑 tests）+ LICENSE | CONV-B | done | `.github/workflows/ci.yml`(新) `LICENSE`(新) |
| T6 | 结构化商品库 StructuredKnowledgeProvider（精确查价/规格） | CONV-B | done | `core/knowledge.py`(新增类；CONV-A 不动此文件) `tests/test_structured_kb_offline.py` |

> CONV-A（当前主对话）承接 **T1→T2→T3**（核心后端，集中改 core/，避免多人冲突）。
> 第二个对话请从 **T4 / T5 / T6** 认领——这三块与 T1-T3 文件基本不重叠。
> 参考：T4 见 `docs/技术文档/04-适配器与部署.md`；T6 见 `docs/技术文档/05-知识模块设计.md`；API 契约见 `docs/技术文档/06-系统架构与API.md`。

> **CONV-B 交接（T4/T5/T6 已 done 并推送）**
> - `main.py` 新增 `build_knowledge()`：`KNOWLEDGE_PROVIDER=static|rag|structured|hybrid` 选知识后端（走 `LLMHandler(knowledge=...)` 注入点，**未改** `core/llm_handler.py`）；并加 `ADAPTER=kf` 分支。RAG/结构化的“接线”已完成（最初需求）。
> - T4 加解密纯 Python 自实现 AES-256（FIPS-197 向量钉住正确性），零第三方依赖；`ADAPTER=kf` 未配 `WECOM_ENCODING_AES_KEY` 会在启动按配置报错（预期）。真机与腾讯互通待公网回调联调。
> - 本机全量 `tests/test_*.py`（9 个）全绿；CI 就绪（3.9–3.12）。
> - 留给 owner（未动免抢文件）：`.env.example` 可补 `KNOWLEDGE_PROVIDER` 与 `LLM_EMBED_URL`/`LLM_EMBED_MODEL` 说明。

> **CONV-B 全局审查（2026-07-08，59 条发现经对抗验证去伪）**
> 已修并推送（CONV-B 文件，`1c68ee0`）：wecom_kf `_sync_msg` 校验 errcode + 分页拉取、缺 `WECOM_ENCODING_AES_KEY` 友好报错、wecom_crypto 密文长度校验统一抛 `WeComCryptError`、knowledge RAG embedding 捕获 `socket.timeout`、`main.py` 加 `STORE=sqlite` 开关 + 默认 `on_escalate` 日志。
> **已由 CONV-B 代修并推送（`29c8e1b`，经用户授权跨界，全量 10 个测试全绿）**——原属 CONV-A 文件：
> - **[high] `api_server.py build_app()` adapter=None** → 已改为复用 `build_adapter()`，人工发消息现能经 adapter 下发（`sent=True`）。
> - **[high] `api_server._preview_seq` 非原子** → 改用 `itertools.count`（原子），msg_id 不再重复。
> - **[high] `SessionStore`/`SqliteSessionStore` 并发 get 竞态** → `SessionStore` 加 `Lock`；`SqliteSessionStore` 改 `RLock` 并锁住 `get/all` 的 `_live` 读写。新增 `tests/test_concurrency_offline.py` 回归。
> - **[high] `SqliteSessionStore._persist_*` 无兜底** → 包 `except sqlite3.Error` 记日志不上抛。
> - **[medium] `api_server` 请求体上限** → >1MB 返回 413；**`llm_handler` 网络降级** → 已含 `socket.timeout`；**[low] `ADMIN_TOKEN`** → 改 `hmac.compare_digest`。
> **仍留给 CONV-A（我按产品语义未擅改）**：
> - **[medium] 对不存在 chat_id 自动建空会话、不返回 404**——需给 store 加 `exists()` 且现有 `test_api_offline` 依赖自动建，改动会动到你的测试，请你定产品行为。
> - **[medium] 文档滞后**：`docs/技术文档/04/05/06` 仍称 RAG/StructuredKB/微信客服"未实现/规划/NotImplemented"，实际已落地——你在做 docs 同步，请顺带更新这几处。
> 对抗验证已排除的假阳性：should_respond 未过滤 bot 自身消息（各适配器不回传 bot 消息，不可达）、Router.send 未 try/except（唯一真适配器 send 已自兜底）、Router `_seen/_last_reply_at` 线程安全（on_message 只在单线程适配器上跑，非 api_server）。

> **CONV-B 全量文档同步（已完成，`eeb36cb`）** — `docs/需求文档/`（00–05）、`docs/技术文档/`（00–06）、README（+ `docs/测试文档/03` 一处过时行）已同步至 T1–T6 落地现状；消除过时"未实现/规划/NotImplemented"标注（ntwork/工单前端/指标表等真规划项保留）；0 死链、全量 10 个测试仍全绿。CONV-A 可正常继续改这两个目录。

> **CONV-B 深度审核修复（生产就绪加固）** — 6 视角深审 + 对抗验证后修复:微信客服适配器健壮性(回调失败回 500 让腾讯重试防丢消息、access_token 加锁+失效清缓存、只处理 origin=3 客户消息防自问自答、cursor 持久化、body 上限、XML 炸弹防护)、SQLite 开 WAL(双进程并发)、LLM 空回复兜底 + sender_name 清洗、Router 空 msg_id 去重;补测试盲区(新增 test_router/test_main + kf/rag 缓存/空回复,共 12 套全绿);persona 加合规红线/禁止承诺/拒私下交易/不贬竞品、收窄转人工、knowledge 售后边界澄清 + 与 products.json 对齐、demo 加 4 个合规对抗场景;CI 加全量 py_compile + import 冒烟。

> **CONV-B 全局整理维护（行为不变，`a984612`，CI 绿）** — 清理未用导入(wecom_crypto/wecom_kf/session);knowledge 三处路径解析抽 `_resolve_path`、常驻关键词/状态映射提模块常量;wecom_kf 内联 handler 工厂 + `_MAX_SYNC_PAGES`;router 省略冗余 `msg_type` 默认;session `Optional[..]`→`..|None`;main `on_escalate` reason 简化。**涉及 CONV-A 边界文件 `core/knowledge.py`/`core/session.py`/`core/router.py`(仅无行为整理,12 测试全绿)**,改前请 pull。

> **产品化冲刺(2026-07-10 起,双 AI 协作)** — 协作通道与逐轮互审记录移至根目录 **`DISCUSSION.md`**(append-only);任务基线 = **`docs/产品化路线图.md`**(三视角审计+对抗验证,P0/P1/P2)。本板(TASKS.md)保留为 T1~T6 历史存档。冲刺已落地:MVP 定为微信客服官方通道(PRD M 表已更)、P0 启动自检、`admin_console.py` Web 工作台、Docker 三服务、`evals/` 金标评测(检索 17/17 离线+回复 21/21 在线)、真机联调 SOP(`docs/技术文档/07`)。测试 12→13 套,CI 全绿。

## 约束（全项目通用）
- 核心 `core/` 纯 Python 标准库、零第三方依赖；可选依赖（如 KF 的 pycryptodome）只在对应适配器内 guard import。
- 密钥只进 `.env`（已 gitignore），**绝不提交**；日志不打印 key。
- 所有网络/检索/对外调用都要有出错兜底，不让 agent 抛异常崩掉。
