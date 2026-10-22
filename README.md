# Paper Credibility Check

**English** · [中文](README.zh-CN.md)

Paper Credibility Check reviews a machine learning paper and shows which parts deserve a closer look. Give it an arXiv ID, an arXiv link or a PDF. It runs three independent checks and ties every finding back to the paper or to an external source:

- **Claims & evidence**: extracts the paper's key claims and checks whether the paper's own results, tables and figures support them.
- **References**: parses the reference list and verifies each entry against Crossref, arXiv and official publisher pages.
- **Code & reproduction**: finds the official code repository, looks for implementation evidence, and reads public issues and discussions for third-party reproduction reports. No repository code is executed.

The same app also offers **Paper scoring**, a five-dimension assessment (research value, soundness, originality, clarity, reproducibility) with page-level evidence. **Reports** lists every saved check and score.

> Scores are AI-generated trial scores under the provisional rubric `0.2-provisional-complete`. They are meant to point a reader to evidence, not to judge an author's integrity.

## Repository layout

```
paper-credibility-check/
├── frontend/   React + TypeScript + Vite web app
├── backend/    FastAPI service: parsing, model calls, the three checks, scoring
├── data/       Benchmark: 50 structured ICLR papers, rubrics, schemas, SQL seeds
└── docs/       API reference
```

```
browser ─▶ frontend (:5173) ──/v1──▶ backend (:8000) ─▶ OpenAI / DeepSeek
                                          │           └▶ arXiv · Crossref · GitHub
                                          └─▶ data/ (parsed papers, rubrics)
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

To try the interface without the backend, run `VITE_API_MODE=mock npm run dev` instead. Mock mode replays fixed scenarios from `public/mock-data.js`.

### 3. Real models

Add a key to `backend/.env` and start the backend with a real parser:

| Command | Default model | Needs |
|---|---|---|
| `make run-openai` | GPT (`gpt-5.6-terra`) | `OPENAI_API_KEY` |
| `make run-deepseek` | DeepSeek (`deepseek-flash`) | `DEEPSEEK_API_KEY` |

When both keys are set, both models appear in the model menu. A full credibility check usually takes 5 to 25 minutes. Most of that time goes to reference verification, because Crossref allows one request per second. Five-dimension scoring takes about a minute. `GITHUB_TOKEN` and `CROSSREF_MAILTO` are optional and raise the rate limits of those services.

Keep API keys in `backend/.env` only. Never put them in the frontend or in any `VITE_` variable.

## Tests

```bash
cd frontend && npm test && npm run lint && npm run build
cd backend  && make lint && make test
cd data     && python3 -m pytest tests && make validate
```

CI runs the same checks (`.github/workflows/ci.yml`).

## Documentation

| Topic | Where |
|---|---|
| HTTP API, error codes, polling | [docs/api.md](docs/api.md) |
| Frontend structure and mock mode | [frontend/README.md](frontend/README.md) |
| Backend configuration, run modes, code map | [backend/README.md](backend/README.md) |
| How each credibility check works | [backend/docs/credibility/](backend/docs/credibility/) |
| Benchmark data, rubrics, SQL | [data/README.md](data/README.md) |
| Changes | [CHANGELOG.md](CHANGELOG.md) |

## Privacy

Do not commit `.env` files, API keys, paper PDFs or uploaded files. Public paper PDFs are referenced by URL, version and SHA-256 checksum instead of being stored in the repository.
