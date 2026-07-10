# wecom-group-agent 常用命令入口。纯标准库项目,make 之外无任何构建依赖。
.DEFAULT_GOAL := help

PY ?= python3

.PHONY: help test eval compile run-mock demo api console health docker-build docker-up docker-down docker-logs

help: ## 列出所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

test: ## 跑全部离线测试(与 CI 相同)
	@pass=0; fail=0; \
	for t in tests/test_*.py; do \
		if $(PY) "$$t" >/dev/null 2>&1; then pass=$$((pass+1)); \
		else fail=$$((fail+1)); echo "FAIL: $$t"; $(PY) "$$t"; fi; \
	done; \
	echo "测试: $$pass 绿 / $$fail 红"; [ $$fail -eq 0 ]

eval: ## 跑离线评测集(知识检索质量,无需网络/密钥)
	$(PY) evals/run_evals.py

eval-online: ## 跑在线评测(真 LLM 回复质量,需 .env 里的 LLM_API_KEY)
	$(PY) evals/run_evals.py --online

compile: ## 全量字节编译 + import 冒烟(快速语法/依赖检查)
	$(PY) -m compileall -q core adapters tests evals main.py api_server.py admin_console.py examples
	$(PY) -c "import main, api_server, admin_console; from adapters.wecom_kf import WecomKfAdapter; print('import 冒烟 OK')"

run-mock: ## 本地交互模拟(Mac 可跑,MOCK 适配器 + 真 LLM,需 .env)
	MOCK=1 $(PY) main.py

demo: ## 17 个真实场景离线演示(需 .env 里的 LLM_API_KEY)
	$(PY) examples/demo_agent.py

api: ## 启动运营 REST API(默认 127.0.0.1:8080)
	$(PY) api_server.py

console: ## 启动 Web 工作台(默认 127.0.0.1:8090 → API :8080)
	$(PY) admin_console.py

health: ## 一键健康检查:api/console/agent 三服务 ready(compose up 后可加 -- --wait 60)
	$(PY) healthcheck.py

docker-build: ## 构建镜像
	docker build -t wecom-group-agent .

docker-up: ## 生产三服务起飞(agent + api + console,需 .env)
	docker compose up -d --build

docker-down: ## 停掉双服务
	docker compose down

docker-logs: ## 跟踪双服务日志
	docker compose logs -f
