# wecom-group-agent —— 纯标准库,无 pip 依赖,一层 COPY 即可运行。
#
#   docker build -t wecom-group-agent .
#   docker run --rm --env-file .env -e MOCK=1 -it wecom-group-agent            # 本地模拟
#   docker compose up -d                                                        # 生产:agent + api 双服务
#
# 同一镜像跑两种进程:main.py(agent)与 api_server.py(运营 API),由 command 区分。
FROM python:3.12-slim

# 运行期基本卫生:不写 .pyc、日志不缓冲(容器里 stderr 直达 docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 显式 COPY 白名单(而非 COPY . ),从根上杜绝 .env/.git/data 进镜像
COPY core/ core/
COPY adapters/ adapters/
COPY prompts/ prompts/
COPY examples/ examples/
COPY main.py api_server.py ./

# data/ 是运行时可写目录(sqlite 会话库、KF token 共享文件、RAG 索引缓存),
# 生产用卷挂载覆盖;这里预建并授权给非 root 用户。
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/data \
    && chown -R agent:agent /app
USER agent

# 9000 = 微信客服回调(ADAPTER=kf,监听 0.0.0.0);8080 = 运营 REST API
EXPOSE 9000 8080

# 默认跑 agent 主进程;api 服务在 compose 里用 command 覆盖
CMD ["python3", "main.py"]
