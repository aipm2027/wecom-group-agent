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
│                           API 层（规划）                                    │
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
│  │  Handler（可插拔）          │      │  SessionStore（内存版）   │       │
│  │  ┌──────────────────────┐  │      │  按 chat_id 隔离          │       │
│  │  │ EchoHandler（占位）   │  │      │  deque(maxlen=20)         │       │
│  │  │ LLMHandler（真大脑）  │  │      │  扩展点：SQLite/Redis      │       │
│  │  └──────────────────────┘  │      └──────────────────────────┘       │
│  │  ┌──────────────────────┐  │                                         │
│  │  │ KnowledgeProvider     │  │                                         │
│  │  │  StaticKnowledgeProvider│  ← 读 prompts/knowledge.md 全量返回     │
│  │  │  RagKnowledgeProvider   │  ← 规划：向量检索，当前 NotImplemented   │
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
│  │ MockCliAdapter   │  │ NtworkWecomAdapter│  │ 微信客服适配器（规划）     │ │
│  │  • 交互/脚本输入  │  │  • 企微群 hook   │  │  • 官方合规 1:1            │ │
│  │  • Mac 本地验证   │  │  • 仅 Windows    │  │  • 仅单聊，不进群          │ │
│  │  【已实现】       │  │  【stub】        │  │  【规划】                  │ │
│  └──────────────────┘  └──────────────────┘  └────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘

                               ┌────────────────────┐
                               │   旁路模块（规划）   │
┌──────────────────────────────┴────────────────────┴──────────────────────────┐
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │ 人工接管工作台  │  │ 知识管理后台   │  │ 配置中心       │  │ 指标/事件管道  │ │
│  │  • 工单队列     │  │  • 知识上传    │  │  • 人设/提示词  │  │  • 埋点采集    │ │
│  │  • 人工回复     │  │  • 商品结构化  │  │  • 系统参数    │  │  • 实时看板    │ │
│  │  • 接管/释放    │  │  • 索引管理    │  │  • 开关/阈值   │  │  • 离线分析    │ │
│  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘ │
│              ↑↑↑↑↑↑↑↑↑↑↑↑↑↑ 以上均为规划，尚未实现 ↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 关键架构决策

| 决策 | 说明 |
|------|------|
| **传输无关三层** | 传输适配器 ↔ Router ↔ Handler/Session，任何一层替换不影响其余层。 |
| **零依赖核心** | `core/` 纯 Python 标准库，LLM 调用用 `urllib`，无 `fastapi`/`sqlalchemy`/`redis` 等强依赖。规划中的 SQLite / HTTP 层作为可选扩展，不污染核心。 |
| **可插拔知识模块** | `KnowledgeProvider` 抽象基类（`retrieve(query)`），当前默认 `StaticKnowledgeProvider`（全量提示词直塞）；以后无缝换 `RagKnowledgeProvider`（向量检索），`LLMHandler` 一行不改。详见 [core/knowledge.py](../../core/knowledge.py)。 |
| **前端仅按 API 契约对接** | 运营后台前端由调用方自行开发，本项目只提供 REST API 契约与鉴权说明。 |

---

## 2. 模块清单表（现状 vs 规划）

| 模块 | 文件/目录 | 现状 | 规划 | 备注 |
|------|-----------|------|------|------|
| 消息模型 | `core/message.py` | ✅ 已实现 | — | `Message` 统一结构，`BOT_SENDER_ID="__bot__"` |
| Adapter 抽象 | `core/adapter.py` | ✅ 已实现 | — | `start`/`send` 契约 |
| Mock 适配器 | `adapters/mock_cli.py` | ✅ 已实现 | — | 交互/脚本两种模式，Mac 本地验证 |
| Ntwork 适配器 | `adapters/ntwork_wecom.py` | 🟡 Stub | 待 Windows 实机 | 文件顶部注释已写实现框架，当前抛 `NotImplementedError` |
| 微信客服适配器 | — | ❌ 无 | 规划中 | 官方合规 1:1，需公网回调+AES 解密 |
| Router | `core/router.py` | ✅ 已实现 | — | 去重/触发/限流/回发/记忆流水线 |
| Handler 抽象 | `core/handler.py` | ✅ 已实现 | — | `EchoHandler` 占位 + `LLMHandler` 真大脑 |
| LLMHandler | `core/llm_handler.py` | ✅ 已实现 | 持续优化 | 纯 `urllib` 调 OpenAI 兼容接口；system prompt 三级组装；支持 `transport` 注入测试 |
| KnowledgeProvider | `core/knowledge.py` | ✅ 骨架已落地 | 规模化后实现 RAG | `StaticKnowledgeProvider` 默认可用；`RagKnowledgeProvider` 抛 `NotImplementedError` |
| Session/SessionStore | `core/session.py` | ✅ 内存版已实现 | 规划 SQLite 持久化 | `deque(maxlen=20)`，扩展点已留 |
| 持久化(SQLite) | — | ❌ 无 | 规划 P1 | 保留 `MAX_CONTEXT` 淘汰语义 |
| REST API 层 | — | ❌ 无 | 规划 P1 | 给前端运营后台，HTTP+JSON，ADMIN_TOKEN 鉴权 |
| 人工接管流程 | — | ❌ 无 | 规划 P2 | 结构化 escalation 信号 + 工单队列 + 工作台接管 |
| 指标/事件管道 | — | ❌ 无 | 规划 P2 | 埋点采集、实时看板、离线分析 |
| 结构化商品库 | — | ❌ 无 | 规划 P1 | 精确查价/规格/库存，与 RAG 互补 |
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

### 3.4 消息记录（MessageRecord）—— 📋 规划

| 字段 | 类型 | 说明 |
|------|------|------|
| `record_id` | `int` | 自增主键 |
| `chat_id` | `str` | 所属会话 |
| `msg_id` | `str` | 去重标识（原始传输层 id） |
| `sender_id` | `str` | 发送者 |
| `sender_name` | `str` | 发送者昵称 |
| `content` | `text` | 文本内容 |
| `msg_type` | `str` | `"text"` / `"image"` / `"voice"` 等 |
| `is_at_bot` | `bool` | 群聊是否 @ 机器人 |
| `timestamp` | `int` | 毫秒时间戳 |
| `is_bot` | `bool` | 是否机器人发送 |
| `created_at` | `datetime` | 入库时间 |

> 规划理由：将 `Session.history` 从内存 `deque` 迁移到 SQLite，保留历史记录用于审计、多轮上下文恢复和指标统计。

### 3.5 商品（Product）—— 📋 规划

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

> 规划理由：电商场景下，"这个怎么卖"需要精确的价格/规格/库存，结构化商品库优于纯 RAG 语义检索。建议与 `RagKnowledgeProvider` 组合使用：商品查结构化，FAQ 查语义 RAG。

### 3.6 FAQ（知识条目）—— 📋 规划

| 字段 | 类型 | 说明 |
|------|------|------|
| `faq_id` | `int` | 自增主键 |
| `question` | `text` | 标准问题 |
| `answer` | `text` | 标准答案 |
| `category` | `str` | 分类（如"物流"、"售后"、"活动"） |
| `embedding` | `blob` | 向量（RAG 规模化后使用） |
| `status` | `str` | `"active"` / `"archived"` |
| `updated_at` | `datetime` | 最后更新时间 |

> 规划理由：替换 `prompts/knowledge.md` 的纯文本方式，支持 FAQ 语义检索和增量更新。

### 3.7 接管工单（EscalationTicket）—— 📋 规划

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

> ⚠️ **以下所有 API 端点均为规划，尚未实现。** 当前项目无 HTTP 服务层，计划 P1 引入轻量 HTTP 框架（如 `http.server` 或 `aiohttp`，均作为可选依赖，不污染零依赖核心）。

### 4.1 统一鉴权说明

所有端点（除健康检查外）在请求头中携带 `Authorization: Bearer <ADMIN_TOKEN>`。
- `ADMIN_TOKEN` 从环境变量读取（`.env` 中 `ADMIN_TOKEN=...`），进程启动时加载，重启生效。
- 未提供或 token 不匹配时返回 `401 Unauthorized`。
- 规划后续可扩展为 RBAC（区分运营/管理员/只读）。

### 4.2 会话管理

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/v1/sessions` | 会话列表（分页） | `?page=1&size=20&status=active`；返回 `chat_id`、`last_active_at`、`msg_count` 等 |
| `GET` | `/api/v1/sessions/{chat_id}` | 会话详情 | 返回完整会话历史（最近 N 条） |
| `GET` | `/api/v1/sessions/{chat_id}/messages` | 会话消息流 | `?limit=20&offset=0`；按时间正序/倒序 |
| `POST` | `/api/v1/sessions/{chat_id}/send` | 运营后台主动发消息 | `{"content":"..."}`；通过适配器发送，记入 session |
| `DELETE` | `/api/v1/sessions/{chat_id}` | 清空/删除会话 | 软删除，保留历史记录用于审计 |

### 4.3 知识管理

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/v1/knowledge` | 查看当前知识 | 返回 `prompts/knowledge.md` 文本（Static 阶段）或 FAQ 列表（P1 后） |
| `PUT` | `/api/v1/knowledge` | 全量更新知识 | `{"content":"..."}`；覆盖 `knowledge.md`；触发 LLMHandler 重新加载（若运行中） |
| `POST` | `/api/v1/faqs` | 新增 FAQ（P1 后） | `{"question":"...","answer":"...","category":"..."}` |
| `PUT` | `/api/v1/faqs/{faq_id}` | 修改 FAQ | 同上 |
| `DELETE` | `/api/v1/faqs/{faq_id}` | 删除/归档 FAQ | 软删除 |
| `POST` | `/api/v1/faqs/search` | FAQ 语义检索（P2 后） | `{"query":"...","top_k":5}`；调用 RagKnowledgeProvider 检索 |

### 4.4 商品库（结构化）

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/v1/products` | 商品列表 | `?category=...&status=...&q=关键词`；支持关键词过滤 |
| `GET` | `/api/v1/products/{product_id}` | 商品详情 | 返回完整商品信息 |
| `POST` | `/api/v1/products` | 新增商品 | 完整商品字段 |
| `PUT` | `/api/v1/products/{product_id}` | 更新商品 | 部分更新支持 |
| `DELETE` | `/api/v1/products/{product_id}` | 删除商品 | 软删除 |

### 4.5 人设与配置

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/v1/persona` | 查看当前人设 | 返回 `prompts/persona.md` 文本 |
| `PUT` | `/api/v1/persona` | 更新人设 | `{"content":"..."}`；覆盖 `persona.md`；热重载（LLMHandler 重新读取） |
| `GET` | `/api/v1/config` | 查看全部配置 | 返回 key-value 列表 |
| `PUT` | `/api/v1/config/{key}` | 更新单项配置 | `{"value":"..."}`；如 `rate_limit_sec`、`dedup_max` 等 |
| `POST` | `/api/v1/config/reload` | 强制重新加载配置 | 重新读取 `.env` 与 `prompts/` 目录文件 |

### 4.6 人工接管

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/v1/tickets` | 工单列表 | `?status=open&assigned_to=me`；分页 |
| `GET` | `/api/v1/tickets/{ticket_id}` | 工单详情 | 包含客户摘要、会话历史、升级原因 |
| `POST` | `/api/v1/tickets/{ticket_id}/takeover` | 人工接管 | 设置 `agent_id`，状态变为 `assigned`；该会话进入人工接管态 |
| `POST` | `/api/v1/tickets/{ticket_id}/reply` | 人工回复 | `{"content":"..."}`；通过适配器发送 |
| `POST` | `/api/v1/tickets/{ticket_id}/release` | 释放回 agent | 状态变为 `resolved`，agent 恢复自动回复 |
| `POST` | `/api/v1/tickets/{ticket_id}/cancel` | 取消工单 | 状态变为 `cancelled`，agent 恢复 |
| `GET` | `/api/v1/tickets/stats` | 工单统计 | 今日待处理、平均响应时长、已解决数等 |

### 4.7 指标与看板

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `GET` | `/api/v1/metrics/dashboard` | 实时看板 | 今日消息量、回复量、触发率、LLM 错误率、人工接管率等 |
| `GET` | `/api/v1/metrics/events` | 事件查询 | `?type=reply&chat_id=...&from=...&to=...`；分页 |
| `GET` | `/api/v1/metrics/llm_stats` | LLM 调用统计 | 调用次数、平均延迟、降级次数、token 消耗（估算） |
| `POST` | `/api/v1/metrics/events` | 手动上报（测试用） | 内部使用 |

### 4.8 Agent 预览与调试

| 方法 | 路径 | 用途 | 请求/响应要点 |
|------|------|------|---------------|
| `POST` | `/api/v1/preview/chat` | 单轮对话预览 | `{"messages":[{...}],"persona":"...","knowledge":"..."}`；调用 LLMHandler，不记入真实会话 |
| `POST` | `/api/v1/preview/knowledge` | 知识检索预览 | `{"query":"..."}`；调用当前 KnowledgeProvider 的 `retrieve(query)`，返回命中内容 |
| `POST` | `/api/v1/preview/product_match` | 商品匹配预览 | `{"query":"..."}`；返回结构化商品查询结果（P1 后） |

### 4.9 响应格式

统一返回 JSON：
```json
{
  "code": 0,
  "message": "ok",
  "data": { ... },
  "pagination": {
    "page": 1,
    "size": 20,
    "total": 100
  }
}
```

错误时 `code != 0`，如 `code=401` 表示鉴权失败，`code=404` 表示资源不存在，`code=500` 表示内部错误。

---

## 5. 人工接管流程

> ⚠️ **人工接管工作台当前仅为规划，尚未实现。** Agent 目前遇到"需人工处理"场景时，以话术形式引导（如"稍等，我帮你转人工客服"），但**无结构化信号、无工单系统、无工作台**，仅停留在 LLM 生成的文本回复。

### 5.1 目标流程（规划）

```
客户提问（复杂/投诉/需查库存）
         │
         ▼
  ┌──────────────┐
  │  LLMHandler  │  识别需人工介入（或通过关键词/规则触发）
  │  生成结构化  │  输出 escalation_signal 而非纯话术
  │  escalation │
  └──────┬───────┘
         │ 结构化信号（JSON）
         │ {
         │   "type": "escalation",
         │   "reason": "complex_query",
         │   "summary": "客户询问团购定制价...",
         │   "urgency": "normal"
         │ }
         ▼
  ┌──────────────┐
  │   Router     │  拦截 escalation_signal，暂停自动回复
  │  创建工单    │  发送占位安抚话术（"已为您转接人工，请稍候"）
  └──────┬───────┘
         │ 写入 EscalationTicket
         │ 状态：open
         ▼
  ┌──────────────┐
  │  通知系统    │  推送通知（WebSocket/钉钉/企微）
  │  通知人工    │  告知有新工单待处理
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │ 人工工作台   │  客服打开运营后台
  │  查看工单    │  查看客户摘要 + 会话历史
  │  点击接管    │  状态：assigned → 人工接管态
  └──────┬───────┘
         │
         ▼
  ┌─────────────────────────────────┐
  │ 接管态行为                       │
  │  • Agent 静默，不再自动回复       │
  │  • 或：Agent 继续运行但只给建议   │
  │    （人工客服可见 AI 建议，自己决定发不发）
  │  • 人工客服通过工作台发送消息     │
  └──────┬────────────────────────────┘
         │ 人工回复通过 adapter.send() 发送
         ▼
  ┌──────────────┐
  │ 客服结束会话 │  点击"结束"或释放
  │  状态 resolved│  状态变为 resolved
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  Agent 恢复  │  回到自动回复模式
  │  自动回复    │  可附带上下文摘要给 LLM
  └──────────────┘
```

### 5.2 关键设计决策

| 决策 | 说明 |
|------|------|
| **结构化 escalation 信号** | 现状是 LLM 输出纯话术（"转人工"），**规划改为** LLM 输出结构化 JSON 信号，`Router` 拦截该信号后走工单流程，而非把"转人工"当普通回复发给客户。这要求：① 给 LLM 加 `escalation` 工具/格式说明；② `LLMHandler` 解析输出，若检测到结构化信号则返回特殊对象而非纯字符串；③ `Router` 识别该对象并触发接管。 |
| **静默 vs 建议模式** | 规划支持两种模式：① 完全静默（agent 不生成任何回复）；② 建议模式（agent 在后台继续推理，给人工客服提供回复建议，但由人工决定是否发送）。默认静默模式，建议模式作为进阶配置。 |
| **上下文移交** | 人工接管时，自动将最近 `MAX_CONTEXT` 条消息生成摘要（可调用 LLM 生成）写入工单 `summary`，人工客服无需翻历史即可了解全貌。 |
| **释放机制** | 人工客服可主动释放（结束工单），或超时自动释放（如 30 分钟无人工回复），释放后 agent 恢复自动回复。 |

---

## 6. 持久化（现状：内存 → 规划：SQLite）

### 6.1 现状：内存 SessionStore

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

### 6.2 规划：SQLite 持久化

P1 阶段引入 `sqlite3`（Python 标准库自带，仍保持零额外依赖）实现持久化。

#### 设计要点

| 要点 | 方案 |
|------|------|
| **存储范围** | `MessageRecord`（全量消息）+ `Session`（元数据：最后活跃时间、当前状态、客户关联）+ `Customer`（客户画像）+ `EscalationTicket`（P2）。不存 LLM 原始响应（仅存最终发送给用户的文本）。 |
| **MAX_CONTEXT 语义保留** | 查询时按 `ORDER BY timestamp DESC LIMIT 20` 取最近消息，再按时间正序送入 LLMHandler，与内存 `deque(maxlen=20)` 语义一致。 |
| **BOT_SENDER_ID 消息也需持久化** | 否则进程重启后，机器人自己的回复丢失，多轮上下文断裂。 |
| **读写分离** | 高频写入（每条消息）直接写 SQLite；读取（运营后台查询、LLM 上下文组装）走同一个库。SQLite 在单进程/低并发场景足够；若以后需高并发，可换 PostgreSQL/Redis，但当前不需要。 |
| **表结构** | 采用第 3 节中的 `MessageRecord`、`Session`（持久化版）、`Customer` 等表。 |
| **迁移方式** | ① P1 新增 `SQLiteSessionStore` 类，保持 `get(chat_id) -> Session` 接口不变；② `main.py` 中通过环境变量 `SESSION_STORE=memory`（默认）/ `sqlite` 切换；③ 切换后，内存版数据不做迁移（数据量小，可接受冷启动），运营后台历史数据从 SQLite 重新查询。 |
| **索引** | `chat_id` + `timestamp` 联合索引；`msg_id` 唯一索引（去重）；`sender_id` 索引（客户画像聚合）。 |

### 6.3 扩展点：以后换 PostgreSQL/Redis

`SessionStore` 的接口设计保持了最小契约：`get(chat_id) -> Session`、`add(msg) -> None`。以后需要横向扩展时，只需替换 `SessionStore` 实现，Router 和 Handler 均无需改动。

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
- **微信客服适配器（规划）**无操作系统限制：只需公网回调地址，Agent 核心可部署在任意有公网 IP 的服务器上。

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

> 务必注意：**以下标注"现状"的部分才是已落地的代码；标注"P1/P2/P3"的均为规划，尚未实现。**

### P0：接口 / 骨架 / 文档（现状）

- ✅ `Message` 统一模型 + `Adapter` 抽象 + `Router` 去重/触发/限流/回发/记忆流水线。
- ✅ `MockCliAdapter`（交互 + 脚本模式），Mac 本地可跑通全链路。
- ✅ `NtworkWecomAdapter` stub（文件顶部写满实现框架，当前抛 `NotImplementedError`）。
- ✅ `EchoHandler` 占位 + `LLMHandler` 真大脑（纯 `urllib` 调 OpenAI 兼容接口，当前接 StepFun）。
- ✅ `KnowledgeProvider` 抽象 + `StaticKnowledgeProvider` 默认实现（读 `prompts/knowledge.md` 全量塞）。
- ✅ `RagKnowledgeProvider` stub（接口预留，抛 `NotImplementedError`）。
- ✅ `SessionStore` 内存版（`dict` + `deque(maxlen=20)`），扩展点已留。
- ✅ 环境变量驱动配置（`.env` + `load_env_file`），无配置框架依赖。
- ✅ 技术文档体系（`docs/技术文档/` 00~04 已交付，本文档 06 为当前新增）。
- ✅ 离线单测（`tests/test_llm_offline.py`、`tests/test_knowledge_offline.py`）。

### P1：持久化 + API + 结构化商品库 + FAQ RAG（样例）

- 📋 **SQLite 持久化**：`SQLiteSessionStore` 替换内存版，保留 `MAX_CONTEXT` 语义；`MessageRecord`、`Customer` 表落地；`main.py` 通过环境变量切换 `memory` / `sqlite`。
- 📋 **REST API 层**：引入轻量 HTTP 服务（如标准库 `http.server` 线程模型或 `aiohttp` 作为可选依赖），提供第 4 节中的会话管理、知识管理、人设配置、Agent 预览端点。前端按此契约对接。
- 📋 **结构化商品库**：`Product` 表 + 管理端点，支持关键词/属性精确查询，用于"这个怎么卖"等精确回答场景。
- 📋 **FAQ RAG 样例**：实现 `RagKnowledgeProvider`（可选依赖：复用 StepFun/OpenAI 兼容接口做 embedding；小规模向量存 SQLite 或 JSON，大规模用向量库），提供至少 20 条 FAQ 的索引和检索演示。
- 📋 **知识混合策略**：`LLMHandler._compose_system` 保持"persona + 知识模块"结构，知识模块内部可组合：精确商品查询（结构化库）+ 语义 FAQ 检索（RAG）+ 兜底全量知识（Static）。

### P2：人工接管工作台后端 + 指标

- 📋 **结构化 escalation 信号**：改造 `LLMHandler` 和 `Router`，使 agent 输出 JSON 结构化信号而非纯话术，触发接管流程。
- 📋 **接管工单系统**：`EscalationTicket` 表 + 人工接管端点（`takeover` / `reply` / `release`），工作台状态管理。
- 📋 **人工回复通道**：运营后台通过 API 发送的消息，走 `adapter.send()` 发给客户，与 agent 自动回复共享同一发送通道。
- 📋 **指标埋点**：`MetricEvent` 表 + 实时看板端点（`/api/v1/metrics/dashboard`），覆盖回复量、触发率、LLM 错误率、降级次数、人工接管率等。
- 📋 **配置中心**：动态调参（如 `rate_limit_sec`、`persona_md`），无需重启进程，运营后台即时生效。
- 📋 **知识管理后台**：FAQ 增删改查、知识文件上传、RAG 索引重建。

### P3：接真实店铺数据 + 真实传输

- 📋 **真实店铺数据**：替换 `prompts/knowledge.md` 和 `Product` 表中的样例数据，接入真实 SKU、价格、库存、活动规则。
- 📋 **真实传输运行**：
  - Windows 机器上实装 `NtworkWecomAdapter`，企微客户端登录，真实群聊/单聊接入。
  - 或：优先接入**微信客服适配器**（合规稳定），将群聊流量引导至 1:1 客服通道。
- 📋 **生产加固**：日志轮转、进程守护（supervisor/systemd）、配置加密（`ADMIN_TOKEN` / `LLM_API_KEY`）、错误告警（企微/钉钉 webhook）。
- 📋 **持续迭代**：根据真实运营数据，优化触发策略、prompt 模板、知识库覆盖、FAQ 命中率和人工接管阈值。

---

## 附录：链路速查表

| 你想做的事 | 应该看哪个文档 | 应该跑哪个命令 |
|-----------|---------------|---------------|
| 了解技术全景 | [技术总览](00-总览.md) | — |
| 看代码层实现 | [后端](03-后端.md) | — |
| 了解传输与部署 | [适配器与部署](04-适配器与部署.md) | — |
| 本地验证全链路（Mac） | [后端](03-后端.md) | `MOCK=1 HANDLER=llm python3 main.py` |
| 离线 LLM 单测 | [测试文档](../测试文档/01-离线单测.md) | `python3 tests/test_llm_offline.py` |
| 知识模块设计 | `docs/技术文档/05-知识模块设计.md`（如已交付） | `python3 tests/test_knowledge_offline.py` |
| 了解本系统架构与 API | 本文档 | — |
