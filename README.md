# wecom-group-agent

电商带货**私域维护**智能客服 agent。接管对话、用大模型智能回复，像个懂行的贴心导购：维护关系、答疑、适度种草促复购、必要时引导下单或转人工。

**传输无关**架构：换传输（企微群 hook / 微信客服 / mock）、换大脑（规则 / 大模型）都不动核心。核心纯 Python 标准库、零第三方依赖。

> ⚠️ 合规提醒：接管微信**群**实时消息只能靠 hook 注入企微客户端（违反企微协议、有封号风险、且仅 Windows 可行）。微信客服 API 官方合规但只能 1:1、进不了群。详见 [需求文档](docs/需求文档/00-总览.md) 与 [04-适配器与部署](docs/技术文档/04-适配器与部署.md)。

## 目录结构

```
wecom-group-agent/
├── main.py              # 程序入口（python3 main.py）
├── core/                # 后端核心引擎（传输无关）
│   ├── message.py       #   规范化消息 Message + BOT_SENDER_ID
│   ├── adapter.py       #   Adapter 抽象（start/send）
│   ├── router.py        #   路由：去重/触发/限流/回发/记忆
│   ├── handler.py       #   Handler 抽象 + EchoHandler（占位）
│   ├── llm_handler.py   #   LLMHandler（真大脑，调 StepFun）
│   └── session.py       #   会话上下文（内存）
├── adapters/            # 传输适配器（可插拔）
│   ├── mock_cli.py      #   本地模拟（交互/JSON 脚本）
│   └── ntwork_wecom.py  #   企微群 hook（仅 Windows，stub 待实现）
├── prompts/             # 人设 + 知识库（可编辑资产）
│   ├── persona.md       #   私域导购人设
│   └── knowledge.md     #   店铺/产品/FAQ（当前为样例，待替换）
├── tests/               # 离线测试
│   └── test_llm_offline.py
├── examples/            # 样例与评测
│   ├── sample.json      #   mock 脚本样例
│   └── demo_agent.py    #   9 场景私域维护评测
├── docs/                # 文档（见下）
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

# 3) 离线单测
python3 tests/test_llm_offline.py

# 4) 9 场景私域维护评测（真调 LLM）
python3 examples/demo_agent.py
```

配置项见 [.env.example](.env.example)。当前 LLM 用 StepFun（阶跃星辰，OpenAI 兼容），换模型/换 Claude 只改 `LLM_BASE_URL`/`LLM_MODEL`。

## 文档

| 文档 | 内容 |
|------|------|
| [需求文档](docs/需求文档/00-总览.md) | 背景动机、目标用户、核心场景、功能/非功能需求、边界与路线图 |
| [技术文档 · 总览](docs/技术文档/00-总览.md) | **总**：架构图、模块索引、消息数据流、技术选型与硬约束 |
| [技术文档 · Agent](docs/技术文档/01-Agent.md) | 分：回复大脑、system prompt 组装、多轮、护栏、换模型 |
| [技术文档 · 知识库](docs/技术文档/02-知识库.md) | 分：人设/知识文件机制、**如何替换成你的真实店铺信息** |
| [技术文档 · 后端](docs/技术文档/03-后端.md) | 分：Message/Router/Session/入口、触发/去重/限流 |
| [技术文档 · 适配器与部署](docs/技术文档/04-适配器与部署.md) | 分：mock / ntwork / 微信客服、部署与合规 |
| [测试文档](docs/测试文档/00-总览.md) | 测试策略、离线用例、评测集、已知结果 |

## 当前进度

- ✅ 传输无关架构 + mock 全链路在 Mac 跑通（触发/去重/限流/多轮）
- ✅ LLMHandler 接 StepFun，人设+知识文件化，9 场景评测
- ⏳ 真实企微群 hook（`ntwork`）——待 Windows 环境
- ⏳ 微信客服适配器（合规 1:1）——待接入
