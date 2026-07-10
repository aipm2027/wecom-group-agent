# wecom-group-agent

电商带货**私域维护**智能客服 agent。接管对话、用大模型智能回复，像个懂行的贴心导购：维护关系、答疑、适度种草促复购、必要时引导下单或转人工。

**传输无关**架构：换传输（企微群 hook / 微信客服 / mock）、换大脑（规则 / 大模型）都不动核心。核心纯 Python 标准库、零第三方依赖。

> ⚠️ 合规提醒：接管微信**群**实时消息只能靠 hook 注入企微客户端（违反企微协议、有封号风险、且仅 Windows 可行）。微信客服 API 官方合规但只能 1:1、进不了群。详见 [需求文档](docs/需求文档/00-总览.md) 与 [04-适配器与部署](docs/技术文档/04-适配器与部署.md)。

## 目录结构

```
wecom-group-agent/
├── main.py              # 程序入口（装配 adapter/handler/knowledge/session 后启动）
├── core/                # 后端核心引擎（传输无关，纯 stdlib）
│   ├── message.py       #   规范化消息 Message + BOT_SENDER_ID
│   ├── adapter.py       #   Adapter 抽象（start/send）
│   ├── router.py        #   路由：去重/触发/限流/接管静默/回发/记忆/on_escalate
│   ├── handler.py       #   Handler 抽象 + EchoHandler（占位）
│   ├── llm_handler.py   #   LLMHandler（真大脑，调 StepFun；[[转人工]] 标记剥离）
│   ├── knowledge.py     #   知识模块：Static / Rag / Structured / Hybrid 四实现
│   ├── session.py       #   会话上下文（内存版 SessionStore，线程安全）
│   └── session_sqlite.py#   SqliteSessionStore（SQLite 持久化，重启恢复）
├── adapters/            # 传输适配器（可插拔）
│   ├── mock_cli.py      #   本地模拟（交互/JSON 脚本）
│   ├── ntwork_wecom.py  #   企微群 hook（仅 Windows，stub 待实现）
│   ├── wecom_kf.py      #   微信客服适配器（官方合规 1:1，已实现）
│   └── wecom_crypto.py  #   企微回调加解密（纯 Python AES-256 / WXBizMsgCrypt）
├── api_server.py        # REST API 层（运营后台，stdlib http.server）
├── admin_console.py     # 运营管理后台 Web 工作台（零依赖单文件，反代 api_server）
├── prompts/             # 人设 + 知识资产（可编辑）
│   ├── persona.md       #   私域导购人设
│   ├── knowledge.md     #   店铺/产品/FAQ（样例，待替换）
│   └── products.json    #   结构化商品库样例（structured/hybrid 用）
├── tests/               # 离线测试（13 套，全绿）
├── examples/            # 样例与评测（sample.json / demo_agent.py）
├── docs/                # 文档（需求/技术/测试三大块，见下）
├── .github/workflows/   # GitHub Actions CI（多版本跑全部离线测试）
├── .env.example         # 配置模板（复制为 .env 填真实值）
└── .gitignore
```

## 快速开始

```bash
# 1) 离线跑通链路（无需任何 key，复读机大脑）
MOCK=1 HANDLER=echo python3 main.py
#    输入：群A|张三|@bot 你好   → 回复：收到：你好

# 2) 接真大模型（配置见下）
cp .env.example .env         # 填入 LLM_API_KEY 等
MOCK=1 HANDLER=llm python3 main.py

# 3) 选知识后端（默认 static；rag 向量检索 / structured 结构化商品库 / hybrid 组合）
MOCK=1 HANDLER=llm KNOWLEDGE_PROVIDER=hybrid python3 main.py

# 4) 会话持久化（重启不丢历史与接管状态）
STORE=sqlite MOCK=1 HANDLER=llm python3 main.py

# 5) 微信客服（官方合规 1:1，需公网回调 + .env 配 WECOM_*）
ADAPTER=kf HANDLER=llm python3 main.py

# 6) 运营后台 REST API（会话/接管/试聊/指标）
ADMIN_TOKEN=xxx API_PORT=8080 python3 api_server.py

# 7) 运营管理后台 Web 工作台（浏览器操作：收件箱/接管/人工回复/试聊）
ADMIN_TOKEN=xxx CONSOLE_PASSWORD=yyy python3 admin_console.py
#    打开 http://127.0.0.1:8090 ，口令登录（需先起 6) 的 API）

# 8) 全部离线测试（13 套）
for f in tests/test_*.py; do python3 "$f"; done
```

配置项见 [.env.example](.env.example)。当前 LLM 用 StepFun（阶跃星辰，OpenAI 兼容），换模型/换 Claude 只改 `LLM_BASE_URL`/`LLM_MODEL`。

> **启动自检**：关键配置缺失/拼写错误会在启动时列出全部问题（含中文修复指引）并拒绝启动（退出码 2），不会静默降级——漏配 `LLM_API_KEY` 不会再"看起来在跑，实际是兜底复读机"。

## 生产部署（Docker 三服务）

```bash
cp .env.example .env      # 填 LLM_API_KEY + WECOM_*（获取方法见联调 SOP）
docker compose up -d      # agent(9000 公网回调) + api(8080 仅回环) + console(8090 仅回环)
docker compose ps         # 期望三服务 Up，api 带 healthcheck
make health               # 一键健康检查（三服务 ready 与否；compose up 后可用 python3 healthcheck.py --wait 60 等就绪）
docker compose logs -f agent   # 看回调/自检日志
```

- 微信客服回调需公网 **https**：反代（Caddy 两行配置）或内网穿透，逐步操作、故障速查、验收清单见 **[07-真机联调 SOP](docs/技术文档/07-真机联调SOP.md)**。
- 运营 API 与工作台只绑回环，不直接暴露公网；远程访问走 SSH 隧道：`ssh -L 8090:127.0.0.1:8090 服务器` 后本地打开 `http://127.0.0.1:8090`。

## 运营者 5 分钟上手（Web 工作台）

1. 打开 `http://127.0.0.1:8090`，输入口令（`CONSOLE_PASSWORD`）登录；
2. 左侧「**待人工**」标签有角标 = AI 兜不住的会话，点开可见转人工原因；
3. 点「**人工接管**」→ AI 立即静默，下方输入框直接回复客户（Ctrl/⌘+Enter 发送）；
4. 处理完点「**交回 AI**」，AI 恢复自动回复；
5. 右上「**🧪 试聊调试**」随时测试 AI 回复效果（不入库、不下发、不影响真实会话）。

## 文档

| 文档 | 内容 |
|------|------|
| [需求文档](docs/需求文档/00-总览.md) | 背景动机、目标用户、核心场景、功能/非功能需求、边界与路线图 |
| [技术文档 · 总览](docs/技术文档/00-总览.md) | **总**：架构图、模块索引、消息数据流、技术选型与硬约束 |
| [技术文档 · Agent](docs/技术文档/01-Agent.md) | 分：回复大脑、system prompt 组装、多轮、护栏、换模型 |
| [技术文档 · 知识库](docs/技术文档/02-知识库.md) | 分：人设/知识文件机制、**如何替换成你的真实店铺信息** |
| [技术文档 · 后端](docs/技术文档/03-后端.md) | 分：Message/Router/Session/入口、触发/去重/限流 |
| [技术文档 · 适配器与部署](docs/技术文档/04-适配器与部署.md) | 分：mock / ntwork / 微信客服、部署与合规 |
| [技术文档 · 知识模块设计](docs/技术文档/05-知识模块设计.md) | 分：提示词/RAG/结构化选型、可插拔知识模块、RAG 落地 |
| [技术文档 · 系统架构与API](docs/技术文档/06-系统架构与API.md) | 分：分层架构、数据模型、REST API 契约、人工接管、路线图 |
| [测试文档](docs/测试文档/00-总览.md) | 测试策略、离线用例、评测集、已知结果 |

## 当前进度

- ✅ 传输无关架构 + mock 全链路在 Mac 跑通（触发/去重/限流/多轮）
- ✅ LLMHandler 接 StepFun，人设+知识文件化；`[[转人工]]` 标记剥离 + 网络异常降级
- ✅ 可插拔知识模块：Static / RAG（混合检索）/ 结构化商品库 / Hybrid，`KNOWLEDGE_PROVIDER` 一键切换
- ✅ 人工接管：转人工信号 + 接管静默 + REST API（`/api/queue`、takeover/release）
- ✅ SQLite 持久化（`STORE=sqlite`，重启恢复）+ REST API 运营后台 + GitHub Actions CI（Py 3.9–3.12）
- ✅ 运营管理后台 Web 工作台（`admin_console.py`：收件箱/接管/人工回复/试聊调试，零依赖单文件）
- ✅ 微信客服适配器（官方合规 1:1，纯 Python AES-256 回调加解密）——代码就绪，真机公网回调联调待验证
- ⏳ 真实企微群 hook（`ntwork`）——待 Windows 环境实机
