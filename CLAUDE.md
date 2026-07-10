# 维护者手册(人类和 AI 都请先读这份)

微信私域电商客服 AI agent。三层架构 Adapter(渠道)↔ Router(策略)↔ Handler(LLM)+Session(会话),
纯 Python 标准库,零第三方依赖,Python 3.9+。

## 铁律(违反任何一条 = 改错了)

1. **不引入任何第三方依赖**。没有 pip install,没有 requirements.txt,一切用标准库。
2. **兼容 Python 3.9**:类型注解里可以用 `X | None`(文件顶部都有 `from __future__ import annotations`),但**运行时代码**不能用 `match` 语句、不能在注解以外的地方用 `X | Y` 语法。
3. **密钥只进 `.env`**(已 gitignore),绝不写进代码、绝不提交、日志绝不打印 key。
4. **所有对外调用(HTTP/LLM/文件)必须有出错兜底**,客服进程绝不能因为一次异常崩掉。
5. **提交纪律:只 `git add` 你改过的具体文件,严禁 `git add -A` / `git add .`**。
6. 改完必须跑 **`make check`**(见下),全绿才能提交。

## 我要改 X,应该动哪个文件?

| 想改什么 | 动哪里 | 改完跑什么 |
|---|---|---|
| 客服的话术风格、红线、转人工时机 | `prompts/persona.md`(先读 `prompts/README.md`) | `make gate`(需 .env 有 key) |
| 店铺信息、活动、物流、售后 FAQ | `prompts/knowledge.md` | `make eval`,再 `make eval-online` 抽查 |
| 商品(加/改/删 SKU) | `prompts/products.json`;批量迁移用 `python3 examples/products_from_csv.py 你的.csv` | `make eval` |
| 转人工触发关键词/标签 | `core/llm_handler.py` 顶部 `REASON_*` 与 `_ESCALATION_RULES`(标签值别乱改,评测同源引用) | `make test && make eval` |
| 评测案例(增删断言) | `evals/golden.json`(每案带 notes 说明;断言经验见 `docs/技术文档/10`) | `make eval`;转人工/合规类必须 `--online` 双跑 |
| 端口/模型/开关等配置 | `.env`(模板见 `.env.example`,每项有注释) | `make health` |
| 部署(容器/服务编排) | `Dockerfile` / `docker-compose.yml` / `Makefile` | CI 的 docker job 会验 |
| 运营后台界面 | `admin_console.py`(单文件,HTML 内嵌) | `python3 tests/test_console_offline.py` |
| REST API | `api_server.py` | `python3 tests/test_api_offline.py` |
| 文档 | `docs/`(需求/技术/测试三册;新增难点记录追加到 `docs/技术文档/09`) | 链接别写死 |

## 禁区(想动这些,先读文档、想清楚再动)

- **`adapters/wecom_crypto.py`**:纯 Python AES-256 加解密,正确性由 FIPS-197 官方向量测试钉住。改一个字节都可能让微信回调验签全挂。
- **`core/knowledge.py` 的 `_search`**(RAG 三路召回):行为被评测断言锁定,改前读 `docs/技术文档/09` 的 B4。
- **`adapters/wecom_kf.py` / `adapters/wecom_aibot.py` 的协议实现**(回调验签/WebSocket 帧):真机验证过,协议细节见 `docs/技术文档/04` 与 `08`。
- **测试里的既有断言**:测试红了优先怀疑你的改动,而不是去改断言让它变绿。确要改断言,先在 `docs/技术文档/09` 追加一条记录说明理由。

## 一键命令(在仓库根目录)

```bash
make check        # 编译+16 套测试+离线评测,全绿才算改完(最常用)
make test         # 只跑 16 套离线测试
make eval         # 离线评测(知识检索+转人工规则,无需网络)
make eval-online  # 在线评测(真 LLM,需 .env 的 LLM_API_KEY)
make gate         # 合规门禁:改 persona/换模型后必跑,全绿才能上线
make health       # 三服务健康检查
make run-mock     # 本地模拟聊天(不连微信,Mac 可跑)
make help         # 全部命令
```

## 常见任务菜谱

**加一个商品**:编辑 `prompts/products.json`,照抄一条现有结构改字段(必填 `product_id/name/price`;
`keywords` 写客户口语里的各种叫法,召回全靠它;`status` 只能是 `on_sale/off_sale/out_of_stock`)。
跑 `make eval` —— schema 写错会直接报第几条错在哪。

**改售后政策**:改 `prompts/knowledge.md` 对应小节(保持 `# 标题` + `- 条目` 的格式,RAG 按这个切块)。
注意:persona 规定"政策咨询由知识库作答,不转人工"——政策没写进 knowledge.md,AI 就会把咨询转给人工。
改完 `make eval-online` 抽查相关问题。

**加转人工关键词**:`core/llm_handler.py` 的 `_ESCALATION_RULES`。注意"政策名词 vs 诉求"的坑:
"退款/退货"这类词单独出现是咨询、不该硬触发,须配 `_DEMAND_MARKERS`(见 `docs/技术文档/09` A3)。
加完在 `evals/golden.json` 补正反两个案例,`make eval` + 在线双跑。

**换 LLM 模型**:改 `.env` 的 `LLM_MODEL`(如有需要连 `LLM_BASE_URL`/`LLM_API_KEY`),
然后 **必须 `make gate` 全绿** 才算完成——这是合规门禁,不是建议。

**真机联调/重启智能机器人(aibot)**:`ADAPTER=aibot HANDLER=llm KNOWLEDGE_PROVIDER=hybrid STORE=sqlite python3 main.py`。
⚠️ 同一 bot_id **全网只允许一条长连接**——起第二个进程会与第一个以 ≤30s 周期互踢(日志反复"被新连接踢下线"),
本地调试前先停掉服务器/别处的进程。凭据(`WECOM_AIBOT_ID/SECRET`)必须用企微后台的「复制」按钮取,手抄必错(853000)。
回复有 24h 窗口:客户超 24h 没来过消息,人工/主动发送不出去是平台规则不是 bug。协议细节见 `docs/技术文档/08`。

**出了 bug 怎么排查**:先 `make check` 定位是哪套测试红;日志都在 stderr(容器里 `docker compose logs -f`);
`make health` 看服务活没活着;历史坑大概率 `docs/技术文档/09` 里有同款。

## 项目全景导航

- 架构与 API 契约:`docs/技术文档/06` · 部署:`04` · 知识模块:`05` · 评测体系:`10`
- **历史决策与坑**:`docs/技术文档/09`(改核心逻辑前必读)· 测试台账:`docs/测试文档/06`
- 双 AI 协作史(如果你也是 AI,这里有前辈们的协作协议):`DISCUSSION.md` · 任务板:`TASKS.md`
