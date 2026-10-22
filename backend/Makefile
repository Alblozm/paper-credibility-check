.PHONY: install dev test lint run run-real run-generic run-openai run-deepseek smoke stop port docker-up docker-down

# 端口可覆盖：make run PORT=8001
PORT ?= 8000

install:
	python3 -m pip install -e ".[dev]"

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check app tests

run:
	python3 -m uvicorn app.main:app --reload --port $(PORT)

# 真实单篇试跑：强制 OpenAI GPT + paper_001 benchmark；API Key 仍从 .env 读取
run-real:
	LLM_PROVIDER=openai PARSER_PROVIDER=benchmark python3 -m uvicorn app.main:app --reload --port $(PORT)

# 通用流程：真实 arXiv/PDF 结构化，默认 GPT，DeepSeek 可在页面选择
run-generic:
	LLM_PROVIDER=openai PARSER_PROVIDER=generic python3 -m uvicorn app.main:app --reload --port $(PORT)

# 内部测试：默认模型为 DeepSeek（页面仍可切换到 GPT）；API Key 从 .env 读取
run-deepseek:
	LLM_PROVIDER=deepseek DEEPSEEK_ENABLED=true PARSER_PROVIDER=generic python3 -m uvicorn app.main:app --reload --port $(PORT)

# 通用解析 + OpenAI GPT 评估；API Key 和模型名从 .env 读取
run-openai:
	LLM_PROVIDER=openai PARSER_PROVIDER=generic python3 -m uvicorn app.main:app --reload --port $(PORT)

# 端到端冒烟：建任务 → 轮询 → 检查契约硬规则（需要先 make run）
smoke:
	@./scripts/smoke.sh $(PORT)

# 看看谁占着端口（uvicorn --reload 起父子两个进程，Ctrl-C 没收干净时子进程会赖着）
port:
	@lsof -i :$(PORT) || echo "端口 $(PORT) 空闲"

# 只杀本项目的 uvicorn，不动别的进程
stop:
	@pkill -f "uvicorn app.main:app" && echo "已停止" || echo "没有在跑的实例"

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v
