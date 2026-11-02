# Backend

**English** · [中文](README.zh-CN.md)

FastAPI + Pydantic service for PaperTrace. It takes an arXiv link or an uploaded PDF, parses the paper, calls a model, and returns either a credibility report (`/v1/credibility/assessments`) or a five-dimension report (`/v1/assessments`). The HTTP API is described in [docs/api.md](../docs/api.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
[ -f .env ] || cp .env.example .env
make run
```

Open http://127.0.0.1:8000/docs. The example `.env` uses the free `fixture` model and the `stub` parser, so `make run` needs no API key. Five-dimension scoring then returns an example report; the credibility check runs none of its three checks until a real parser and model are configured (`make run-openai` or `make run-deepseek`). Do not overwrite an existing `.env`; it may hold your keys.

## Commands

```bash
make run           # dev server with the settings in .env (SQLite + in-process tasks)
make run-openai    # generic arXiv/PDF parser + OpenAI GPT
make run-deepseek  # generic parser, DeepSeek as default (GPT still selectable)
make run-generic   # same as run-openai
make run-real      # OpenAI + the bundled benchmark parse of paper_001 (2307.08122v3 only)
make test          # pytest
make lint          # ruff
make smoke         # end-to-end run that checks the contract's hard rules (needs make run)
make stop          # stop this project's uvicorn
make docker-up     # full stack: Postgres, Redis, Celery, GROBID
```

Override the port with `make run PORT=8001`.

## Configuration

Everything is set in `.env`; see `.env.example` for every option with comments. For setting up GPT, Claude, Gemini or DeepSeek with your own keys, follow [docs/models.md](../docs/models.md).

| Variable | Values |
|---|---|
| `LLM_PROVIDER` | `fixture` (example output), `openai`, `deepseek`: the default model in the frontend menu |
| `PARSER_PROVIDER` | `stub`, `generic` (any arXiv/PDF), `benchmark` (paper_001 only), `grobid` (reserved) |
| `TASK_RUNNER` | `inline` (FastAPI background tasks) or `celery` |
| `CREDIBILITY_PARSE_SOURCE` | `live` (default: parse the input like the five-dimension flow) or `benchmark` (prefer `../data/data/parsed`, for regression replays) |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | GPT, default `gpt-5.6-terra` |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_ENABLED` | DeepSeek, default `deepseek-flash`; hidden when `DEEPSEEK_ENABLED=false` |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `CLAUDE_ENABLED` | Claude, default `claude-sonnet-5`; listed once a key is set, selectable with `CLAUDE_ENABLED=true` |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_ENABLED` | Gemini, default `gemini-3.7-flash`; listed once a key is set, selectable with `GEMINI_ENABLED=true` |
| `CREDIBILITY_CODE_MAX_SOURCE_READS` | Shared budget of source reads for the code check, default 60 |
| `GITHUB_TOKEN` | Optional; raises the GitHub rate limit for code checks |
| `CROSSREF_MAILTO` | Optional; contact email sent to Crossref in the User-Agent |

Every configured model is listed by `/v1/models`, and the user's choice applies to that job. Claude and Gemini are called through their OpenAI-compatible `/chat/completions` endpoints (`app/llm/compatible.py`), with a capability profile per vendor (strict JSON or not, minimum output budget for thinking models, context limit). Neither has a usable built-in web search, so their code search borrows OpenAI's search; this is recorded in the report, and it needs `OPENAI_API_KEY`. DeepSeek accepts `medium` reasoning effort but maps it to `high`; its native levels are `low`, `high` and `max`. DeepSeek only supports `response_format={"type": "json_object"}`, so structural validity is enforced by `app/validation/`. The OpenAI client uses the Responses API with JSON mode and `store: false`.

## Credibility check

Three checks run in parallel. Each keeps its own status, so one failing lookup does not erase the others' results.

| Check | What it does | Details |
|---|---|---|
| Claims & evidence | Extracts key claims, locates supporting text, tables and figures in the paper, and judges each claim | [live-claims.md](docs/credibility/live-claims.md) |
| References | Parses the reference list, matches entries on Crossref, then arXiv and official publisher pages, then a bounded search agent | [crossref.md](docs/credibility/crossref.md), [reference-auto.md](docs/credibility/reference-auto.md) |
| Code & reproduction | Finds the official repository, samples implementation files, reads public issues and discussions; never runs code | [code-discovery.md](docs/credibility/code-discovery.md), [code-usability.md](docs/credibility/code-usability.md) |

Shared pieces: [external evidence tools](docs/credibility/external-tools.md), [checkpoints and offline replay](docs/credibility/durable-evidence.md) and [provisional scoring](docs/credibility/provisional-scoring.md). Rubric `0.2-provisional-complete` produces trial scores; an explicit `0.1-draft` request stays unscored.

| `schema_version` | Response |
|---|---|
| `credibility-0.1.0-draft` | Original shape |
| `credibility-0.2.0-draft` | Adds coverage, execution and external review |
| `credibility-0.3.0-draft` | Adds the reference verification summary |
| `credibility-0.4.0-draft` | Same fields as 0.3; bounded code-plan completion semantics |
| `credibility-0.5.0-draft` (default) | Adds `code_discovery`: search candidates, provenance and discovery status |

These transport versions do not change the scoring.

Controlling a running check:

- `GET /v1/credibility/assessments/{id}/progress` reports each check's status and the model calls in progress, for the waiting page (`app/credibility/live.py`; display only).
- `POST /v1/credibility/assessments/{id}/checks/{check}/stop` stops one check. It is recorded as stopped by the user (`STOPPED_BY_USER`) and gets the fallback score; the other checks continue. A restart does not run it again.
- `POST /v1/credibility/assessments/{id}/cancel` cancels the whole run (`CANCELLED`, retryable). Model calls run on a helper thread (`app/llm/interrupt.py`), so both stop within about a second instead of waiting for the current call.
- `DELETE /v1/credibility/assessments/{id}` and `DELETE /v1/assessments/{id}` remove finished records; a running record returns 409.
- A five-dimension request may carry `credibility_assessment_id` to mark it as part of the same run. The server stores it and returns it in the detail and history responses.

See [docs/api.md](../docs/api.md) for request and response details.

## Five-dimension scoring

The older flow scores research value, soundness, originality, clarity and reproducibility under field contract v0.4, with a six-item reproducibility checklist. Input handling and the eligibility gate are described in [arxiv-ingestion.zh-CN.md](docs/arxiv-ingestion.zh-CN.md) and [eligibility-and-rubric.zh-CN.md](docs/eligibility-and-rubric.zh-CN.md); the analysis prompt in [llm-analysis.md](docs/llm-analysis.md).

Rules the backend enforces:

- Overall score, rating and `is_provisional` are always recomputed by the backend; values returned by the model are discarded.
- `dimensions` always has 5 entries and `reproducibility_checklist` always has 6. Gaps are filled with `uncertain` or `unable_to_verify`.
- Every evidence location must be traceable. Body text cites `chunk_id`s, and tables and figures cite parsed element labels. Anything that fails the lookup gets lower confidence, out-of-range page numbers become `null`, and the parser's own `location_confidence` is a ceiling. No invented page numbers or quotes.

## Code map

- `app/api/`: HTTP routes.
- `app/credibility/`: the three checks, external lookups, live progress, scoring and the task runner.
- `app/eligibility/`: the machine-learning paper gate.
- `app/llm/`: model clients (OpenAI, DeepSeek, OpenAI-compatible for Claude and Gemini), prompts, retry, repair and interruptible calls.
- `app/parsing/`: arXiv download, PDF parsing, location anchors and the benchmark loader.
- `app/pipeline/orchestrator.py`: the five-dimension flow from input to report.
- `app/validation/`: evidence verification, dimension degradation, cross-checks and overall score.
- `app/schemas/`, `app/storage/`, `app/tracking/`: types, tables and model-call logs.
- `scripts/`: verification and replay tools for each check.

The backend reads the benchmark in `../data` directly: `load_parsed_document("../data/data/parsed/paper_001/v3").build_index()` feeds `validate_report_payload(source_index=...)`. If the benchmark changes a field shape, `tests/test_parsing_benchmark.py` fails first. Tests that need the benchmark are skipped when `../data` is missing.

## Privacy

Never commit `.env`, API keys, paper PDFs or uploaded files. Runtime files (SQLite database, uploads, arXiv cache) live in `backend/data/`, which is ignored by git.
