"""拼装可信度报告：三项检查 → 合并证据 → 引用闭合校验 → 计分。

总分只来自 scoring.score_dimensions，这里不手写分数。导入本模块不会跑模型或外部查询。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.credibility.claims import check_claims
from app.credibility.context import CheckContext, build_context, paper_from_context, parser_version
from app.credibility.coverage import build_reference_summary, explain_coverage
from app.credibility.execution import check_code_with_model, run_checks
from app.credibility.item_scoring import score_check_results
from app.credibility.locate import assert_report_links
from app.credibility.models import (
    CREDIBILITY_SCHEMA_VERSION,
    ClaimCheckResult,
    CodeCheckResult,
    CredibilityAlert,
    CredibilityAssessment,
    CredibilityNotice,
    CredibilityReport,
    CredibilityScoreResult,
    ReferenceCheckResult,
)
from app.credibility.reconcile import reconcile_artifact_claims
from app.credibility.references import check_references
from app.credibility.rubric import DRAFT_RUBRIC, CredibilityRubric
from app.credibility.runtime import StageExecution, decode_context, encode_context
from app.credibility.scoring import score_dimensions

PIPELINE_VERSION = "credibility-0.13-model-release-labels"


def _release_classifier(ctx: CheckContext, calls: list):
    """Model labels for release claims; None keeps the deterministic sentence rules."""
    from app.config import get_settings

    if not get_settings().credibility_release_classifier_enabled or ctx.model_id in {None, "not_called"}:
        return None
    from app.credibility.release_classifier import safe_classify

    return lambda claims: safe_classify(ctx, claims, calls)


class PaperUnavailable(Exception):
    """The input paper could not be retrieved or parsed; no check was run."""

    def __init__(self, message: str, *, code: str = "PARSE_FAILED", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PaperIneligible(Exception):
    """The paper failed the same English machine-learning gate as the five-dimension review."""

    def __init__(self, message: str, *, paper: dict | None) -> None:
        super().__init__(message)
        self.paper = paper


def _require_eligible(ctx: CheckContext, model_id: str, execution: StageExecution | None) -> None:
    """Run the five-dimension eligibility gate on a live parse; benchmark papers are already curated."""
    from app.config import get_settings

    settings = get_settings()
    if ctx.ref is not None or ctx.doc is None or not settings.credibility_eligibility_gate_enabled:
        return
    verdict = execution.load("eligibility", execution.fingerprint) if execution else None
    if verdict is None:
        from app.eligibility.gate import evaluate_eligibility
        from app.llm.retry import RetryPolicy
        from app.pipeline.orchestrator import build_llm_client
        from app.tracking.run_log import RunRecord, log_run

        result = evaluate_eligibility(
            ctx.doc, build_llm_client(model_id), retry_policy=RetryPolicy(max_attempts=settings.llm_max_retries + 1),
        )
        log_run(RunRecord(
            assessment_id=ctx.assessment_id or "credibility", stage="eligibility_check",
            model_id=result.model_id or "deterministic_gate",
            model_version=result.model_version or result.policy_version, rubric_version="not_applied",
            prompt_version=result.prompt_version, policy_version=result.policy_version,
            outcome=result.decision.value, reason_code=result.reason_code, prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens, latency_ms=result.latency_ms,
        ))
        verdict = {"eligible": result.is_eligible, "decision": result.decision.value, "message": result.user_reason}
        if execution:
            execution.save("eligibility", execution.fingerprint, verdict)
    if not verdict["eligible"]:
        paper = paper_from_context(ctx)
        raise PaperIneligible(
            f"No assessment was produced: {verdict['message']}",
            paper=paper.model_dump(mode="json") if paper else None,
        )


@dataclass(frozen=True)
class CheckSet:
    claims: Callable[[CheckContext], ClaimCheckResult] = check_claims
    references: Callable[[CheckContext], ReferenceCheckResult] = check_references
    code: Callable[[CheckContext], CodeCheckResult] = check_code_with_model


DEFAULT_CHECKS = CheckSet()


def assemble_report(
    ctx: CheckContext,
    *,
    report_id: str,
    rubric: CredibilityRubric = DRAFT_RUBRIC,
    checks: CheckSet = DEFAULT_CHECKS,
    execution: StageExecution | None = None,
) -> tuple[CredibilityReport, list[CredibilityNotice]]:
    (claim, references, code), execution_summary = run_checks(ctx, checks, execution)
    release_calls: list = []
    claim = reconcile_artifact_claims(claim, code, classify=_release_classifier(ctx, release_calls))
    claim.model_calls.extend(release_calls)
    if ctx.doc is not None:
        claim, references, code = score_check_results(
            claim, references, code, rubric=rubric,
            paper_parse_usable=ctx.doc.parse_quality.value == "good",
        )
    scored = score_dimensions([claim.dimension, references.dimension, code.dimension], rubric=rubric)

    findings = claim.findings + references.findings + code.findings
    payload = {
        "report_id": report_id,
        "rubric_id": rubric.rubric_id,
        "rubric_version": rubric.rubric_version,
        "rubric_status": rubric.status,
        "rubric_sha256": rubric.content_sha256,
        "scope_complete": (
            ctx.doc is not None and ctx.doc.parse_quality.value == "good"
            and claim.checks.scope_complete and claim.dimension.check_status == "completed"
            and all(item.outcome not in {"not_checked", "unable_to_determine"} for item in claim.checks.items)
            and references.checks.extraction_status in {"complete", "not_applicable"}
            and not references.checks.gaps
            and not references.checks.unverified_count
            and not references.checks.not_checked_count
            and (
                references.dimension.applicability == "not_applicable"
                or references.checks.original_total == references.checks.extracted_count
            )
            and (code.checks.scope_complete or code.dimension.applicability == "not_applicable")
        ),
        "is_provisional": rubric.status != "approved",
        "versions": {
            "pipeline_version": PIPELINE_VERSION,
            "parser_version": parser_version(ctx),
            "prompt_version": claim.prompt_version,
            "model_calls": [
                call.model_dump(mode="json") for call in claim.model_calls + references.model_calls + code.model_calls
            ],
        },
        "generated_at": ctx.generated_at,
        "dimensions": [d.model_dump(mode="json") for d in scored.dimensions],
        "checks": {
            "claims": claim.checks.model_dump(mode="json"),
            "references": references.checks.model_dump(mode="json"),
            "code": code.checks.model_dump(mode="json"),
        },
        "evidence": [e.model_dump(mode="json") for e in claim.evidence + references.evidence + code.evidence],
        "sources": [s.model_dump(mode="json") for s in references.sources + code.sources],
        "findings": [f.model_dump(mode="json") for f in findings],
        "overall": scored.overall.model_dump(mode="json"),
        "alerts": [a.model_dump(mode="json") for a in _alerts(scored, findings, rubric)],
        "limitations": _limitations(ctx, rubric, claim),
        "coverage": [row.model_dump(mode="json") for row in explain_coverage(ctx, claim, references, code)],
        "execution": execution_summary,
        "external_review": code.external_review,
        "reference_verification_summary": build_reference_summary(references.checks).model_dump(mode="json"),
        "code_discovery": code.discovery.model_dump(mode="json"),
    }
    if not rubric.can_score:
        _clear_item_scores(payload["checks"])
    report = CredibilityReport.model_validate(payload)
    assert_report_links(report)
    return report, _warnings(ctx, claim, rubric)


def build_completed_assessment(
    *,
    assessment_id: str,
    model_id: str,
    source: dict,
    created_at: str,
    started_at: str,
    completed_at: str,
    repo_root: Path | None = None,
    rubric: CredibilityRubric = DRAFT_RUBRIC,
    checks: CheckSet = DEFAULT_CHECKS,
    on_stage: Callable[[str, int], None] | None = None,
    execution: StageExecution | None = None,
    schema_version: str = CREDIBILITY_SCHEMA_VERSION,
) -> dict:
    stage = on_stage or (lambda _stage, _progress: None)
    stage("parsing", 30)
    cached = execution.load("parsing", execution.fingerprint) if execution else None
    if cached is not None:
        ctx = decode_context(cached["context"])
    else:
        ctx = build_context(source, generated_at=completed_at, repo_root=repo_root)
        if ctx.doc is not None and ctx.ref is None and model_id != "not_called":
            from app.parsing.title_repair import repair_title

            repair_title(ctx.doc, model_id)
    ctx.model_id = model_id
    if ctx.doc is None and model_id != "not_called":
        reason = ctx.unresolved_reason or "The paper could not be parsed."
        raise PaperUnavailable(
            str(reason), code=getattr(reason, "code", "PARSE_FAILED"), retryable=getattr(reason, "retryable", False),
        )
    if execution:
        if cached is None:
            execution.save("parsing", execution.fingerprint, {"context": encode_context(ctx)})
        execution.bind(ctx)
    if model_id != "not_called":
        _require_eligible(ctx, model_id, execution)
    stage("checking", 60)
    report, warnings = assemble_report(
        ctx, report_id=f"cred_report_{assessment_id}", rubric=rubric, checks=checks, execution=execution,
    )
    stage("finalizing", 90)
    paper = paper_from_context(ctx)
    return CredibilityAssessment.model_validate(
        {
            "schema_version": schema_version,
            "analysis_mode": "credibility",
            "assessment_id": assessment_id,
            "data_origin": "live" if ctx.doc is not None else "synthetic",
            "source": source,
            "model_id": model_id,
            "status": "completed",
            "stage": "done",
            "progress_percent": 100,
            "created_at": created_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "paper": paper.model_dump(mode="json") if paper else None,
            "report": report.model_dump(mode="json"),
            "warnings": [w.model_dump(mode="json") for w in warnings],
            "error": None,
        }
    ).model_dump(mode="json")


def _clear_item_scores(checks: dict) -> None:
    for item in checks["claims"]["items"] + checks["references"]["items"]:
        item["item_score"] = None
        item["score_rule_id"] = None
    checks["code"]["base_score_rule_id"] = None
    checks["code"]["score_rule_id"] = None


def _alerts(scored: CredibilityScoreResult, findings: list, rubric: CredibilityRubric) -> list[CredibilityAlert]:
    alerts: list[CredibilityAlert] = []
    blocking = set(scored.overall.calculation.blocking_dimension_ids)
    for finding in findings:
        if finding.kind == "concern" and finding.severity in ("major", "critical"):
            alerts.append(
                CredibilityAlert(
                    type="material_finding",
                    dimension_id=finding.dimension_id,
                    finding_ids=[finding.finding_id],
                    message=f"{finding.title}. Inspect the linked evidence before relying on the conclusion.",
                )
            )
    for dim in scored.dimensions:
        threshold = rubric.low_dimension_threshold
        if threshold is not None and dim.score is not None and dim.score < threshold:
            alerts.append(
                CredibilityAlert(
                    type="low_dimension_score",
                    dimension_id=dim.dimension_id,
                    finding_ids=list(dim.finding_ids),
                    message=f"This check scores {dim.score:g}. Other dimensions do not cancel it.",
                )
            )
        if dim.dimension_id in blocking or (
            rubric.status == "provisional" and (
                dim.check_status != "completed" or dim.applicability == "undetermined"
                or any("FALLBACK" in rule or "UNVERIFIABLE" in rule for rule in dim.score_rule_ids)
            ) and dim.applicability != "not_applicable"
        ):
            alerts.append(
                CredibilityAlert(
                    type="assessment_gap",
                    dimension_id=dim.dimension_id,
                    finding_ids=list(dim.finding_ids),
                    message=dim.unassessed_reason or dim.summary,
                )
            )
    return alerts


def _warnings(ctx: CheckContext, claim: ClaimCheckResult, rubric: CredibilityRubric) -> list[CredibilityNotice]:
    notices: list[CredibilityNotice] = []
    if ctx.doc is None:
        notices.append(
            CredibilityNotice(
                code="paper_not_parsed",
                message=(ctx.unresolved_reason or "The paper could not be parsed.") + " All three checks were not run.",
                dimension_id=None,
            )
        )
    elif not claim.applies:
        notices.append(
            CredibilityNotice(
                code=("claim_check_failed" if claim.dimension.check_status == "failed" else
                      "claims_not_found" if claim.generation_origin == "model" else "claims_not_prepared"),
                message=(
                    claim.dimension.summary if claim.dimension.check_status == "failed" else
                    "The model did not identify verifiable claims in the parsed abstract and introduction."
                    if claim.generation_origin == "model"
                    else "No claim candidates are available for this paper yet. Claim extraction has not run."
                ),
                dimension_id="claim_evidence",
            )
        )
    if any(f.certainty != "confirmed" for f in claim.findings):
        notices.append(
            CredibilityNotice(
                code="human_review_pending",
                message="At least one claim finding has not been human-reviewed yet.",
                dimension_id="claim_evidence",
            )
        )
    if rubric.status == "provisional":
        notices.append(CredibilityNotice(
            code="provisional_rubric", dimension_id=None,
            message=(
                "Trial scores use the benchmark rubric 0.2-provisional-complete. "
                "Unresolved checks or technical failures can receive low-verifiability scores; "
                "this does not establish false citations, wrong results or misconduct."
            ),
        ))
    elif not rubric.can_score:
        notices.append(
            CredibilityNotice(
                code="draft_rubric",
                message=(
                    f"Rubric {rubric.rubric_version} is not approved. "
                    "Dimension scores, overall score and category stay empty."
                ),
                dimension_id=None,
            )
        )
    return notices


def _limitations(ctx: CheckContext, rubric: CredibilityRubric, claim: ClaimCheckResult) -> list[str]:
    if claim.dimension.check_status == "failed":
        introduction = "Claim analysis failed to produce a validated result; its attempted model calls are recorded."
    elif claim.model_calls:
        introduction = (
            "Claims, comparison evidence and judgments were generated by the selected model in this task lineage. "
            "Evidence anchors and quotations were checked against the parsed paper. "
            "These are paper-internal model judgments, not human-reviewed or external verification."
        )
    elif ctx.model_id not in (None, "not_called"):
        introduction = "Claim analysis did not produce a validated result."
    else:
        introduction = (
            "No model was called in this run. "
            "Claim candidates are checked against the parsed paper before they are reported."
        )
    lines = [introduction]
    if ctx.doc is None:
        lines.append(ctx.unresolved_reason or "The paper could not be parsed.")
    else:
        lines.append(
            "Page numbers, sections and table labels come from the parsed paper, not from the claim candidates."
        )
    lines.append("Paper-internal evidence and external evidence are labelled separately by origin.")
    if rubric.status == "provisional":
        lines.extend([
            "These are provisional trial verifiability scores, not approved or human-Gold assessments.",
            "Coverage, failures and pending review remain unchanged even when numeric scores are shown.",
            "Reference scores average extracted items; unextracted references do not create imaginary scored rows.",
            "Categories use the unrounded total. Display precision must preserve the side of each threshold.",
            "Code states use the delivered minimum: available code (7) plus independent success (9) stays 7.",
        ])
    elif not rubric.can_score:
        lines.append("Score values, weights and category thresholds will be filled in after the rubric is approved.")
    return lines
