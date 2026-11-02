# 后端

[English](README.md) · **中文**

PaperTrace 的 FastAPI + Pydantic 服务。输入 arXiv 链接或上传的 PDF，后端解析论文、调用模型，返回可信度报告（`/v1/credibility/assessments`）或五维评分报告（`/v1/assessments`）。HTTP 接口见 [docs/api.zh-CN.md](../docs/api.zh-CN.md)。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
[ -f .env ] || cp .env.example .env
make run
```

打开 http://127.0.0.1:8000/docs。示例 `.env` 使用免费的 `fixture` 模型和 `stub` 解析器，`make run` 不需要任何 API key。此时五维评分返回示例报告；可信度核查要配置真实解析器和模型（`make run-openai` 或 `make run-deepseek`）后才会运行三项检查。已有的 `.env` 不要覆盖，里面可能有你的 key。

## 命令

```bash
make run           # 按 .env 启动开发服务器（SQLite + 进程内任务）
make run-openai    # 通用 arXiv/PDF 解析 + OpenAI GPT
make run-deepseek  # 通用解析，DeepSeek 为默认（仍可选 GPT）
make run-generic   # 与 run-openai 相同
make run-real      # OpenAI + 仓库自带的 paper_001 解析结果（只接受 2307.08122v3）
make test          # pytest
make lint          # ruff
make smoke         # 端到端检查契约硬规则（需要先 make run）
make stop          # 停掉本项目的 uvicorn
make docker-up     # 完整栈：Postgres、Redis、Celery、GROBID
```

端口可以覆盖：`make run PORT=8001`。

## 配置

全部写在 `.env` 里，`.env.example` 列出了每个选项和注释。用自己的 key 配置 GPT、Claude、Gemini 或 DeepSeek，按 [docs/models.zh-CN.md](../docs/models.zh-CN.md) 操作。

| 变量 | 取值 |
|---|---|
| `LLM_PROVIDER` | `fixture`（示例输出）、`openai`、`deepseek`：前端模型菜单的默认项 |
| `PARSER_PROVIDER` | `stub`、`generic`（任意 arXiv/PDF）、`benchmark`（仅 paper_001）、`grobid`（预留） |
| `TASK_RUNNER` | `inline`（FastAPI 后台任务）或 `celery` |
| `CREDIBILITY_PARSE_SOURCE` | `live`（默认，与五维相同，现场解析输入）或 `benchmark`（优先读 `../data/data/parsed`，用于回归重放） |
| `OPENAI_API_KEY`、`OPENAI_MODEL` | GPT，默认 `gpt-5.6-terra` |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_ENABLED` | DeepSeek，默认 `deepseek-flash`；`DEEPSEEK_ENABLED=false` 时不列出 |
| `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`CLAUDE_ENABLED` | Claude，默认 `claude-sonnet-5`；配置 key 后列出，`CLAUDE_ENABLED=true` 后可选 |
| `GEMINI_API_KEY`、`GEMINI_MODEL`、`GEMINI_ENABLED` | Gemini，默认 `gemini-3.7-flash`；配置 key 后列出，`GEMINI_ENABLED=true` 后可选 |
| `CREDIBILITY_CODE_MAX_SOURCE_READS` | 代码检查共享的来源读取上限，默认 60 |
| `GITHUB_TOKEN` | 可选，提高代码检查的 GitHub 限流额度 |
| `CROSSREF_MAILTO` | 可选，放在 User-Agent 里发给 Crossref 的联系邮箱 |

配置好的模型都会出现在 `/v1/models` 里，用户的选择只作用于当次任务。Claude 和 Gemini 通过各自 OpenAI 兼容的 `/chat/completions` 接口调用（`app/llm/compatible.py`），每个厂商有一份能力档案（是否严格 JSON、思考模型的最低输出额度、上下文上限）。两者都没有可用的内置联网搜索，代码检索借用 OpenAI 的搜索，报告里如实记录，因此也需要 `OPENAI_API_KEY`。DeepSeek 接受 `medium` 推理强度但实际映射为 `high`，原生档位只有 `low`、`high`、`max`。DeepSeek 只支持 `response_format={"type": "json_object"}`，结构正确性由 `app/validation/` 保证。OpenAI 客户端使用 Responses API 的 JSON 模式，并设置 `store: false`。

## 可信度核查

三项检查并行运行，各自保留状态，一项外部查询失败不会抹掉其他检查的结果。

| 检查 | 做什么 | 详细说明 |
|---|---|---|
| 主张与证据 | 抽取关键主张，在论文里定位支持它的正文、表格和图，并逐条判断 | [live-claims.md](docs/credibility/live-claims.md) |
| 参考文献 | 解析参考文献列表，先查 Crossref，再查 arXiv 和出版方官方页面，最后交给有预算限制的搜索 agent | [crossref.md](docs/credibility/crossref.md)、[reference-auto.md](docs/credibility/reference-auto.md) |
| 代码与复现 | 寻找官方仓库，抽查实现文件，阅读公开 issue 和讨论；从不运行代码 | [code-discovery.md](docs/credibility/code-discovery.md)、[code-usability.md](docs/credibility/code-usability.md) |

共用部分：[外部证据工具](docs/credibility/external-tools.md)、[检查点与离线重放](docs/credibility/durable-evidence.md)、[试行计分](docs/credibility/provisional-scoring.md)。Rubric `0.2-provisional-complete` 给出试验分；显式请求 `0.1-draft` 时不出分。

| `schema_version` | 响应 |
|---|---|
| `credibility-0.1.0-draft` | 最初的结构 |
| `credibility-0.2.0-draft` | 增加覆盖率、执行记录和外部审阅 |
| `credibility-0.3.0-draft` | 增加引用核实汇总 |
| `credibility-0.4.0-draft` | 字段与 0.3 相同，补充代码计划完成语义 |
| `credibility-0.5.0-draft`（默认） | 增加 `code_discovery`：搜索候选、来源和发现状态 |

这些传输版本不影响计分。

运行中的控制：

- `GET /v1/credibility/assessments/{id}/progress`：三项检查各自的状态和进行中的模型调用，供等待页显示（`app/credibility/live.py`，只读）。
- `POST /v1/credibility/assessments/{id}/checks/{check}/stop`：只停止一项检查，记为用户停止（`STOPPED_BY_USER`）并按兜底分计分，其余检查继续；服务重启后不会重新执行。
- `POST /v1/credibility/assessments/{id}/cancel`：取消整次运行（`CANCELLED`，可重试）。模型调用在辅助线程上进行（`app/llm/interrupt.py`），所以停止和取消都在约 1 秒内生效，不用等当前调用返回。
- `DELETE /v1/credibility/assessments/{id}` 和 `DELETE /v1/assessments/{id}`：删除已结束的记录，运行中的返回 409。
- 五维评分请求可以带 `credibility_assessment_id`，标明属于同一次运行；服务端会记录，并在详情和历史列表中返回。

请求与响应细节见 [docs/api.zh-CN.md](../docs/api.zh-CN.md)。

## 五维评分

较早的流程按字段合同 v0.4 给研究价值、可靠性、原创性、清晰度和可复现性打分，并附 6 项可复现清单。输入处理和论文门控见 [arxiv-ingestion.zh-CN.md](docs/arxiv-ingestion.zh-CN.md) 和 [eligibility-and-rubric.zh-CN.md](docs/eligibility-and-rubric.zh-CN.md)，分析提示词见 [llm-analysis.md](docs/llm-analysis.md)。

后端强制执行的规则：

- 总分、评级和 `is_provisional` 一律由后端重算，模型返回的值直接丢弃。
- `dimensions` 固定 5 项，`reproducibility_checklist` 固定 6 项，缺的用 `uncertain` 或 `unable_to_verify` 补齐。
- 每个证据位置都必须可追溯：正文引用 `chunk_id`，表格和图引用解析出的元素标签。回查失败的降低置信度，超出范围的页码改为 `null`，解析器自己的 `location_confidence` 是上限。不编造页码或引文。

## 代码位置

- `app/api/`：HTTP 路由。
- `app/credibility/`：三项检查、外部查询、实时进度、计分和任务执行。
- `app/eligibility/`：机器学习论文门控。
- `app/llm/`：模型客户端（OpenAI、DeepSeek，以及 Claude 和 Gemini 用的 OpenAI 兼容客户端）、提示词、重试、修复和可中断调用。
- `app/parsing/`：arXiv 下载、PDF 解析、位置锚点和 benchmark 读取。
- `app/pipeline/orchestrator.py`：五维评分从输入到报告的流程。
- `app/validation/`：证据校验、维度降级、交叉校验和总分。
- `app/schemas/`、`app/storage/`、`app/tracking/`：类型、数据表和模型调用日志。
- `scripts/`：各项检查的验证与重放工具。

后端直接读取 `../data` 里的 benchmark：`load_parsed_document("../data/data/parsed/paper_001/v3").build_index()` 的结果可直接传给 `validate_report_payload(source_index=...)`。benchmark 的字段形状一变，`tests/test_parsing_benchmark.py` 会先报错。缺少 `../data` 时，依赖它的测试会自动跳过。

## 隐私

不要提交 `.env`、API key、论文 PDF 或上传的文件。运行时文件（SQLite 数据库、上传文件、arXiv 缓存）放在 `backend/data/`，已被 git 忽略。
