"""端到端流程骨架。

    validating_input → retrieving_paper → parsing_paper → analyzing
        → linking_evidence → finalizing_report → report_ready

TODO：接真实存储、真实解析、真实模型，并把 stage/progress 落库。

当前实现：走 fixture 模型 + stub 解析，让整条链路可跑通、可演示、可被前端联调。
校验与总分部分（analyze 之后的一切）已经是最终实现，不是占位。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.eligibility.gate import evaluate_eligibility
from app.llm.base import LLMClient
from app.llm.fixture import FixtureLLMClient
from app.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_analysis_prompt, get_active_rubric
from app.llm.repair import validate_with_repair
from app.llm.retry import LLMError, RetryPolicy, call_with_retry
from app.parsing.arxiv import ArxivDownloadError, ArxivSourceError
from app.parsing.benchmark import BenchmarkParseError, BenchmarkSourceMismatch
from app.parsing.generic import GenericPdfParser, PdfParseError
from app.parsing.grobid import StubParser
from app.schemas.enums import SCHEMA_VERSION, SourceType, Stage, Status
from app.schemas.requests import CreateAssessmentRequest
from app.tracking.run_log import RunRecord, log_run

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_llm_client(model_id: str) -> LLMClient:
    """Build the provider requested by the frontend's stable product model ID."""
    s = get_settings()
    if model_id == "model_standard_v1":
        from app.llm.deepseek import DeepSeekClient

        return DeepSeekClient(
            api_key=s.deepseek_api_key,
            base_url=s.deepseek_base_url,
            model=s.deepseek_model,
            thinking_enabled=s.deepseek_thinking_enabled,
            reasoning_effort=s.deepseek_reasoning_effort,
            timeout=s.llm_timeout_seconds,
        )
    if model_id == "model_gpt_v1":
        from app.llm.openai import OpenAIClient

        return OpenAIClient(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            model=s.openai_model,
            reasoning_effort=s.openai_reasoning_effort,
            timeout=s.llm_timeout_seconds,
        )
    if model_id == "model_fixture_v1":
        return FixtureLLMClient()
    raise ValueError(f"Unsupported model_id: {model_id}")


def build_parser():
    s = get_settings()
    if s.parser_provider == "benchmark":
        from app.parsing.benchmark import BenchmarkParser

        return BenchmarkParser(
            directory=s.benchmark_parsed_dir,
            expected_arxiv_id=s.benchmark_arxiv_id,
            source_record=s.benchmark_source_record,
        )
    if s.parser_provider in {"generic", "grobid"}:
        generic = GenericPdfParser(
            arxiv_cache_dir=s.arxiv_cache_dir,
            max_download_bytes=s.max_upload_mb * 1024 * 1024,
            max_pages=s.max_page_count,
            arxiv_timeout_seconds=s.arxiv_timeout_seconds,
        )
        if s.parser_provider == "generic":
            return generic
        from app.parsing.grobid import GrobidParser

        return GrobidParser(s.grobid_url, fallback=generic)
    return StubParser()


def run_assessment(
    assessment_id: str, request: CreateAssessmentRequest, store: dict, pdf_path: str | None = None,
) -> None:
    """在后台执行一次完整评估，把状态写回 store。"""
    from app.parsing.anchors import build_anchored_text

    s = get_settings()
    state = store.setdefault(assessment_id, {})
    state.update(
        schema_version=SCHEMA_VERSION,
        assessment_id=assessment_id,
        status=Status.PROCESSING.value,
        stage=Stage.RETRIEVING_PAPER.value,
        progress_percent=10,
        started_at=_now(),
    )

    try:
        # --- 解析 ---
        parser = build_parser()
        state.update(stage=Stage.PARSING_PAPER.value, progress_percent=30)
        if request.source.type is SourceType.ARXIV:
            doc = parser.parse_arxiv(request.source.arxiv_id or "")
        else:
            if not pdf_path:
                raise ValueError("Missing private upload path")
            doc = parser.parse_pdf(pdf_path)

        # Small-caps PDF titles ("E VALUATING LLM S") are restored before storage;
        # only spacing and case may change.
        if s.parser_provider in {"generic", "grobid"}:
            from app.parsing.title_repair import repair_title

            repair_title(doc, request.model_id)

        # Generic inputs are stored as an immutable structured snapshot before
        # scoring. A model outage therefore never discards completed parsing work.
        if doc.source_sha256:
            from app.storage.parsed_documents import store_parsed_document

            store_parsed_document(doc)

        # --- 评分资格门控（固定硬检查 + 最多两次受控分类）---
        state.update(stage=Stage.ANALYZING.value, progress_percent=45)
        client = build_llm_client(request.model_id)
        eligibility = evaluate_eligibility(
            doc,
            client,
            retry_policy=RetryPolicy(max_attempts=s.llm_max_retries + 1),
        )
        log_run(
            RunRecord(
                assessment_id=assessment_id,
                stage="eligibility_check",
                model_id=eligibility.model_id or "deterministic_gate",
                model_version=eligibility.model_version or eligibility.policy_version,
                rubric_version="not_applied",
                prompt_version=eligibility.prompt_version,
                policy_version=eligibility.policy_version,
                outcome=eligibility.decision.value,
                reason_code=eligibility.reason_code,
                prompt_tokens=eligibility.prompt_tokens,
                completion_tokens=eligibility.completion_tokens,
                latency_ms=eligibility.latency_ms,
            )
        )
        if not eligibility.is_eligible:
            state.update(
                status=Status.FAILED.value,
                paper=doc.paper.model_dump(mode="json"),
                report=None,
                error={
                    "code": "UNABLE_TO_ASSESS",
                    "message": f"No assessment was produced: {eligibility.user_reason}",
                    "stage": Stage.ANALYZING.value,
                    "retryable": False,
                    "debug_id": assessment_id,
                },
                completed_at=_now(),
            )
            return

        anchored = build_anchored_text(doc)
        # 校验层要的全部信息（纯文本、合法页码、chunk、element、置信度上限）
        # 都在 index 里，不用再单独传 source_text / known_pages。
        index = doc.build_index()

        # --- 模型分析 ---
        state.update(stage=Stage.ANALYZING.value, progress_percent=55)
        rubric = get_active_rubric()
        response = call_with_retry(
            lambda: client.complete_json(SYSTEM_PROMPT, build_analysis_prompt(doc, anchored, rubric)),
            RetryPolicy(max_attempts=s.llm_max_retries + 1),
        )

        # --- 校验 / 修复 / 总分（已是最终实现）---
        state.update(stage=Stage.LINKING_EVIDENCE.value, progress_percent=75)
        result, repairs = validate_with_repair(
            client,
            response.text,
            source_index=index,
            policy=s.evidence_degradation_policy,
            dimension_weights={
                dimension.dimension_id: dimension.weight
                for dimension in rubric.definition.dimensions
            },
        )

        log_run(
            RunRecord(
                assessment_id=assessment_id,
                stage=Stage.ANALYZING.value,
                model_id=response.model_id,
                model_version=response.model_version,
                rubric_version=rubric.rubric_version,
                prompt_version=PROMPT_VERSION,
                rubric_sha256=rubric.content_sha256,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                latency_ms=response.latency_ms,
                repair_passes=repairs,
                validation_issue_count=len(result.issues),
            )
        )

        if not result.ok:
            state.update(
                status=Status.FAILED.value,
                stage=Stage.FINALIZING_REPORT.value,
                error={
                    "code": "MODEL_OUTPUT_INVALID",
                    "message": "The model output did not meet the report structure. Please try again.",
                    "stage": Stage.FINALIZING_REPORT.value,
                    "retryable": True,
                    "debug_id": assessment_id,
                },
                completed_at=_now(),
            )
            return

        state.update(
            status=Status.COMPLETED.value,
            stage=Stage.REPORT_READY.value,
            progress_percent=100,
            paper=doc.paper.model_dump(mode="json"),
            report=result.report.model_dump(mode="json"),
            warnings=state.get("warnings", []) + [
                w.model_dump(mode="json") for w in result.to_warnings(Stage.LINKING_EVIDENCE.value)
            ],
            error=None,
            completed_at=_now(),
        )

    except LLMError as exc:
        logger.exception("assessment %s failed", assessment_id)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": exc.error_code.value,
                "message": "The analysis service is temporarily unavailable. Please try again later.",
                "stage": state.get("stage"),
                "retryable": exc.retryable,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
    except ArxivSourceError:
        logger.info("assessment %s received an invalid arxiv source", assessment_id)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": "INVALID_ARXIV_ID",
                "message": "Enter a valid arXiv ID or link.",
                "stage": state.get("stage"),
                "retryable": False,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
    except ArxivDownloadError as exc:
        logger.warning("assessment %s could not retrieve arxiv: %s", assessment_id, exc)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": exc.code,
                "message": str(exc),
                "stage": state.get("stage"),
                "retryable": exc.retryable,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
    except PdfParseError as exc:
        logger.info("assessment %s could not parse PDF: %s", assessment_id, exc)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": exc.code,
                "message": f"No assessment was produced: {exc}",
                "stage": state.get("stage"),
                "retryable": False,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
    except BenchmarkSourceMismatch:
        logger.info("assessment %s rejected a non-benchmark source", assessment_id)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": "BENCHMARK_SOURCE_MISMATCH",
                "message": f"Benchmark mode only supports the test sample {s.benchmark_arxiv_id}.",
                "stage": state.get("stage"),
                "retryable": False,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
    except BenchmarkParseError:
        logger.exception("assessment %s could not load benchmark data", assessment_id)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": "PDF_PARSE_FAILED",
                "message": "The benchmark parsed paper could not be read. Check the test data and configuration.",
                "stage": state.get("stage"),
                "retryable": False,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
    except Exception:
        logger.exception("assessment %s crashed", assessment_id)
        state.update(
            status=Status.FAILED.value,
            error={
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred. Please try again later.",
                "stage": state.get("stage"),
                "retryable": True,
                "debug_id": assessment_id,
            },
            completed_at=_now(),
        )
