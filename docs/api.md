# HTTP API

**English** · [中文](api.zh-CN.md)

The backend listens on `http://127.0.0.1:8000` by default. The running service's `/openapi.json` (or `/docs`) is the full schema. This page covers the conventions a client needs.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, current schema and rubric versions, active providers |
| GET | `/v1/venues` | Venue options |
| GET | `/v1/models` | Model options |
| POST | `/v1/uploads` | Upload a PDF, returns `upload_id` |
| POST | `/v1/assessments` | Create a five-dimension scoring job (202) |
| GET | `/v1/assessments` | Five-dimension history |
| GET | `/v1/assessments/{id}` | Poll a five-dimension job |
| POST | `/v1/credibility/assessments` | Create a credibility check (202) |
| GET | `/v1/credibility/assessments` | Credibility check history |
| GET | `/v1/credibility/assessments/{id}` | Poll a credibility check |
| POST | `/internal/validate-report` | Internal tool: validate one model output |

Both job types are long-running. Creation returns at once and the client polls for status. Do not wait for a synchronous result.

## 1. Models

`GET /v1/models`

| model_id | provider | Default | Notes |
|---|---|---|---|
| `model_gpt_v1` | openai (`gpt-5.6-terra`) | yes (`LLM_PROVIDER=openai`) | Default model |
| `model_standard_v1` | deepseek (`deepseek-flash`) | no | Lower cost |

- Use the option with `is_default=true` as the initial choice, and send the selected `model_id` unchanged to both scoring and credibility endpoints.
- Only options with `is_available=true` can be submitted. Others return 400 `OPTION_UNAVAILABLE`.
- With `DEEPSEEK_ENABLED=false`, DeepSeek is not listed. Starting with `make run-deepseek` makes DeepSeek the default while GPT stays selectable. The credibility endpoint accepts only available `openai` or `deepseek` models. The one exception: when the backend runs with `LLM_PROVIDER=fixture`, `model_id: "not_called"` returns an offline example without calling a model.

## 2. PDF upload

`POST /v1/uploads` (multipart, field `file`) → `201 {upload_id, original_filename, page_count, sha256}`

| code | HTTP | Meaning |
|---|---|---|
| `UNSUPPORTED_FILE_TYPE` | 400 | Not a .pdf |
| `EMPTY_FILE` / `INVALID_PDF` | 400 | Empty or damaged file |
| `PDF_ENCRYPTED` | 400 | Encrypted PDF |
| `FILE_TOO_LARGE` | 413 | Larger than `MAX_UPLOAD_MB` (default 30 MB) |
| `PAGE_LIMIT_EXCEEDED` | 413 | More than `MAX_PAGE_COUNT` pages (default 120) |

## 3. Five-dimension scoring

- `POST /v1/assessments` → 202 `{assessment_id, status, stage, progress_percent, created_at}`
- `GET /v1/assessments/{id}`: poll
- `GET /v1/assessments?search=&sort=date|title|author&offset=&limit=`: history (completed and failed)

## 4. Credibility check

### 4.1 Create

`POST /v1/credibility/assessments` → 202 with the full queued snapshot

```json
{
  "analysis_mode": "credibility",
  "schema_version": "credibility-0.5.0-draft",
  "rubric_version": "0.2-provisional-complete",
  "model_id": "model_gpt_v1",
  "source": {"type": "arxiv", "arxiv_id": "2106.09685", "upload_id": null}
}
```

- `source` may also be `{"type": "arxiv", "arxiv_id": null, "original_url": "https://arxiv.org/abs/2106.09685", "upload_id": null}`. The backend normalizes it to `{"type", "arxiv_id", "upload_id"}`.
- PDF: `{"type": "pdf", "arxiv_id": null, "upload_id": "upl_..."}`.
- An omitted `schema_version` means `credibility-0.5.0-draft`. An omitted `rubric_version` means `0.2-provisional-complete`, which produces trial scores. An explicit `0.1-draft` produces no scores.
- An invalid arXiv ID returns 400 `INVALID_ARXIV_ID` and a missing upload returns 404 `UPLOAD_NOT_FOUND`. Both are rejected at submission instead of failing after queueing.

### 4.2 Polling and history

- `GET /v1/credibility/assessments/{id}`: `status` is `queued | processing | completed | failed`; `stage` is `queued | retrieving | parsing | checking | finalizing | done | failed`.
- Progress stays at 60% during `checking` until all three checks finish, with no finer steps. This usually takes from a few minutes to over twenty, mostly depending on the number of references.
- `GET /v1/credibility/assessments?search=&offset=&limit=`: locally saved completed and failed checks, newest first:

```json
{"items": [{"assessment_id": "cred_...", "status": "completed", "title": "...", "arxiv_id": "1706.03762",
            "source_type": "arxiv", "model_id": "model_gpt_v1", "overall_score": 8.0,
            "category": "no_significant_concerns", "error_code": null,
            "created_at": "...", "completed_at": "..."}],
 "total": 1, "offset": 0, "limit": 20}
```

The home page's example reports are the `status=completed` items from this list, opened through the detail endpoint by `assessment_id`.

### 4.3 Failure codes (`error.code` when `status=failed`)

| code | retryable | Suggested display |
|---|---|---|
| `UNABLE_TO_ASSESS` | no | Not an English machine learning research paper. Show a neutral "out of scope" message; `paper` carries the paper details |
| `PAGE_LIMIT_EXCEEDED` / `FILE_TOO_LARGE` | no | Show `message` |
| `ARXIV_NOT_FOUND` / `INVALID_ARXIV_ID` | no | Ask the user to check the input |
| `PDF_PASSWORD_PROTECTED` / `EMPTY_TEXT` / `PDF_PARSE_FAILED` / `PARSE_FAILED` | no | Show `message` |
| `UPLOAD_NOT_FOUND` | no | Ask the user to upload again |
| `ARXIV_FETCH_FAILED` | yes | Offer a retry |
| `SERVICE_RESTARTED` | yes | Interrupted by a restart; submit again |
| `MODEL_UNAVAILABLE` / `MODEL_TIMEOUT` / `MODEL_OUTPUT_INVALID` / `INTERNAL_ERROR` | per `retryable` | Offer a retry |

Five-dimension scoring uses the same error codes.

## 5. Error format

Every error is `{"detail": {"code": "...", "message": "...", "debug_id": ...}}`, with `message` in English.

- Request body validation fails: 422 `INVALID_REQUEST`, with field-level details in `detail.errors`.
- Unknown route or method: `HTTP_404` / `HTTP_405`.

## 6. How a check ran (0.5.0)

- `report.versions.model_calls`: the `purpose` of each model call (for example `claim_extraction`, `paper_link_classification`, `reference_source_agent`, `external_code_discovery_native_tools`). The frontend shows these as a list of agent steps.
- Reference `reason` prefixes: `[agent_verified_source]` means the reference agent found the source and a program check accepted it; `[non_scholarly_official_page]` means a non-scholarly source, such as a dataset or system card, whose official page was found.
- `report.code_discovery.queries[].provider`: `openai_web_search` (GPT) or `deepseek_search` (DeepSeek).
- Claim `reconciled_from`: the original judgment before evidence from the code dimension changed it.
