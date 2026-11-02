# PaperTrace

**English** · [中文](README.zh-CN.md)

PaperTrace checks the credibility of a machine learning paper and shows which parts deserve a closer look. Give it an arXiv ID, an arXiv link or a PDF. It runs three independent checks and ties every finding back to the paper or to an external source:

- **Claims & evidence**: extracts the paper's key claims and checks whether the paper's own results, tables and figures support them.
- **References**: parses the reference list and verifies each entry against Crossref, arXiv and official publisher pages.
- **Code & reproduction**: finds the official code repository, looks for implementation evidence, and reads public issues and discussions for third-party reproduction reports. No repository code is executed.

The same run can also produce a **five-dimension quality assessment** (research value, soundness, originality, clarity, reproducibility) with page-level evidence. Reports open on the paper itself, with evidence highlighted in the PDF, or as a document. **History** keeps every run, and a small **help assistant** answers questions about using the app.

You can choose GPT, Claude, Gemini or DeepSeek for each run, stop a single slow check or cancel a whole run, and export a report as PDF or JSON.

> Scores are AI-generated trial scores under the provisional rubric `0.2-provisional-complete`. They are meant to point a reader to evidence, not to judge an author's integrity.

## Repository layout

```
paper-credibility-check/
├── frontend/   React + TypeScript + Vite web app, plus the optional help assistant service
├── backend/    FastAPI service: parsing, model calls, the three checks, scoring
├── data/       Benchmark: 50 structured ICLR papers, rubrics, schemas, SQL seeds
└── docs/       API reference
```

```
browser ─▶ frontend (:5173) ──/v1──▶ backend (:8000) ─▶ GPT · Claude · Gemini · DeepSeek
              │                           │           └▶ arXiv · Crossref · GitHub
              │                           └─▶ data/ (parsed papers, rubrics)
              └──/assistant──▶ help assistant (:8020, optional)
```

## Quick start

You need Python 3.10+ and Node.js 20.19+ (or 22.12+).

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # defaults to the free fixture model
make run                    # http://127.0.0.1:8000/docs
```

The default `.env` uses `LLM_PROVIDER=fixture` and `PARSER_PROVIDER=stub`, so it needs no API key. Paper scoring returns an example report. The credibility check only shows the report layout in this mode, because its three checks need a real parser and model (step 3).

### 2. Frontend

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

Open http://127.0.0.1:5173. The dev server forwards `/v1` requests to `http://127.0.0.1:8000`.

To try the interface without the backend, run `VITE_API_MODE=mock npm run dev` instead. Mock mode uses the sample data in `public/mock-data.js`.

The help assistant is optional: `npm run assistant` starts it on port 8020 using the `OPENAI_*` settings from `backend/.env`. See [frontend/README.md](frontend/README.md).

### 3. Real models

PaperTrace works with GPT, Claude, Gemini and DeepSeek. Bring your own key from each vendor you want to use; one is enough. In `backend/.env`, switch on real parsing and add the keys:

```ini
PARSER_PROVIDER=generic
LLM_PROVIDER=openai          # model selected by default: openai or deepseek

OPENAI_API_KEY=...           # GPT
ANTHROPIC_API_KEY=...        # Claude, together with:
CLAUDE_ENABLED=true
GEMINI_API_KEY=...           # Gemini, together with:
GEMINI_ENABLED=true
DEEPSEEK_API_KEY=...         # DeepSeek
```

Restart with `make run`. Each model with a key can then be picked in the model menu; Claude and Gemini also need their switch. Claude and Gemini use OpenAI's web search for the code check, so an OpenAI key is recommended alongside them. **[docs/models.md](docs/models.md)** covers each vendor: where to get the key, default model names, and what happens when a key is missing.

A full credibility check usually takes 5 to 15 minutes, sometimes longer. Most of that time goes to reference verification, because Crossref allows one request per second. Five-dimension scoring takes about a minute. `GITHUB_TOKEN` and `CROSSREF_MAILTO` are optional and raise the rate limits of those services.

Keep API keys in `backend/.env` only. Never put them in the frontend or in any `VITE_` variable.

## Tests

```bash
cd frontend && npm test && npm run lint && npm run assistant:check && npm run build
cd backend  && make lint && make test
cd data     && python3 -m pytest tests && make validate
```

CI runs the same checks (`.github/workflows/ci.yml`).

## Documentation

| Topic | Where |
|---|---|
| Model setup: keys for GPT, Claude, Gemini, DeepSeek | [docs/models.md](docs/models.md) |
| HTTP API, error codes, polling | [docs/api.md](docs/api.md) |
| Frontend pages, mock mode, help assistant | [frontend/README.md](frontend/README.md) |
| Design system | [frontend/docs/design/DESIGN_SYSTEM.zh-CN.md](frontend/docs/design/DESIGN_SYSTEM.zh-CN.md) |
| Backend configuration, run modes, code map | [backend/README.md](backend/README.md) |
| How each credibility check works | [backend/docs/credibility/](backend/docs/credibility/) |
| Benchmark data, rubrics, SQL | [data/README.md](data/README.md) |
| Changes | [CHANGELOG.md](CHANGELOG.md) |

## Privacy

Do not commit `.env` files, API keys, paper PDFs or uploaded files. Public paper PDFs are referenced by URL, version and SHA-256 checksum instead of being stored in the repository.
