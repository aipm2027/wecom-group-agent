# 技术文档·系统架构与 API 设计

> 本文档定位：面向前后端协作与架构决策的顶层设计，涵盖系统分层、模块清单、数据模型、REST API 契约、人工接管流程、持久化策略、部署形态及分阶段路线图。如需了解底层代码实现，请参考 [后端](03-后端.md) 与 [适配器与部署](04-适配器与部署.md)；业务需求与测试验证见 [需求总览](../需求文档/00-总览.md) 与 [测试总览](../测试文档/00-总览.md)。
>
> [返回总览](00-总览.md)

---

## 1. 全局分层架构图

```
                              ┌─────────────────────┐
                              │  前端（运营后台）    │
                              │ 按 API 契约对接     │
                              │ 本项目只提供后端+API  │
                              └─────────┬───────────┘
                                        │ HTTP/REST
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API 层（已实现）                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │
│  │ 会话管理    │  │ 知识管理    │  │ 人设/配置   │  │ 人工接管工单    │   │
│  │  端点      │  │  端点      │  │  端点      │  │  端点          │   │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘   │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────────────┐  │
│  │ 指标/事件   │  │ Agent 预览  │  │        统一鉴权 (ADMIN_TOKEN)     │  │
│  │  端点      │  │  端点      │  │                                   │  │
│  └─────────────┘  └─────────────┘  └──────────────────────────────────┘  │
└────────────────────────────────────────────┬────────────────────────────┘
                                               │
┌──────────────────────────────────────────────┼────────────────────────────┐
│                           核心引擎（已落地）    │                            │
│                                              ▼                            │
│  ┌─────────────────────────────────────────────────────────────────┐       │
│  │                        Router（路由核心）                        │       │
│  │  去重 → 记入会话 → 触发判断 → 限流 → 调 handler → 回发 → 记忆   │       │
│  └──────────────┬──────────────────────────────────┬────────────────┘       │
│                 │                                  │                        │
│                 ▼ reply()                          ▼ session               │
│  ┌────────────────────────────┐      ┌──────────────────────────┐       │
│  │  Handler（可插拔）          │      │  SessionStore（内存/SQLite）│       │
│  │  ┌──────────────────────┐  │      │  按 chat_id 隔离          │       │
│  │  │ EchoHandler（占位）   │  │      │  内存: deque(maxlen=20)  │       │
│  │  │ LLMHandler（真大脑）  │  │      │  SQLite: 写穿透+重启恢复  │       │
│  │  └──────────────────────┘  │      └──────────────────────────┘       │
│  │  ┌──────────────────────┐  │                                         │
│  │  │ KnowledgeProvider     │  │                                         │
│  │  │  StaticKnowledgeProvider│  ← 读 prompts/knowledge.md 全量返回     │
│  │  │  RagKnowledgeProvider   │  ← 已实现：语义检索 + 混合召回 + 缓存   │
│  │  │  StructuredKnowledgeProvider│ ← 已实现：结构化商品库精确查询        │
│  │  └──────────────────────┘  │                                         │
│  └────────────────────────────┘                                         │
│  【可插拔】换 Adapter / 换 Handler / 换 KnowledgeProvider 均不改 Router  │
└───────────────────────────────────────────────────────────────────────────┘
                                               │
                                               │ start(on_message) / send()
                                               ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                           传输适配器（可插拔）                                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────────────┐ │
│  │ MockCliAdapter   │  │ NtworkWecomAdapter│  │ WecomKfAdapter（微信客服） │ │
│  │  • 交互/脚本输入  │  │  • 企微群 hook   │  │  • 官方合规 1:1            │ │
│  │  • Mac 本地验证   │  │  • 仅 Windows    │  │  • 仅单聊，不进群          │ │
│  │  【已实现】       │  │  【stub】        │  │  【已实现】                │ │
│  └──────────────────┘  └──────────────────┘  └────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘

                               ┌────────────────────┐
                               │ 旁路模块（部分已实现）│
┌──────────────────────────────┴────────────────────┴──────────────────────────┐
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │ 人工接管工作台  │  │ 知识管理后台   │  │ 配置中心       │  │ 指标/事件管道  │ │
│  │  • 工单队列     │  │  • 知识上传    │  │  • 人设/提示词  │  │  • 埋点采集    │ │
│  │  • 人工回复     │  │  • 商品结构化  │  │  • 系统参数    │  │  • 实时看板    │ │
│  │  • 接管/释放    │  │  • 索引管理    │  │  • 开关/阈值   │  │  • 离线分析    │ │
│  │  【已实现】     │  │  【规划】      │  │  【规划】      │  │  【规划】      │ │
│  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

> Router 流水线完整顺序：去重 → 记入会话 → **接管静默检查**（`human_controlled=True` 时 agent 静默）→ 触发判断 → 限流 → 调 handler → 回发 → 记忆 → **on_escalate 回调**（若 `needs_human=True`）。详见 [后端](03-后端.md)。

### 关键架构决策

| 决策 | 说明 |
|------|------|
| **传输无关三层** | 传输适配器 ↔ Router ↔ Handler/Session，任何一层替换不影响其余层。 |
| **零依赖核心** | `core/` 纯 Python 标准库，LLM 调用用 `urllib`，无 `fastapi`/`sqlalchemy`/`redis` 等强依赖。SQLite / HTTP 层作为可选扩展，不污染核心。 |
| **可插拔知识模块** | `KnowledgeProvider` 抽象基类（`retrieve(query)`），当前已实现四实现：`StaticKnowledgeProvider`（全量提示词直塞）、`RagKnowledgeProvider`（语义 embedding + 混合检索 + 缓存）、`StructuredKnowledgeProvider`（精确商品查询）、`HybridKnowledgeProvider`（结构化 + RAG 组合兜底）。`LLMHandler` 通过 `build_knowledge()` 注入，一行不改。详见 [core/knowledge.py](../../core/knowledge.py)。 |
| **前端仅按 API 契约对接** | 运营后台前端由调用方自行开发，本项目只提供 REST API 契约与鉴权说明。**内置参考实现**：[admin_console.py](../../admin_console.py) 零依赖单文件 Web 工作台（同源反代 `/api/*`，`ADMIN_TOKEN` 服务端注入不下发前端，`CONSOLE_PASSWORD` 登录，收件箱/接管/人工回复/试聊），既可直接生产使用，也可作为自研前端的契约用法范例。 |

---

## 2. 模块清单表（现状 vs 规划）

| 模块 | 文件/目录 | 现状 | 规划 | 备注 |
|------|-----------|------|------|------|
| 消息模型 | `core/message.py` | ✅ 已实现 | — | `Message` 统一结构，`BOT_SENDER_ID="__bot__"` |
| Adapter 抽象 | `core/adapter.py` | ✅ 已实现 | — | `start`/`send` 契约 |
| Mock 适配器 | `adapters/mock_cli.py` | ✅ 已实现 | — | 交互/脚本两种模式，Mac 本地验证 |
| Ntwork 适配器 | `adapters/ntwork_wecom.py` | 🟡 Stub | 待 Windows 实机 | 文件顶部注释已写实现框架，当前抛 `NotImplementedError` |
| 微信客服适配器 | `adapters/wecom_kf.py` + `adapters/wecom_crypto.py` | ✅ 已实现 | 公网回调联调 | 官方合规 1:1，需公网回调+AES 解密；加密走纯 Python 自实现（AES-256-CBC + PKCS7 + SHA1），等价 WXBizMsgCrypt |
| Router | `core/router.py` | ✅ 已实现 | — | 去重/触发/限流/回发/记忆流水线；已支持 `human_controlled` 静默逻辑 |
| Handler 抽象 | `core/handler.py` | ✅ 已实现 | — | `Handler` 抽象 + `EchoHandler` 占位复读机（`LLMHandler` 见下行） |
| LLMHandler | `core/llm_handler.py` | ✅ 已实现 | 持续优化 | 纯 `urllib` 调 OpenAI 兼容接口；system prompt 三级组装；识别 `[[转人工]]` 控制标记并剥离；支持 `transport` 注入测试 |
| KnowledgeProvider | `core/knowledge.py` | ✅ 已实现 | 持续扩展 | `StaticKnowledgeProvider` 默认；`RagKnowledgeProvider` 已实现（语义 embedding + 混合检索 + 缓存）；`StructuredKnowledgeProvider` 已实现（精确商品查询）；`HybridKnowledgeProvider` 已实现（组合兜底） |
| Session/SessionStore | `core/session.py` | ✅ 已实现 | — | `deque(maxlen=20)`；已扩展 `human_controlled`/`needs_human`/`escalation_reason` 字段及 `take_over()`/`release()`/`mark_needs_human()` |
| 持久化(SQLite) | `core/session_sqlite.py` | ✅ 已实现 | — | `SqliteSessionStore` 与内存 `SessionStore` 同接口；写穿透透明持久化；`UNIQUE(chat_id,msg_id)` 去重；重启恢复最近 N 条历史与接管/转人工状态 |
| REST API 层 | `api_server.py` | ✅ 已实现 | — | `ApiApp` 纯逻辑（无 socket，可离线单测）+ `stdlib http.server` 薄壳；ADMIN_TOKEN 鉴权；开发模式未配置时告警放行 |
| 人工接管流程 | `core/session.py`, `core/router.py`, `core/llm_handler.py`, `prompts/persona.md` | ✅ 已实现 | — | 结构化 escalation 信号（`[[转人工]]` 标记→`mark_needs_human`→`on_escalate` 回调）；`human_controlled` 静默；API `takeover`/`release` |
| 指标/事件管道 | — | ❌ 无 | 规划 P2 | 埋点采集、实时看板、离线分析 |
| 结构化商品库 | `core/knowledge.py` (`StructuredKnowledgeProvider`) | ✅ 已实现 | 扩展至真实 SKU | 精确查价/规格/库存/促销/状态，按 name/product_id/category/keywords 子串匹配；样例数据 `prompts/products.json`（约10条坚果零食）；无命中返回空串；文件缺失/JSON 损坏兜底为空不崩 |
| 配置中心 | — | ❌ 无 | 规划 P2 | 动态调参，无需重启进程 |
| 知识管理后台 | — | ❌ 无 | 规划 P2 | 知识上传、切块、索引重建 |

---

## 3. 数据模型

> 以下涵盖**已落地的运行时内存结构**（`core/` 中的 `Message`、`Session`）与**规划中的持久化实体表**（P1 及以后）。用 `🏷` 标注现状，用 `📋` 标注规划。

### 3.1 Message（消息）—— 🏷 已落地

| 字段 | 类型 | 说明 |
|------|------|------|
| `chat_id` | `str` | 群/会话唯一标识，由适配器按传输层原字段映射（如企微 `conversation_id`） |
| `chat_type` | `"group" \| "single"` | 区分群聊与单聊，决定路由触发策略 |
| `msg_id` | `str` | 消息唯一 id，用于 `Router` 去重；mock 模式下由适配器自增生成 |
| `sender_id` | `str` | 发送者唯一标识；`BOT_SENDER_ID = "__bot__"` 表示机器人自身 |
| `sender_name` | `str` | 发送者昵称，LLMHandler 在历史记录中拼接为 `"昵称：内容"` 送入 user role |
| `content` | `str` | 文本内容；若为空则在 LLMHandler 组装时会被跳过 |
| `msg_type` | `str` | 当前仅支持 `"text"`，非 text 消息触发策略直接拒绝 |
| `is_at_bot` | `bool` | 群聊中是否 @ 了机器人；`MockCliAdapter` 将内容中的 `@bot` 解析为此标记 |
| `timestamp` | `int` | 毫秒时间戳，当前主要留存，未参与限流逻辑 |
| `raw` | `dict` | 原始传输层 payload，供调试或未来扩展字段时使用 |

源码：`core/message.py`（[查看](../../core/message.py)）。

### 3.2 Session（会话）—— 🏷 已落地

| 字段 | 类型 | 说明 |
|------|------|------|
| `chat_id` | `str` | 会话唯一标识 |
| `history` | `deque[Message]` | 最近 `MAX_CONTEXT=20` 条消息，自动淘汰旧记录 |
| `state` | `dict` | 预留字典，未来可存放会话阶段（如"已询价→待下单"）、用户标签等 |
| `human_controlled` | `bool` | **现状新增。** 是否处于人工接管态；为 `True` 时 agent 静默不回 |
| `needs_human` | `bool` | **现状新增。** 是否已触发转人工（`[[转人工]]` 标记）；用于人工工作台收件箱 |
| `escalation_reason` | `str` | **现状新增。** 转人工原因摘要，由 LLM 在 `[[转人工]]` 时附带 |

源码：`core/session.py`（[查看](../../core/session.py)）。

### 3.3 客户（Customer）—— 📋 规划

| 字段 | 类型 | 说明 |
|------|------|------|
| `customer_id` | `str` | 全局唯一客户标识（如企微 `sender_id`） |
| `chat_id` | `str` | 最近关联的会话 id |
| `nickname` | `str` | 昵称（冗余缓存） |
| `tags` | `list[str]` | 客户标签（如"高意向"、"已下单"） |
| `first_seen_at` | `datetime` | 首次出现时间 |
| `last_active_at` | `datetime` | 最后活跃时间 |
| `metadata` | `dict` | 扩展字段（如地域、客单价等） |

> 规划理由：持久化后，从会话历史聚合客户画像，支持运营后台做客户分层。

### 3.4 消息记录（MessageRecord）—— 🏷 现状：SQLite 已持久化

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `int` | 自增主键（文档概念名 `record_id`，实际 schema 中为 `id`） |
| `chat_id` | `str` | 所属会话 |
| `msg_id` | `str` | 去重标识（原始传输层 id） |
| `sender_id` | `str` | 发送者 |
| `sender_name` | `str` | 发送者昵称 |
| `content` | `text` | 文本内容 |
| `msg_type` | `str` | `"text"` / `"image"` / `"voice"` 等 |
| `is_at_bot` | `bool` | 群聊是否 @ 机器人 |
| `timestamp` | `int` | 毫秒时间戳 |
| `created_at` | `real` | 入库时间（Unix 时间戳，schema 中类型为 `REAL`） |
| `is_bot` | `bool` | **运行时/ API 中计算得出：** `sender_id == "__bot__"`；schema 中不单独存列 |

> **现状说明**：`core/session_sqlite.py` 中 `messages` 表已按上述结构落地，并设 `UNIQUE(chat_id, msg_id)` 去重约束。`SqliteSessionStore` 通过 `Session` 写穿透钩子（`on_message`/`on_flags`）透明持久化，Router 与 Handler 零改动。`is_bot` 不在表中存列，由 API 序列化时根据 `sender_id` 计算。

源码：`core/session_sqlite.py`（[查看](../../core/session_sqlite.py)）。

### 3.5 商品（Product）—— 🏷 现状：运行时已实现（`StructuredKnowledgeProvider`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `product_id` | `str` | 商品唯一标识（如 SKU 编码） |
| `name` | `str` | 商品名称 |
| `category` | `str` | 类目 |
| `price` | `decimal` | 售价 |
| `spec` | `str` | 规格（如"30 包/盒"） |
| `stock` | `int` | 库存（实时或快照） |
| `promotion` | `str` | 当前促销描述（如"满 199 减 30"） |
| `keywords` | `list[str]` | 索引关键词（用于结构化查询） |
| `status` | `str` | `"on_sale"` / `"off_sale"` / `"out_of_stock"` |
| `updated_at` | `datetime` | 最后更新时间 |

> **现状说明**：`core/knowledge.py` 中 `StructuredKnowledgeProvider` 已按上述字段做运行时精确/子串匹配，样例数据见 `prompts/products.json`（约10条坚果零食）。无命中返回空串；文件缺失/JSON 损坏兜底为空不崩。未来如需持久化表 + REST 管理端点，可迁移至独立 `Product` 表（规划 P2）。

源码：`core/knowledge.py`（[查看](../../core/knowledge.py)）。

### 3.6 FAQ（知识条目）—— 🏷 现状：运行时已实现（`RagKnowledgeProvider`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `faq_id` | `int` | 自增主键 |
| `question` | `text` | 标准问题 |
| `answer` | `text` | 标准答案 |
| `category` | `str` | 分类（如"物流"、"售后"、"活动"） |
| `embedding` | `blob` | 向量（RAG 规模化后使用） |
| `status` | `str` | `"active"` / `"archived"` |
| `updated_at` | `datetime` | 最后更新时间 |

> **现状说明**：当前 `RagKnowledgeProvider` 从 `prompts/knowledge.md` 按 Markdown 语义切块，通过 HTTP embedding 接口做语义向量检索 + 关键词/子串/常驻块混合召回。无需预建 FAQ 表即可运行。未来如需 FAQ 增删改查、增量更新、独立表持久化，可迁移至独立 `FAQ` 表（规划 P2）。

源码：`core/knowledge.py`（[查看](../../core/knowledge.py)）。

### 3.7 接管工单（EscalationTicket）—— 📋 规划（现状用 session 字段替代）

| 字段 | 类型 | 说明 |
|------|------|------|
| `ticket_id` | `str` | 全局唯一工单号（如 `ESC-20260708-001`） |
| `chat_id` | `str` | 关联会话 |
| `customer_id` | `str` | 客户标识 |
| `reason` | `str` | 升级原因（如 `"complex_query"`、`"complaint"`、`"inventory_check"`） |
| `status` | `str` | `"open"` / `"assigned"` / `"resolved"` / `"cancelled"` |
| `agent_id` | `str` | 接管人工客服 id（nullable） |
| `escalation_signal` | `dict` | 结构化信号（agent 触发时附带，见第 5 节） |
| `summary` | `text` | 自动摘要（agent 升级时的上下文） |
| `created_at` | `datetime` | 创建时间 |
| `resolved_at` | `datetime` | 结案时间（nullable） |

> **现状说明**：当前未建独立工单表，转人工与接管状态直接保存在 `Session` 的 `needs_human`/`human_controlled`/`escalation_reason` 字段中，通过 REST API 暴露给前端。未来如需要工单历史审计、多客服分配、SLA 统计，可迁移至独立 `EscalationTicket` 表。

### 3.8 配置（Config）—— 📋 规划

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | `str` | 主键（如 `"persona_md"`、`"dedup_max"`、`"rate_limit_sec"`） |
| `value` | `text` | 配置值（JSON 或纯文本） |
| `updated_at` | `datetime` | 最后修改时间 |
| `updated_by` | `str` | 修改人 |

### 3.9 指标事件（MetricEvent）—— 📋 规划

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | `int` | 自增主键 |
| `event_type` | `str` | `"reply"`、`"escalation"`、`"rate_limit_hit"`、`"llm_error"` 等 |
| `chat_id` | `str` | 关联会话（nullable） |
| `customer_id` | `str` | 关联客户（nullable） |
| `payload` | `dict` | 事件详情（JSON） |
| `timestamp` | `datetime` | 事件时间 |

---

## 4. REST API 契约（给前端团队）

> ✅ **以下 API 端点已在 `api_server.py` 实现。** 当前基于 `stdlib http.server` 线程模型，无 `fastapi`/`aiohttp` 等额外依赖；生产环境如需更高并发，可在外层加 Nginx 或替换为 ASGI 框架，但 `ApiApp` 纯逻辑层零改动。
>
> 源码：`api_server.py`（[查看](../../api_server.py)）。

### 4.1 统一鉴权说明

所有端点（除健康检查外）在请求头中携带 `Authorization: Bearer <ADMIN_TOKEN>`，或 `X-Admin-Token: <ADMIN_TOKEN>`。
- `ADMIN_TOKEN` 从环境变量读取（`.env` 中 `ADMIN_TOKEN=...`），进程启动时加载，重启生效。
- **未配置 `ADMIN_TOKEN` 时**：开发模式自动放行并打印告警日志（`WARNING: ADMIN_TOKEN not set, API is open to everyone`），方便本地调试。
- Token 匹配使用 `hmac.compare_digest` 做恒定时间比较，防止时序攻击。
- Token 不匹配时返回 `401 Unauthorized`。
- 请求体上限 1MB（超返 `413 Payload Too Large`），防超大 Content-Length 耗内存。
- 进程收到 `SIGTERM`/`SIGINT` 时调用 `server.shutdown()` 优雅停止（先停接新请求再退出），适配容器/systemd。
- 规划后续可扩展为 RBAC（区分运营/管理员/只读）。

### 4.2 会话管理

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/health` | 健康检查 | **免鉴权。** 返回 `{"ok":True}` |
| `GET` | `/api/conversations` | 会话列表 | 返回会话摘要（`chat_id`、`human_controlled`、`needs_human`、`escalation_reason`、`message_count`、`last_message` 等）；支持 `?limit=N` 限制返回条数（分页钩子） |
| `GET` | `/api/conversations/{id}/messages` | 会话消息流 | 按时间正序返回消息列表（含 `is_bot`、`is_human_agent` 标记） |
| `POST` | `/api/conversations/{id}/messages` | 人工发消息 | `{"text":"...","sender_name":"人工客服"}`；通过已装配的 adapter 发送，记入 session；返回 `{"ok":True,"sent":true/false}` |
| `POST` | `/api/conversations/{id}/takeover` | 人工接管 | 设置 `human_controlled=True`，agent 静默 |
| `POST` | `/api/conversations/{id}/release` | 释放回 agent | 设置 `human_controlled=False`，agent 恢复自动回复 |

> 注：对不存在的 `chat_id` 会自动建空会话（不返回 404）——产品语义待定。

### 4.3 人工工作台队列

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/queue` | 人工工作台收件箱 | 返回 `needs_human=True` 且 `human_controlled=False`（未接管）的会话列表（内存版按插入顺序、SQLite 版按 `updated_at` 倒序） |

> 说明：`/api/queue` 是运营后台"待处理"视图的核心接口，客服先从此列表取会话，再调用 `takeover` 接管。

### 4.4 知识管理—— 规划

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/knowledge` | 查看当前知识 | 返回 `prompts/knowledge.md` 文本（Static 阶段） |
| `PUT` | `/api/knowledge` | 全量更新知识 | `{"content":"..."}`；覆盖 `knowledge.md`；触发 LLMHandler 重新加载（若运行中） |

> 当前未实现，保留规划（P2）。知识模块目前通过环境变量 `KNOWLEDGE_PROVIDER` 与文件系统驱动。FAQ 增删改查、语义检索端点亦保留规划。

### 4.5 商品库（结构化）—— 规划

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/products` | 商品列表 | `?category=...&status=...&q=关键词`；支持关键词过滤 |
| `GET` | `/api/products/{product_id}` | 商品详情 | 返回完整商品信息 |
| `POST` | `/api/products` | 新增商品 | 完整商品字段 |
| `PUT` | `/api/products/{product_id}` | 更新商品 | 部分更新支持 |
| `DELETE` | `/api/products/{product_id}` | 删除商品 | 软删除 |

> 当前未实现 REST 端点，规划 P2。`StructuredKnowledgeProvider`（`core/knowledge.py`）已实现运行时精确查询能力，样例数据见 `prompts/products.json`。

### 4.6 人设与配置

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/config` | 查看当前配置 | 返回 key-value 列表（**不含密钥**，如 `LLM_API_KEY` 等敏感字段被过滤） |

> 动态调参、人设热重载、配置中心保留规划（P2）。当前人设/知识通过文件系统 + 环境变量驱动，重启生效。

### 4.7 人工接管

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/queue` | 待处理队列 | 同 4.3，人工工作台收件箱 |
| `POST` | `/api/conversations/{id}/takeover` | 人工接管 | 同 4.2 |
| `POST` | `/api/conversations/{id}/release` | 释放回 agent | 同 4.2 |

> 独立工单表（`EscalationTicket`）及统计端点保留规划（P2）。

### 4.8 指标与看板—— 规划

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/metrics` | 实时指标 | 返回基础运行时计数：当前会话总数、需人工会话数、人工接管中会话数、消息总条数（来自内存/SQLite 中的 `sessions` 快照）。完整看板保留规划（P2） |
| `GET` | `/api/metrics/dashboard` | 实时看板 | 今日消息量、回复量、触发率、LLM 错误率、人工接管率等（规划 P2） |
| `GET` | `/api/metrics/events` | 事件查询 | `?type=reply&chat_id=...&from=...&to=...`；分页（规划 P2） |
| `GET` | `/api/metrics/llm_stats` | LLM 调用统计 | 调用次数、平均延迟、降级次数、token 消耗（估算）（规划 P2） |
| `POST` | `/api/metrics/events` | 手动上报（测试用） | 内部使用（规划 P2） |

### 4.9 Agent 预览与调试

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `POST` | `/api/agent/preview` | 单轮对话预览 | `{"text":"...","chat_id":"...","sender_name":"..."}`；调用 LLMHandler 生成回复，**不记入真实会话**；返回 `{"reply":"...","needs_human":true/false}` |

> 知识检索预览、商品匹配预览端点保留规划（P2）。

### 4.10 响应格式

当前 API 返回扁平 JSON，HTTP 状态码即语义（无外层 `code`/`message`/`data` 包装，无分页包装）。示例：

成功：
```json
{"ok": true}
```

列表：
```json
{"conversations": [{"chat_id": "...", "human_controlled": false, ...}]}
```

错误：
```json
{"error": "unauthorized"}
```
```json
{"error": "not found", "path": "/api/unknown"}
```
```json
{"error": "internal error", "detail": "..."}
```

统一返回 HTTP 状态码：
- `200` 成功
- `400` 请求参数错误（如 JSON 解析失败、必填字段缺失）
- `401` 鉴权失败（Token 不匹配）
- `404` 端点不存在
- `413` 请求体超过 1MB
- `500` 内部异常

> 注：当前未实现 `code`/`message`/`data` 外层包装及分页包装。前端如需统一封装，可自行在调用层适配；若后续引入，将向后兼容（仅在现有扁平结构外添加包装层，不改变原有字段）。

---

## 5. 人工接管流程

> ✅ **人工接管后端已实现。** 当前通过 `Session` 字段 + `LLMHandler` 控制标记 + `Router` 回调 + REST API 完成结构化转人工与接管释放，无需独立工单表即可运行。前端运营后台按第 4 节 API 契约对接即可。
>
> 源码：`core/session.py`（[查看](../../core/session.py)）、`core/router.py`（[查看](../../core/router.py)）、`core/llm_handler.py`（[查看](../../core/llm_handler.py)）、`prompts/persona.md`（[查看](../../prompts/persona.md)）。

### 5.1 现状：已实现流程

```
客户提问（复杂/投诉/需查库存）
         │
         ▼
  ┌──────────────┐
  │  LLMHandler  │  识别需人工介入
  │  剥离控制标记 │  检测到回复中包含 [[转人工]]
  │  ESCALATE_TAG│  从客户可见内容中剥离，仅保留结构化意图
  └──────┬───────┘
         │ 调用 session.mark_needs_human(reason)
         │ 设置 needs_human=True, escalation_reason=reason
         ▼
  ┌──────────────┐
  │   Router     │  on_escalate 回调（若已注册）
  │  触发回调    │  发送安抚话术（"已为您转接人工，请稍候"）
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │ 人工工作台   │  运营后台调用 GET /api/queue
  │  查看收件箱  │  获取 needs_human=True 且未接管的会话列表
  │  点击接管    │  调用 POST /api/conversations/{id}/takeover
  └──────┬───────┘
         │ 设置 human_controlled=True
         ▼
  ┌─────────────────────────────────┐
  │ 接管态行为（Router 已实现）      │
  │  • Agent 静默，不再自动回复     │
  │    → session.human_controlled 时 │
  │      Router 只记消息，不调 handler│
  │  • 人工客服通过后台发消息       │
  │    → POST /api/conversations/{id}/messages
  └──────┬────────────────────────────┘
         │ 人工回复通过 adapter.send() 发送
         ▼
  ┌──────────────┐
  │ 客服结束会话 │  调用 POST /api/conversations/{id}/release
  │  释放回 agent│  设置 human_controlled=False
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  Agent 恢复  │  回到自动回复模式
  │  自动回复    │  可附带上下文摘要给 LLM
  └──────────────┘
```

### 5.2 关键设计决策（现状）

| 决策 | 说明 |
|------|------|
| **结构化 escalation 信号** | **已实现**：不是让客户看到"转人工"纯话术，而是 LLM 在回复中插入 `[[转人工]]` 控制标记（`ESCALATE_TAG`）。`LLMHandler` 解析输出，**剥离该标记**（客户不可见），并调用 `session.mark_needs_human(reason)` 设置结构化状态。`Router` 检测到 `needs_human` 时触发 `on_escalate` 回调（`main.py` 默认实现仅打印 stderr；生产可换成发送安抚话术/写工单/通知工作台）。`prompts/persona.md` 中已追加"转人工时追加控制标记"的指令。 |
| **静默模式** | **已实现**：`session.human_controlled = True` 时，`Router` 在 `on_message` 中只将消息记入 `Session.history`，**不调 `handler.reply()`**，agent 完全静默。人工客服通过 API 发送的消息走正常 `adapter.send()` 通道。 |
| **释放机制** | **已实现**：人工客服通过 `POST /api/conversations/{id}/release` 主动释放；释放后 `human_controlled=False`，agent 恢复自动回复。当前未实现超时自动释放（可后续扩展）。 |
| **建议模式** | 规划（P2）：支持 agent 在后台继续推理给人工客服提供回复建议，但由人工决定是否发送。默认当前为完全静默模式。 |
| **上下文移交** | 规划（P2）：人工接管时自动生成最近 `MAX_CONTEXT` 条消息的摘要写入工单。当前人工客服需通过 `GET /api/conversations/{id}` 查看历史。 |

---

## 6. 持久化（现状：内存 + SQLite 已提供）

### 6.1 内存 SessionStore

当前 [`core/session.py`](../../core/session.py) 使用纯内存 `dict` 保存 `Session`：

```python
class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
    def get(self, chat_id: str) -> Session:
        s = self._sessions.get(chat_id)
        if s is None:
            s = Session(chat_id=chat_id)
            self._sessions[chat_id] = s
        return s
```

- 优点：零依赖、零延迟、实现极简。
- 缺点：进程重启后全部会话历史丢失；无法跨进程共享；无法做离线审计和数据分析。

### 6.2 SQLite 持久化（现状已提供）

`core/session_sqlite.py` 引入 `sqlite3`（Python 标准库自带，仍保持零额外依赖）实现持久化。

#### 设计要点

| 要点 | 方案 |
|------|------|
| **存储范围** | `MessageRecord`（全量消息）+ `Session` 元数据（`human_controlled`、`needs_human`、`escalation_reason`、最后活跃时间）。不存 LLM 原始响应（仅存最终发送给用户的文本）。 |
| **接口兼容** | `SqliteSessionStore` 保持与内存 `SessionStore` 同接口：`get(chat_id) -> Session`、`all() -> list[Session]`。Router 与 Handler 零改动。 |
| **写穿透** | `Session` 注册 `on_message`/`on_flags` 钩子，`SqliteSessionStore` 在钩子中实时写入 `messages`/`sessions` 表，无需显式 `save()` 调用。 |
| **MAX_CONTEXT 语义保留** | 查询时按 `ORDER BY id DESC LIMIT ?`（`?`=MAX_CONTEXT；自增主键 `id` 近似插入顺序）取最近消息，再按时间正序送入 LLMHandler，与内存 `deque(maxlen=20)` 语义一致。 |
| **去重** | `messages` 表设 `UNIQUE(chat_id, msg_id)`，重复消息自动丢弃。 |
| **重启恢复** | 进程重启后，`SqliteSessionStore.get(chat_id)` 从 SQLite 读取最近 N 条历史重构 `Session`；同时恢复 `human_controlled`/`needs_human`/`escalation_reason` 状态，确保接管/转人工不丢失。 |
| **BOT_SENDER_ID 消息也需持久化** | 否则进程重启后，机器人自己的回复丢失，多轮上下文断裂。 |
| **读写分离** | 高频写入（每条消息）直接写 SQLite；读取（运营后台查询、LLM 上下文组装）走同一个库。SQLite 在单进程/低并发场景足够；若以后需高并发，可换 PostgreSQL/Redis，但当前不需要。 |
| **迁移方式** | `main.py` 中通过环境变量 `STORE=memory`（默认）/ `sqlite` 切换；切换后内存版数据不做迁移（数据量小，可接受冷启动），运营后台历史数据从 SQLite 重新查询。 |
| **索引** | 实际 schema：`messages(chat_id, id)` 联合索引 + `UNIQUE(chat_id, msg_id)` 复合唯一约束（去重）。`sender_id` / `timestamp` 索引为规划（当前未建）。 |

源码：`core/session_sqlite.py`（[查看](../../core/session_sqlite.py)）。

### 6.3 扩展点：以后换 PostgreSQL/Redis

`SessionStore` 的接口设计保持了最小契约：`get(chat_id) -> Session`、`all() -> list[Session]`。以后需要横向扩展时，只需替换 `SessionStore` 实现，Router 和 Handler 均无需改动。

---

## 7. 部署形态

详见 [适配器与部署](04-适配器与部署.md)，本节提炼架构视角的部署要点。

### 7.1 单进程 vs 拆分

| 形态 | 现状 | 规划 | 适用场景 |
|------|------|------|----------|
| **单进程 all-in-one** | ✅ 当前 | 保留 | 开发验证、小规模测试、个人 Windows 机器跑企微 hook |
| **核心 + 传输分离** | — | 规划 | 将 Agent 核心（Router+Handler+SessionStore）部署在云服务器，通过 WebSocket/HTTP 与 Windows 机器上的 hook 进程通信；或核心直接对接微信客服回调（无需 Windows） |
| **核心 + 前端分离** | — | 规划 | 核心部署在云服务器/容器，前端（运营后台）独立部署，通过 REST API 调用 |

### 7.2 关键约束

- **Agent 核心可部署在任意机器**：纯 Python 标准库，Mac/Linux/Windows/云服务器均可。
- **Ntwork 企微 hook 必须贴 Windows 客户端**：`ntwork` 注入的是 Windows 版 `WXWork.exe`，因此 hook 进程必须与企微客户端同机运行。Agent 核心与 hook 进程可分离，但 hook 进程所在机器必须是 Windows。
- **微信客服适配器（已实现）**无操作系统限制：只需公网回调地址，Agent 核心可部署在任意有公网 IP 的服务器上。`WecomKfAdapter` 代码已就绪，真机与腾讯服务器互通尚待公网回调联调。

### 7.3 推荐拓扑（规划）

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────────┐
│   用户设备       │     │  Windows 机器    │     │  云服务器 / 本机    │
│  (企微客户端)    │◄───►│  WXWork.exe     │◄────│  Agent 核心        │
│                 │hook │  + ntwork hook  │     │  + SQLite          │
└─────────────────┘     └─────────────────┘     │  + REST API 层     │
        ↑                                        │  + 运营后台前端    │
        └──────────────── 或 ──────────────────────│  (前端自行开发)    │
                           微信客服（公网回调）     └─────────────────────┘
```

---

## 8. 分阶段路线

> 务必注意：**以下标注"现状"或 ✅ 的部分才是已落地的代码；标注"📋 规划"或 📋 的均为尚未实现。**

### P0：接口 / 骨架 / 文档（现状）

- ✅ `Message` 统一模型 + `Adapter` 抽象 + `Router` 去重/触发/限流/接管静默/回发/记忆/on_escalate 回调流水线。
- ✅ `MockCliAdapter`（交互 + 脚本模式），Mac 本地可跑通全链路。
- ✅ `NtworkWecomAdapter` stub（文件顶部写满实现框架，当前抛 `NotImplementedError`）。
- ✅ `WecomKfAdapter` 已实现：回调 HTTP 服务 + msg_signature 验签 + AES 解密 + sync_msg 拉取 + send_msg 下发；access_token 带过期缓存；真机与腾讯服务器互通尚待公网回调联调。
- ✅ `EchoHandler` 占位 + `LLMHandler` 真大脑（纯 `urllib` 调 OpenAI 兼容接口，当前接 StepFun）。
- ✅ `KnowledgeProvider` 抽象 + `StaticKnowledgeProvider` 默认实现（读 `prompts/knowledge.md` 全量塞）。
- ✅ `RagKnowledgeProvider` 已实现：逻辑切块 + 混合检索（语义 embedding 余弦 top-k ∪ 关键词/bigram ∪ 活动/主推常驻块）+ 小库直通 + never-worse 兜底 + 内存+磁盘缓存；embedding 走 HTTP 或注入 embed_fn；任何出错回退全量。
- ✅ `SessionStore` 内存版（`dict` + `deque(maxlen=20)`），已加 `threading.Lock`；扩展点已留。
- ✅ 环境变量驱动配置（`.env` + `load_env_file`），无配置框架依赖。
- ✅ 技术文档体系（`docs/技术文档/` 00~06 已交付）。
- ✅ CI：`.github/workflows/ci.yml`，矩阵 Python 3.9/3.10/3.11/3.12，遍历跑 `tests/test_*.py`。
- ✅ 离线单测套件（全部纯离线，10 个文件全绿，注入假 transport/embedder、临时 db、localhost 无需外网）：
  - `tests/test_llm_offline.py`
  - `tests/test_knowledge_offline.py`
  - `tests/test_rag_offline.py`
  - `tests/test_structured_kb_offline.py`
  - `tests/test_kf_offline.py`
  - `tests/test_takeover_offline.py`
  - `tests/test_sqlite_offline.py`
  - `tests/test_api_offline.py`
  - `tests/test_integration_offline.py`
  - `tests/test_concurrency_offline.py`

### P1：持久化 + API + 结构化商品库 + FAQ RAG

- ✅ **SQLite 持久化**：`SqliteSessionStore` 已落地，保留 `MAX_CONTEXT` 语义；`MessageRecord` 表落地；`main.py` 通过环境变量 `STORE=memory` / `sqlite` 切换。`api_server.py` 的 `build_app()` 与 `main.py` 的 `build_sessions()` 保持一致，指向同一 `SQLITE_PATH` 即可共享会话。
- ✅ **REST API 层**：`ApiApp` + `stdlib http.server` 薄壳已落地，提供会话管理、人工接管、Agent 预览、配置查看、指标基础端点。前端按此契约对接。鉴权用 `ADMIN_TOKEN` + `hmac.compare_digest` 恒定时间比较；请求体上限 1MB（超返 413）。
- ✅ **人工接管后端**：结构化 escalation 信号（`[[转人工]]`）+ `mark_needs_human` + `on_escalate` 回调 + `human_controlled` 静默 + API `takeover`/`release` 已落地。`main.py` 已装配默认 `on_escalate`（打印 stderr）。
- ✅ **结构化商品库**：`StructuredKnowledgeProvider`（`core/knowledge.py`）已落地，按 `name`/`product_id`/`category`/`keywords` 做精确/子串匹配，返回价格/规格/库存/促销/状态（下架/售罄有中文标注）。样例数据 `prompts/products.json`（约10条坚果零食）。无命中返回空串；文件缺失/JSON 损坏兜底为空不崩。`main.py` 的 `build_knowledge()` 支持 `KNOWLEDGE_PROVIDER=structured` 装配。
- ✅ **FAQ RAG**：`RagKnowledgeProvider` 已落地，逻辑切块 + 语义 embedding 余弦 top-k + 关键词/子串/常驻块混合检索 + 小库直通 + never-worse 兜底。embedding 走 HTTP（OpenAI 兼容 `/embeddings`）或注入 `embed_fn`。内存+磁盘缓存（`data/rag_cache`，按内容 SHA256+模型名）。任何出错回退全量（等价 `Static`）。当前 StepFun key 未必带 embedding 端点，未配置则自动回退全量，不影响可用。`main.py` 的 `build_knowledge()` 支持 `KNOWLEDGE_PROVIDER=rag` 装配。
- ✅ **知识混合策略**：`HybridKnowledgeProvider` 已落地，组合 `primary`（结构化查商品）+ `fallback`（全量/语义查 FAQ），各自 try/except 兜底。`main.py` 的 `build_knowledge()` 支持 `KNOWLEDGE_PROVIDER=hybrid` 装配。`LLMHandler` 保持 "persona + 知识模块" 结构，知识模块内部按上述策略组合，无需改动 `core/llm_handler.py`。
- 📋 **RAG REST 端点**：语义检索预览、知识管理后台增删改查，保留规划（P2）。
- 📋 **商品库 REST 端点**：`/api/products` 增删改查，保留规划（P2）。

### P2：指标 + 配置中心 + 知识管理后台

- 📋 **指标埋点**：`MetricEvent` 表 + 实时看板端点（`/api/metrics/dashboard`），覆盖回复量、触发率、LLM 错误率、降级次数、人工接管率等。
- 📋 **配置中心**：动态调参（如 `rate_limit_sec`、`persona_md`），无需重启进程，运营后台即时生效。
- 📋 **知识管理后台**：FAQ 增删改查、知识文件上传、RAG 索引重建。
- 📋 **独立工单系统**：`EscalationTicket` 表 + 多客服分配 + SLA 统计 + 超时自动释放。
- 📋 **建议模式**：人工接管时 agent 后台推理提供回复建议，由人工决定是否发送。
- 📋 **上下文移交摘要**：接管时自动生成最近对话摘要写入工单。

### P3：接真实店铺数据 + 真实传输

- 📋 **真实店铺数据**：替换 `prompts/knowledge.md` 和 `prompts/products.json` 中的样例数据，接入真实 SKU、价格、库存、活动规则。
- 📋 **真实传输运行**：
  - Windows 机器上实装 `NtworkWecomAdapter`，企微客户端登录，真实群聊/单聊接入。
  - 微信客服真机公网回调联调：`WecomKfAdapter` 代码已就绪，需完成公网回调地址配置与腾讯服务器互通验证。
- 📋 **生产加固**：日志轮转、进程守护（supervisor/systemd）、配置加密（`ADMIN_TOKEN` / `LLM_API_KEY`）、错误告警（企微/钉钉 webhook）。
- 📋 **持续迭代**：根据真实运营数据，优化触发策略、prompt 模板、知识库覆盖、FAQ 命中率和人工接管阈值。
- ✅ **CI**：`.github/workflows/ci.yml` 已落地，矩阵 Python 3.9/3.10/3.11/3.12，遍历跑 `tests/test_*.py`（退出码传播）。

---

## 附录：链路速查表

| 你想做的事 | 应该看哪个文档 | 应该跑哪个命令 |
|-----------|---------------|---------------|
| 了解技术全景 | [技术总览](00-总览.md) | — |
| 看代码层实现 | [后端](03-后端.md) | — |
| 了解传输与部署 | [适配器与部署](04-适配器与部署.md) | — |
| 本地验证全链路（Mac） | [后端](03-后端.md) | `MOCK=1 HANDLER=llm python3 main.py` |
| 离线 LLM 单测 | [测试文档](../测试文档/01-离线单测.md) | `python3 tests/test_llm_offline.py` |
| 知识模块设计 | [知识模块设计](05-知识模块设计.md) | `python3 tests/test_knowledge_offline.py` |
| 了解本系统架构与 API | 本文档 | — |
| 运行 REST API（本地） | 本文档 | `ADMIN_TOKEN=dev python3 api_server.py`（或 `python3 tests/test_api_offline.py`） |
| 运行人工接管离线测试 | 本文档 | `python3 tests/test_takeover_offline.py` |
| 运行 SQLite 离线测试 | 本文档 | `python3 tests/test_sqlite_offline.py` |
| 运行端到端集成测试 | 本文档 | `python3 tests/test_integration_offline.py` |
