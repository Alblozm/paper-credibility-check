# PaperTrace

[English](README.md) · **中文**

PaperTrace 用来核查一篇机器学习论文的可信度，并指出哪些地方值得进一步查看。输入 arXiv ID、arXiv 链接或 PDF，系统会并行做三项独立检查，每条结论都能回到论文原文或外部来源：

- **主张与证据**：抽取论文的关键主张，核对正文里的结果、表格和图是否真的支持这些主张。
- **参考文献**：解析参考文献列表，逐条到 Crossref、arXiv 和出版方官方页面核实。
- **代码与复现**：寻找官方代码仓库和实现证据，并阅读公开的 issue 与讨论，寻找第三方复现反馈。不会运行仓库里的代码。

同一次运行还可以并行做**五维质量评估**（研究价值、可靠性、原创性、清晰度、可复现性），每个维度都附带页码级证据。报告既可以在论文原文上看（证据直接在 PDF 里高亮），也可以按文档阅读。**History** 保存每一次运行，右下角的**帮助助手**回答使用上的问题。

每次运行都可以在 GPT、Claude、Gemini、DeepSeek 中选择模型；可以单独停掉一项跑得太久的检查，也可以取消整次运行；报告可以导出为 PDF 或 JSON。

> 分数是 AI 在试行规则 `0.2-provisional-complete` 下给出的试验分，用来把读者引向证据，不是对作者诚信的判断。

## 仓库结构

```
paper-credibility-check/
├── frontend/   React + TypeScript + Vite 前端，以及可选的帮助助手服务
├── backend/    FastAPI 后端：解析、模型调用、三项核查、计分
├── data/       基准数据：50 篇结构化 ICLR 论文、Rubric、Schema、SQL 种子
└── docs/       接口文档
```

```
浏览器 ─▶ frontend (:5173) ──/v1──▶ backend (:8000) ─▶ GPT · Claude · Gemini · DeepSeek
            │                           │           └▶ arXiv · Crossref · GitHub
            │                           └─▶ data/（解析好的论文、Rubric）
            └──/assistant──▶ 帮助助手 (:8020，可选)
```

## 快速开始

需要 Python 3.10 及以上，以及 Node.js 20.19 及以上（或 22.12 及以上）。

### 1. 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # 默认使用免费的 fixture 模型
make run                    # http://127.0.0.1:8000/docs
```

默认 `.env` 是 `LLM_PROVIDER=fixture`、`PARSER_PROVIDER=stub`，不需要 API key。五维评分会返回示例报告；可信度核查在这个模式下只显示报告版面，因为三项检查需要真实的解析器和模型（见第 3 步）。

### 2. 前端

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 http://127.0.0.1:5173。开发服务器会把 `/v1` 请求转发到 `http://127.0.0.1:8000`。

不启动后端也可以看界面：改用 `VITE_API_MODE=mock npm run dev`，Mock 模式使用 `public/mock-data.js` 里的示例数据。

帮助助手是可选的：`npm run assistant` 会在 8020 端口启动它，并沿用 `backend/.env` 里的 `OPENAI_*` 设置。详见 [frontend/README.zh-CN.md](frontend/README.zh-CN.md)。

### 3. 接入真实模型

PaperTrace 支持 GPT、Claude、Gemini 和 DeepSeek。想用哪家就填哪家你自己的官方 key，配一家就能用。在 `backend/.env` 里打开真实解析并填上 key：

```ini
PARSER_PROVIDER=generic
LLM_PROVIDER=openai          # 页面默认选中的模型：openai 或 deepseek

OPENAI_API_KEY=...           # GPT
ANTHROPIC_API_KEY=...        # Claude，还要同时设置：
CLAUDE_ENABLED=true
GEMINI_API_KEY=...           # Gemini，还要同时设置：
GEMINI_ENABLED=true
DEEPSEEK_API_KEY=...         # DeepSeek
```

用 `make run` 重启后端。填了 key（Claude、Gemini 还要打开开关）的模型都会出现在模型菜单里。Claude 和 Gemini 做代码检查时借用 OpenAI 的联网搜索，建议同时配一个 OpenAI key。每家去哪申请 key、默认模型叫什么、缺 key 时会怎样，见 **[docs/models.zh-CN.md](docs/models.zh-CN.md)**。

一次完整的可信度核查通常需要 5 到 15 分钟，有时更久，大部分时间花在引用核实上，因为 Crossref 限制每秒 1 次请求。五维评分大约 1 分钟。`GITHUB_TOKEN` 和 `CROSSREF_MAILTO` 可选，填了可以提高这两个服务的限流额度。

API key 只放在 `backend/.env`，不要放进前端，也不要放进任何 `VITE_` 变量。

## 测试

```bash
cd frontend && npm test && npm run lint && npm run assistant:check && npm run build
cd backend  && make lint && make test
cd data     && python3 -m pytest tests && make validate
```

CI 跑的是同一套检查（`.github/workflows/ci.yml`）。

## 文档

| 内容 | 位置 |
|---|---|
| 模型配置：GPT、Claude、Gemini、DeepSeek 的 key | [docs/models.zh-CN.md](docs/models.zh-CN.md) |
| HTTP 接口、错误码、轮询 | [docs/api.zh-CN.md](docs/api.zh-CN.md) |
| 前端页面、Mock 模式、帮助助手 | [frontend/README.zh-CN.md](frontend/README.zh-CN.md) |
| 设计规范 | [frontend/docs/design/DESIGN_SYSTEM.zh-CN.md](frontend/docs/design/DESIGN_SYSTEM.zh-CN.md) |
| 后端配置、运行模式、代码地图 | [backend/README.zh-CN.md](backend/README.zh-CN.md) |
| 各项可信度核查的实现 | [backend/docs/credibility/](backend/docs/credibility/) |
| 基准数据、Rubric、SQL | [data/README.zh-CN.md](data/README.zh-CN.md) |
| 版本记录 | [CHANGELOG.md](CHANGELOG.md) |

## 隐私

不要提交 `.env`、API key、论文 PDF 或用户上传的文件。公开论文的 PDF 只记录 URL、版本和 SHA-256 校验值，不存进仓库。
