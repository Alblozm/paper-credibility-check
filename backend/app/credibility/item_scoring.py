"""Deterministic 0.2 trial scores over validated check results; no model calls.

Keep the delivered policy's incomplete-scope fallbacks and raw-score categories.
Original check statuses, findings, sources and coverage are never changed.
"""
from __future__ import annotations

from app.credibility.models import ClaimCheckResult, CodeCheckResult, CredibilityDimension, ReferenceCheckResult
from app.credibility.reconcile import SUPPORTED_BY_CODE_RULE
from app.credibility.rubric import CredibilityRubric


def _assign(dimension: CredibilityDimension, value: float, rules: list[str], gaps: list[str]) -> None:
    if dimension.applicability == "not_applicable":
        dimension.score = None
        dimension.score_status = "not_applicable"
        dimension.score_rule_ids = []
        return
    dimension.score = value
    dimension.score_status = "scored"
    dimension.score_rule_ids = list(dict.fromkeys(rules))
    dimension.unassessed_reason = None
    # Applicability remains undetermined when the check could not establish it.
    # Only the trial total may include its explicitly labelled fallback score.
    dimension.summary += " Trial verifiability score under rubric 0.2-provisional-complete."
    if gaps:
        dimension.summary += " Scoring gaps: " + "; ".join(gaps) + "."


def score_check_results(
    claim: ClaimCheckResult,
    references: ReferenceCheckResult,
    code: CodeCheckResult,
    *,
    rubric: CredibilityRubric,
    paper_parse_usable: bool = True,
) -> tuple[ClaimCheckResult, ReferenceCheckResult, CodeCheckResult]:
    """Copy inputs and map checked outcomes to trial item/dimension scores."""
    claim, references, code = (item.model_copy(deep=True) for item in (claim, references, code))
    if rubric.status != "provisional":
        return claim, references, code
    fallback = rubric.technical_fallback_score

    values: list[float] = []
    rules: list[str] = []
    gaps: list[str] = []
    for item in claim.checks.items:
        state = "evidence_not_found" if item.outcome == "not_found" else item.outcome
        value = rubric.claim_scores.get(state, fallback)
        item.item_score = value
        item.score_rule_id = "P_C_" + (state.upper() if state in rubric.claim_scores else "UNVERIFIABLE")
        if item.outcome == "supported" and item.reconciled_from is not None:
            item.score_rule_id = SUPPORTED_BY_CODE_RULE
        values.append(value)
        rules.append(item.score_rule_id)
        if item.outcome in {"not_checked", "unable_to_determine"}:
            gaps.append("claim item could not be verified")
    if not claim.checks.scope_complete or not values or not paper_parse_usable:
        gaps.append("claim scope or parsed material is incomplete")
    if gaps:
        values.append(fallback)
        rules.append("P_C_SCOPE_FALLBACK")
    _assign(claim.dimension, min(values), rules, list(dict.fromkeys(gaps)))

    _score_references_in_place(references, rubric)

    _score_code_in_place(code, rubric)
    return claim, references, code


def score_code_result(code: CodeCheckResult, *, rubric: CredibilityRubric) -> CodeCheckResult:
    """Score a code-only verification through the frozen production mapping."""
    code = code.model_copy(deep=True)
    if rubric.status == "provisional":
        _score_code_in_place(code, rubric)
    return code


def _score_code_in_place(code: CodeCheckResult, rubric: CredibilityRubric) -> None:
    fallback = rubric.technical_fallback_score
    checks = code.checks
    if code.dimension.applicability == "not_applicable":
        _assign(code.dimension, fallback, [], [])
        return
    available = checks.code_availability == "available" and any(
        repo.association == "confirmed" and repo.availability == "accessible" and repo.implementation_evidence_ids
        for repo in checks.repositories
    )
    confirmed = {repo.repository_id for repo in checks.repositories if repo.association == "confirmed"}
    eligible = [
        d for d in checks.discussions
        if d.scoring_eligible and d.relevance == "relevant" and d.repository_id in confirmed
    ]
    failure = any(d.signal == "failure_reported" for d in eligible)
    if failure:
        state = "failure_or_data_problem_report"
    elif available:
        state = "official_code_available"
    elif checks.code_availability == "not_found_in_scope":
        state = "no_public_code_found"
    else:
        state = "unverifiable"
    value = rubric.code_scores.get(state, fallback)
    checks.base_score_rule_id = "P_K_" + state.upper()
    rules = [checks.base_score_rule_id]
    # Preserve 0.2's minimum over coexisting code states (7 plus 9 remains 7).
    if available and any(d.signal == "success_reported" and d.independence == "independent" for d in eligible):
        value = min(value, rubric.code_scores["independent_success_report"])
        rules.append("P_K_INDEPENDENT_SUCCESS")
    checks.score_rule_id = "P_K_MINIMUM_WITH_CAP"
    rules.append(checks.score_rule_id)
    gaps = [] if checks.scope_complete else ["code search coverage is incomplete"]
    if any(not d.scoring_eligible for d in checks.discussions):
        gaps.append("unreviewed discussions do not change the code score")
    _assign(code.dimension, min(value, rubric.code_score_cap), rules, gaps)


def score_reference_result(references: ReferenceCheckResult, *, rubric: CredibilityRubric) -> ReferenceCheckResult:
    """Score an isolated reference execution using the same frozen rule path."""
    references = references.model_copy(deep=True)
    if rubric.status == "provisional":
        _score_references_in_place(references, rubric)
    return references


def _score_references_in_place(references: ReferenceCheckResult, rubric: CredibilityRubric) -> None:
    fallback = rubric.technical_fallback_score
    values, rules, gaps = [], [], []
    for item in references.checks.items:
        item.item_score = rubric.reference_scores.get(item.outcome, fallback)
        item.score_rule_id = "P_R_" + (
            item.outcome.upper() if item.outcome in rubric.reference_scores else "UNVERIFIABLE"
        )
        values.append(item.item_score)
        rules.append(item.score_rule_id)
    if references.checks.extraction_status != "complete" or references.checks.gaps:
        gaps.append("reference extraction is incomplete")
    if references.checks.unverified_count or references.checks.not_checked_count:
        gaps.append("references include unresolved or failed checks")
    if not values:
        rules.append("P_R_NO_ITEMS_FALLBACK")
        gaps.append("no reference items available")
    # The delivered 0.2 policy does not invent missing reference rows/denominators.
    _assign(references.dimension, sum(values) / len(values) if values else fallback, rules, gaps)
