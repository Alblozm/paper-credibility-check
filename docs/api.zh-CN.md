# HTTP 接口

[English](api.md) · **中文**

后端默认运行在 `http://127.0.0.1:8000`。完整 schema 以运行中的 `/openapi.json`（或 `/docs`）为准；本文列出前端需要关心的约定。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 存活检查，返回当前 schema / rubric 版本和 provider |
| GET | `/v1/venues` | 会议选项 |
| GET | `/v1/models` | 模型选项 |
| POST | `/v1/uploads` | 上传 PDF，返回 `upload_id` |
| POST | `/v1/assessments` | 创建五维评分任务（202） |
| GET | `/v1/assessments` | 五维评分历史 |
| GET | `/v1/assessments/{id}` | 轮询五维评分 |
| POST | `/v1/credibility/assessments` | 创建可信度核查任务（202） |
| GET | `/v1/credibility/assessments` | 可信度核查历史 |
| GET | `/v1/credibility/assessments/{id}` | 轮询可信度核查 |
| POST | `/internal/validate-report` | 内部工具：校验一份模型输出 |

两类任务都是长任务：创建后立即返回，前端轮询状态，不要等待同步结果。

## 1. 模型选项

`GET /v1/models`

| model_id | provider | 默认 | 用途 |
|---|---|---|---|
| `model_gpt_v1` | openai（`gpt-5.6-terra`） | 是（`LLM_PROVIDER=openai`） | 默认模型 |
| `model_standard_v1` | deepseek（`deepseek-flash`） | 否 | 成本较低 |

- 前端用 `is_default=true` 的选项作为初始值，提交时把用户选择的 `model_id` 原样传给五维和三维接口。
- 只有 `is_available=true` 的选项可以提交，否则返回 400 `OPTION_UNAVAILABLE`。
- `DEEPSEEK_ENABLED=false` 时 DeepSeek 不出现在列表中。用 `make run-deepseek` 启动时 DeepSeek 为默认项，GPT 仍可选。可信度接口只接受 provider 为 `openai` 或 `deepseek` 的可用模型；唯一例外是后端以 `LLM_PROVIDER=fixture` 运行时传 `model_id: "not_called"`，返回不调用模型的离线示例。

## 2. PDF 上传

`POST /v1/uploads`（multipart，字段名 `file`）→ `201 {upload_id, original_filename, page_count, sha256}`

| code | HTTP | 含义 |
|---|---|---|
| `UNSUPPORTED_FILE_TYPE` | 400 | 不是 .pdf |
| `EMPTY_FILE` / `INVALID_PDF` | 400 | 空文件、损坏 |
| `PDF_ENCRYPTED` | 400 | 加密 PDF |
| `FILE_TOO_LARGE` | 413 | 超过 `MAX_UPLOAD_MB`（默认 30 MB） |
| `PAGE_LIMIT_EXCEEDED` | 413 | 超过 `MAX_PAGE_COUNT`（现为 120 页） |

## 3. 五维评分

- `POST /v1/assessments` → 202 `{assessment_id, status, stage, progress_percent, created_at}`
- `GET /v1/assessments/{id}`：轮询
- `GET /v1/assessments?search=&sort=date|title|author&offset=&limit=`：历史列表（completed / failed）

## 4. 可信度核查

### 4.1 创建

`POST /v1/credibility/assessments` → 202，返回完整排队快照

```json
{
  "analysis_mode": "credibility",
  "schema_version": "credibility-0.5.0-draft",
  "rubric_version": "0.2-provisional-complete",
  "model_id": "model_gpt_v1",
  "source": {"type": "arxiv", "arxiv_id": "2106.09685", "upload_id": null}
}
```

- `source` 也可以写成 `{"type": "arxiv", "arxiv_id": null, "original_url": "https://arxiv.org/abs/2106.09685", "upload_id": null}`，后端规范化后存为 `{"type", "arxiv_id", "upload_id"}`。
- PDF：`{"type": "pdf", "arxiv_id": null, "upload_id": "upl_..."}`。
- `schema_version` 省略时为 `credibility-0.5.0-draft`，`rubric_version` 省略时为 `0.2-provisional-complete`（出试验分）。显式传 `0.1-draft` 时不出分。
- 无效 arXiv ID → 400 `INVALID_ARXIV_ID`；upload 不存在 → 404 `UPLOAD_NOT_FOUND`。两者都在提交时返回，不会排队后才失败。

### 4.2 轮询与历史

- `GET /v1/credibility/assessments/{id}`：`status` 为 `queued | processing | completed | failed`；`stage` 为 `queued | retrieving | parsing | checking | finalizing | done | failed`。
- `checking` 阶段进度停在 60%，直到三项检查都结束，没有细分进度。通常需要几分钟到二十多分钟，主要取决于参考文献数量。
- `GET /v1/credibility/assessments?search=&offset=&limit=`：本地已完成 / 失败的核查，按时间倒序：

```json
{"items": [{"assessment_id": "cred_...", "status": "completed", "title": "...", "arxiv_id": "1706.03762",
            "source_type": "arxiv", "model_id": "model_gpt_v1", "overall_score": 8.0,
            "category": "no_significant_concerns", "error_code": null,
            "created_at": "...", "completed_at": "..."}],
 "total": 1, "offset": 0, "limit": 20}
```

首页的示例报告：取列表里 `status=completed` 的条目，用 `assessment_id` 调详情接口打开。

### 4.3 失败码（`status=failed` 时 `error.code`）

| code | retryable | 前端建议 |
|---|---|---|
| `UNABLE_TO_ASSESS` | 否 | 不是英文机器学习研究论文，按“超出评估范围”中性显示；`paper` 字段有论文信息 |
| `PAGE_LIMIT_EXCEEDED` / `FILE_TOO_LARGE` | 否 | 显示 message |
| `ARXIV_NOT_FOUND` / `INVALID_ARXIV_ID` | 否 | 提示检查输入 |
| `PDF_PASSWORD_PROTECTED` / `EMPTY_TEXT` / `PDF_PARSE_FAILED` / `PARSE_FAILED` | 否 | 显示 message |
| `UPLOAD_NOT_FOUND` | 否 | 提示重新上传 |
| `ARXIV_FETCH_FAILED` | 是 | 可重试 |
| `SERVICE_RESTARTED` | 是 | 服务重启中断，重新提交 |
| `MODEL_UNAVAILABLE` / `MODEL_TIMEOUT` / `MODEL_OUTPUT_INVALID` / `INTERNAL_ERROR` | 视 `retryable` | 可重试 |

五维评分的 `error.code` 使用同一套错误码。

## 5. 统一错误格式

所有错误都是 `{"detail": {"code": "...", "message": "...", "debug_id": ...}}`，`message` 均为英文。

- 请求体校验失败：422 `INVALID_REQUEST`，另有 `detail.errors`（字段级明细）。
- 未知路由或方法：`HTTP_404` / `HTTP_405`。

## 6. 报告里的执行过程（0.5.0）

- `report.versions.model_calls`：每次模型调用的 `purpose`（如 `claim_extraction`、`paper_link_classification`、`reference_source_agent`、`external_code_discovery_native_tools`），可做成简易的 agent 步骤列表。
- 引用条目 `reason` 前缀：`[agent_verified_source]` 表示由引用 agent 找到并经程序验收；`[non_scholarly_official_page]` 表示数据集、系统卡等非学术来源，已找到官方页面。
- `report.code_discovery.queries[].provider`：`openai_web_search`（GPT）或 `deepseek_search`（DeepSeek）。
- 主张条目 `reconciled_from`：被代码维度证据改判的原始判断。
