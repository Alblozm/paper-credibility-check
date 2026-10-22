"""任务一：摘要与引言的关键主张，对照方法、实验、结果、表格、附录或结论里的证据。

真实请求现场生成模型候选；prepared_claims 仅供显式离线示例和回归测试。
两种候选都交给 build_claim_checks，走同一套定位核对与条件守卫：
- 主张端必须位于 abstract / introduction；对照端不得位于 abstract、introduction、
  background（相关工作）、references。
- 页码、章节、表号只取解析结果；quote 必须能在原文找到；写出的数字必须在原文出现。
- 判为 contradicted 时，至少一条对照证据的 metric、dataset、baseline、setting
  都写明且与主张一致；否则降为 unable_to_determine。
任务二、任务三不改本文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.credibility.context import CheckContext
from app.credibility.locate import PaperSpanCandidate, locate_paper_span
from app.credibility.models import (
    ClaimCheckResult,
    CredibilityClaimChecks,
    CredibilityClaimItem,
    CredibilityDimension,
    CredibilityEvidence,
    CredibilityFinding,
)
from app.schemas.common import StrictModel

PREPARED_DIR = Path(__file__).with_name("prepared_claims")
CLAIM_ROLES = frozenset({"abstract", "introduction"})
NON_EVIDENCE_ROLES = frozenset({"abstract", "introduction", "background", "references"})
CONDITION_KEYS = ("metric", "dataset", "baseline", "setting")

Outcome = Literal["supported", "partially_supported", "not_found", "contradicted", "unable_to_determine"]


class ComparisonConditions(StrictModel):
    metric: str | None = None
    dataset: str | None = None
    baseline: str | None = None
    setting: str | None = None


class SpanInput(StrictModel):
    evidence_id: str
    mode: Literal["quote", "paraphrase"]
    excerpt: str = Field(min_length=1)
    chunk_id: str | None = None
    element_id: str | None = None
    values: list[str] = []
    conditions: ComparisonConditions | None = None


class ReviewRecord(StrictModel):
    status: Literal["pending", "verified", "rejected"]
    reviewer: str | None = None
    reviewed_at: str | None = None
    note: str | None = None


class ClaimCandidate(StrictModel):
    claim_id: str
    statement: str = Field(min_length=1)
    importance: Literal["core", "supporting"]
    claim_conditions: ComparisonConditions
    claim_evidence: list[SpanInput] = Field(min_length=1)
    comparison_evidence: list[SpanInput]
    search_scope: str
    proposed_outcome: Outcome
    reason: str
    review: ReviewRecord


class ClaimCandidateSet(StrictModel):
    paper_version_id: str
    origin: Literal["prepared", "model"]
    selection_scope: str
    scope_complete: bool
    scope_limitations: list[str]
    claims: list[ClaimCandidate]


def load_prepared_claims(paper_version_id: str, directory: Path = PREPARED_DIR) -> ClaimCandidateSet | None:
    path = directory / f"{paper_version_id}.json"
    if not path.is_file():
        return None
    candidate_set = ClaimCandidateSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if candidate_set.paper_version_id != paper_version_id:
        raise ValueError(f"{path.name} declares {candidate_set.paper_version_id}")
    return candidate_set


def check_claims(ctx: CheckContext, *, prepared_dir: Path = PREPARED_DIR) -> ClaimCheckResult:
    if ctx.doc is None or ctx.paper_version_id is None:
        return _not_run(
            "The paper could not be parsed, so claims were not checked.",
            ctx.unresolved_reason or "The paper has not been parsed.",
        )
    if ctx.model_id not in (None, "not_called"):
        from app.config import get_settings
        from app.credibility.claim_model import generate_claim_candidates
        from app.llm.retry import RetryPolicy
        from app.pipeline.orchestrator import build_llm_client

        generated = generate_claim_candidates(
            ctx,
            build_llm_client(ctx.model_id),
            retry_policy=RetryPolicy(max_attempts=get_settings().llm_max_retries + 1),
        )
        result = build_claim_checks(ctx, generated.candidates)
        result.model_calls = generated.model_calls
        result.prompt_version = generated.prompt_version
        result.generation_origin = "model"
        return result

    # Explicit offline/demo callers retain the checked prepared example. A real
    # model request never reads this file, including after a provider failure.
    candidates = load_prepared_claims(ctx.paper_version_id, prepared_dir)
    if candidates is None or not candidates.claims:
        return _not_run(
            "No claim candidates are available for this paper yet, so claims were not checked.",
            "Claim extraction has not run for this paper. Only prepared claims are checked at present.",
        )
    result = build_claim_checks(ctx, candidates)
    result.generation_origin = "prepared"
    return result


def build_claim_checks(ctx: CheckContext, candidates: ClaimCandidateSet) -> ClaimCheckResult:
    items: list[CredibilityClaimItem] = []
    evidence: list[CredibilityEvidence] = []
    findings: list[CredibilityFinding] = []
    limitations = list(candidates.scope_limitations)

    for claim in candidates.claims:
        item, located, finding = _check_one(ctx, claim, origin=candidates.origin)
        items.append(item)
        evidence.extend(located)
        if finding is not None:
            findings.append(finding)

    checked = [i for i in items if i.outcome != "not_checked"]
    if not checked:
        check_status = "failed"
        summary = "No claim could be checked because its parsed locations could not be confirmed."
    elif candidates.scope_complete and len(checked) == len(items):
        check_status = "completed"
        summary = f"{len(checked)} claim(s) compared with later evidence."
    else:
        check_status = "partial"
        summary = f"{len(checked)} of {len(items)} selected claim(s) compared. The claim selection is incomplete."
    pending = [c.claim_id for c in candidates.claims if c.review.status != "verified"]
    if pending:
        limitations.append(f"Human review is pending for: {', '.join(pending)}.")

    dimension = _dimension(
        applicability="applicable",
        applicability_reason=(
            "The paper makes claims in its abstract or introduction that can be compared with later evidence."
        ),
        check_status=check_status,
        summary=summary,
        unassessed_reason=(
            "The rubric is still a draft, so this dimension has no score."
            if candidates.scope_complete
            else "The claim selection is incomplete and the rubric is still a draft, so this dimension has no score."
        ),
        evidence_ids=[e.evidence_id for e in evidence],
        finding_ids=[f.finding_id for f in findings],
    )
    return ClaimCheckResult(
        applies=True,
        anchors_matched=bool(checked),
        dimension=dimension,
        checks=CredibilityClaimChecks(
            selection_scope=candidates.selection_scope,
            scope_complete=candidates.scope_complete,
            scope_limitations=limitations,
            items=items,
        ),
        evidence=evidence,
        findings=findings,
    )


def _check_one(
    ctx: CheckContext, claim: ClaimCandidate, *, origin: str = "prepared",
) -> tuple[CredibilityClaimItem, list[CredibilityEvidence], CredibilityFinding | None]:
    problems: list[str] = []
    claim_side: list[CredibilityEvidence] = []
    comparison_side: list[tuple[CredibilityEvidence, ComparisonConditions | None]] = []

    for span in claim.claim_evidence:
        located, issues = locate_paper_span(ctx, _span(span), allowed_roles=CLAIM_ROLES)
        problems += issues
        if located:
            claim_side.append(located.evidence)
    for span in claim.comparison_evidence:
        located, issues = locate_paper_span(ctx, _span(span), forbidden_roles=NON_EVIDENCE_ROLES)
        problems += issues
        if located:
            comparison_side.append((located.evidence, span.conditions))

    conditions_text = _conditions_text(claim, comparison_side)
    if problems:
        item = CredibilityClaimItem(
            claim_id=claim.claim_id,
            statement=claim.statement,
            importance=claim.importance,
            conditions=conditions_text,
            claim_evidence_ids=[],
            comparison_evidence_ids=[],
            search_scope=claim.search_scope,
            outcome="not_checked",
            reason="Not checked: " + "; ".join(problems) + ".",
            score_rule_id=None,
            item_score=None,
        )
        return item, [], None

    outcome, reason = _guard_outcome(claim, [cond for _, cond in comparison_side])
    item = CredibilityClaimItem(
        claim_id=claim.claim_id,
        statement=claim.statement,
        importance=claim.importance,
        conditions=conditions_text,
        claim_evidence_ids=[e.evidence_id for e in claim_side],
        comparison_evidence_ids=[e.evidence_id for e, _ in comparison_side],
        search_scope=claim.search_scope,
        outcome=outcome,
        reason=reason,
        score_rule_id=None,
        item_score=None,
    )
    located = claim_side + [e for e, _ in comparison_side]
    return item, located, _finding(claim, item, origin=origin)


def _guard_outcome(claim: ClaimCandidate, conditions: list[ComparisonConditions | None]) -> tuple[str, str]:
    outcome, reason = claim.proposed_outcome, claim.reason
    if outcome in ("supported", "partially_supported", "contradicted") and not conditions:
        return "unable_to_determine", "No comparison evidence was given, so the outcome cannot be determined. " + reason
    if outcome == "contradicted" and not any(_same_conditions(claim.claim_conditions, c) for c in conditions):
        return "unable_to_determine", (
            "A difference was found, but no comparison evidence shares the claim's metric, dataset, baseline "
            "and setting, so it is not marked as a contradiction. " + reason
        )
    return outcome, reason


def _same_conditions(claim: ComparisonConditions, evidence: ComparisonConditions | None) -> bool:
    if evidence is None:
        return False
    for key in CONDITION_KEYS:
        left, right = getattr(claim, key), getattr(evidence, key)
        if not left or not right or left.strip().casefold() != right.strip().casefold():
            return False
    return True


def _finding(claim: ClaimCandidate, item: CredibilityClaimItem, *, origin: str = "prepared") -> CredibilityFinding:
    verified = claim.review.status == "verified"
    if item.outcome == "contradicted":
        kind, severity = "concern", "major" if claim.importance == "core" else "moderate"
        title = "Claim contradicted by later evidence"
    elif item.outcome == "not_found":
        kind, severity = "concern", "moderate" if claim.importance == "core" else "minor"
        title = "No supporting evidence found in the checked scope"
    elif item.outcome == "unable_to_determine":
        kind, severity, title = "verification_gap", "info", "Claim could not be determined from the evidence"
    elif item.outcome == "partially_supported":
        kind, severity, title = "context", "info", "Claim partially supported by later evidence"
    else:
        kind, severity, title = "context", "info", "Claim supported by later evidence"
    if verified:
        review_line = f"Human-reviewed by {claim.review.reviewer or 'an unnamed reviewer'}"
        review_line += f" on {claim.review.reviewed_at}." if claim.review.reviewed_at else "."
    else:
        review_line = "Human review pending: check both locations against the PDF before relying on this finding."
    return CredibilityFinding(
        finding_id=f"find_{claim.claim_id}",
        dimension_id="claim_evidence",
        item_ids=[claim.claim_id],
        kind=kind,
        severity=severity,
        certainty="confirmed" if verified else "unresolved",
        title=title,
        detail=item.reason,
        evidence_ids=item.claim_evidence_ids + item.comparison_evidence_ids,
        source_ids=[],
        limitations=[
            review_line,
            "Paper-internal evidence only. The comparison was generated by a model and checked against the source."
            if origin == "model"
            else "Paper-internal evidence only. Prepared comparison; no model was called.",
        ],
    )


def _conditions_text(
    claim: ClaimCandidate, comparison: list[tuple[CredibilityEvidence, ComparisonConditions | None]]
) -> str:
    parts = [f"Claim: {_fmt(claim.claim_conditions)}"]
    for evidence, cond in comparison:
        label = evidence.paper_locator.element_label if evidence.paper_locator else None
        parts.append(f"{label or evidence.evidence_id}: {_fmt(cond) if cond else 'conditions not recorded'}")
    return " | ".join(parts)


def _fmt(cond: ComparisonConditions) -> str:
    return ", ".join(f"{k} = {getattr(cond, k) or 'not stated'}" for k in CONDITION_KEYS)


def _span(span: SpanInput) -> PaperSpanCandidate:
    return PaperSpanCandidate(
        evidence_id=span.evidence_id,
        mode=span.mode,
        excerpt=span.excerpt,
        chunk_id=span.chunk_id,
        element_id=span.element_id,
        values=tuple(span.values),
    )


def _not_run(summary: str, reason: str) -> ClaimCheckResult:
    return ClaimCheckResult(
        applies=False,
        anchors_matched=False,
        dimension=_dimension(
            applicability="undetermined",
            applicability_reason="Claim applicability was not determined because no claims were checked.",
            check_status="not_run",
            summary=summary + " Empty records do not mean no concerns were found.",
            unassessed_reason=f"Not run: {reason}",
            evidence_ids=[],
            finding_ids=[],
        ),
        checks=CredibilityClaimChecks(
            selection_scope="No claims were selected.",
            scope_complete=False,
            scope_limitations=[f"Not run: {reason}"],
            items=[],
        ),
        evidence=[],
        findings=[],
    )


def _dimension(**fields: object) -> CredibilityDimension:
    return CredibilityDimension.model_validate(
        {
            "dimension_id": "claim_evidence",
            "score_status": "unable_to_assess",
            "score": None,
            "weight": 1,
            "score_rule_ids": [],
            **fields,
        }
    )
