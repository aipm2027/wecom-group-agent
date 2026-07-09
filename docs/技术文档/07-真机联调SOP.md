# 07 · 微信客服真机联调 SOP

> 路线图 P1-2。代码侧已就绪(加解密 FIPS-197 钉住、18 项离线测试、防丢消息/token 自愈),本文是把它接上腾讯真实服务器的**操作手册**。预计耗时:云服务器路径 ~40 分钟,本地穿透路径 ~20 分钟。
>
> 适配器内部机制(回调流程/加解密等价性/环境变量含义)见 [04-适配器与部署](04-适配器与部署.md) 第 4 节,本文不重复。

## 0. 前置条件

| 条件 | 说明 |
|------|------|
| 企业微信管理员权限 | 需要进企微管理后台配置微信客服与回调 |
| 一个公网 HTTPS 入口 | 腾讯回调**只支持 https 域名**;二选一:云服务器+域名(生产)或内网穿透(开发联调) |
| 企业可信 IP | 企微新规:调用 API 的服务器出口 IP 需加入应用「企业可信 IP」,否则 `gettoken`/`sync_msg` 报 60020 |

## 1. 腾讯侧准备(企微管理后台)

1. **开通微信客服**:企微管理后台 → 应用管理 → 微信客服(或 kf.weixin.qq.com);创建至少一个客服账号,记下要接管的账号。
2. **拿三样东西**:
   - `WECOM_CORP_ID`:我的企业 → 企业信息 → 企业 ID;
   - `WECOM_KF_SECRET`:微信客服应用详情 → Secret(查看需管理员扫码);
   - 回调侧的 `Token` 与 `EncodingAESKey`:微信客服 → API → 「回调地址」配置页,点「随机获取」生成(**EncodingAESKey 固定 43 位**),先复制保存,**页面先不要点确定**(要等我们的服务先起来,否则 URL 验证失败保存不了)。
3. **配置企业可信 IP**:微信客服应用详情 → 企业可信 IP → 填部署机的公网出口 IP。

## 2. 网络路径(二选一)

### 路径 A:云服务器 + 域名(生产推荐)

```
微信用户 → 腾讯 → https://kf.你的域名.com/wecom/callback → Caddy/Nginx(443,TLS 终止) → 127.0.0.1:9000(agent)
```

1. 轻量云服务器(2C2G 足够)+ 已备案域名解析到服务器;
2. `git clone` 本仓库,`cp .env.example .env` 按第 3 节填好;
3. `docker compose up -d`(三服务:agent 9000 / api 8080 回环 / console 8090 回环);
4. 反代把 443 的 `/wecom/callback` 转给 `127.0.0.1:9000`(Caddy 两行:`kf.你的域名.com { reverse_proxy /wecom/callback* 127.0.0.1:9000 }`,证书自动);
5. 运营后台不暴露公网:SSH 隧道 `ssh -L 8090:127.0.0.1:8090 服务器` 后本地浏览器开 `http://127.0.0.1:8090`。

### 路径 B:本地 + 内网穿透(开发联调)

```
腾讯 → https://随机子域.trycloudflare.com/wecom/callback → cloudflared → 本机 9000
```

1. `cloudflared tunnel --url http://127.0.0.1:9000`(免费、免注册,启动后打印公网 https 地址);frp/ngrok 同理;
2. 本机 `ADAPTER=kf HANDLER=echo python3 main.py`;
3. 把打印出的 `https://xxx.trycloudflare.com/wecom/callback` 填到企微回调配置。
   ⚠️ 免费穿透域名每次重启会变,仅用于联调;可信 IP 填本机出口 IP(`curl ifconfig.me`)。

## 3. .env 填写(逐项)

```bash
ADAPTER=kf
HANDLER=echo                       # 联调先用 echo(不烧 LLM);通了再改 llm
WECOM_CORP_ID=ww………                # 企业 ID
WECOM_KF_SECRET=…                  # 微信客服 Secret
WECOM_CALLBACK_TOKEN=…             # 回调配置页的 Token
WECOM_ENCODING_AES_KEY=…           # 43 位,少一位都会被启动自检/适配器拒绝
# WECOM_CALLBACK_PORT=9000         # 默认即可,反代/穿透指到这个端口
# WECOM_CALLBACK_PATH=/wecom/callback
# WECOM_TOKEN_FILE=data/wecom_kf_token.json  # 多进程部署(compose)保持默认
```

> 漏配任何一项,`main.py` 启动自检会列出缺项并拒绝启动(路线图 P0),不会出现"启动正常但收不到消息"。

## 4. 联调步骤(严格按序)

| 步 | 操作 | 期望现象 | 不符时看 |
|----|------|----------|----------|
| 1 | 启动 agent(`ADAPTER=kf HANDLER=echo`) | stderr 打印回调服务监听 9000 | 端口占用/自检报错 |
| 2 | 企微回调配置页点「保存」 | 页面保存成功(腾讯发 GET 验证,适配器解密 echostr 回显) | §5-Q1/Q2 |
| 3 | 用个人微信扫客服账号二维码,发「你好」 | ≤5 秒收到 `收到：你好`(echo 复读) | §5-Q3 |
| 4 | `.env` 改 `HANDLER=llm`(需 LLM_API_KEY),重启 | 收到导购人设的真实回复 | LLM 日志/自检 |
| 5 | 发「转人工」类消息;开 console(8090) | 会话进「待人工」收件箱;接管后 AI 静默,人工回复能下发到微信 | api/console 日志 |
| 6 | `kill`(SIGTERM)后重启 agent,再发一条 | 正常回复且**不重复回复旧消息**(cursor 持久化生效) | data/ 卷是否挂对 |

## 5. 常见故障速查

| 症状 | 最可能原因 | 处理 |
|------|-----------|------|
| Q1 保存回调时报「URL 验证失败」 | Token/AESKey 与 .env 不一致;或服务没起/反代路径没对上 | 三处(腾讯页面、.env、进程实际加载)逐一核对后重启再保存 |
| Q2 验证通过但日志报解密失败 | EncodingAESKey 复制少字符(必须 43 位);receiveid 不匹配(CORP_ID 填错) | 重新「随机获取」并同步两侧 |
| Q3 发消息无回复,日志无回调 | 可信 IP 未配(60020);穿透隧道断了;回调 URL 域名变了(免费穿透重启) | 企微后台查「API 调用异常」记录;重配可信 IP/回调 URL |
| Q4 有回调但不回复 | 消息被过滤:适配器只处理 `origin=3`(微信客户消息),客服人员自己发的话术不会触发 | 用**个人微信**发,别用客服工作台发 |
| Q5 回复发送失败(send_msg errcode≠0) | token 过期(会自愈重试一次)/可信 IP/账号未分配接待人员 | 看 stderr 的 errcode 对照官方错误码 |
| Q6 重启后重复回复旧消息 | `data/` 未持久化(容器没挂卷),cursor 丢了 | compose 已内置 `agent-data` 卷;裸跑确保 data/ 可写 |

## 6. 验收标准(路线图 P1-2)

- [ ] 回调 URL 验证一次通过;
- [ ] 真实微信用户消息 ≤5 秒收到 AI 回复;
- [ ] 处理失败回 500,腾讯重试后消息不丢;
- [ ] token 过期自动重取(观察连续运行 >2 小时);
- [ ] 重启不重复回复(cursor 持久化);
- [ ] 转人工 → console 接管 → 人工回复 → 交回,全链路在真机走通。

> 以上跑通即可在 [产品化路线图](../产品化路线图.md) 勾掉 P1-2,并把 README「当前进度」的"真机联调待验证"改为已验证(注明日期)。
