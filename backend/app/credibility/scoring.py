"""总分只由后端按「适用维度平均」计算。

检查结果和模型输出都不带总分；这里只读三个维度的适用性和分数。
draft Rubric 先清空维度分。严格模式中适用维度无分或适用性未定时，总分和类别留空。
0.2 试验模式可以纳入有明确分数的低可验证性回退项，但保留原适用性与检查状态。
类别分界未确认时 category 保持 null。导入本模块不会跑核查管线。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from app.credibility.models import (
    CredibilityDimension,
    CredibilityDimensionId,
    CredibilityOverall,
    CredibilityOverallCalculation,
    CredibilityScoreResult,
)
from app.credibility.rubric import CredibilityRubric

_DIMENSION_ORDER = ("claim_evidence", "reference_verification", "code_reproduction")


def score_dimensions(
    dimensions: Sequence[CredibilityDimension],
    *,
    rubric: CredibilityRubric,
) -> CredibilityScoreResult:
    """返回清空草稿分后的三个维度，以及由适用性和分数决定的总分。"""
    if tuple(item.dimension_id for item in dimensions) != _DIMENSION_ORDER:
        raise ValueError("dimensions must be claim_evidence, reference_verification, code_reproduction")

    copied = [item.model_copy(deep=True) for item in dimensions]
    if not rubric.can_score:
        copied = [_clear_draft_score(item) for item in copied]

    trial = rubric.status == "provisional"
    applicable = [
        item for item in copied
        if item.applicability == "applicable" or (trial and item.applicability == "undetermined")
    ]
    included = [item.dimension_id for item in applicable]
    excluded = [item.dimension_id for item in copied if item.applicability == "not_applicable"]
    blocking = [
        item.dimension_id
        for item in copied
        if (item.applicability == "undetermined" and not trial)
        or (item.applicability != "not_applicable" and item.score is None)
    ]
    assessed = [item for item in applicable if item.score is not None]
    if rubric.can_score and not blocking and applicable:
        score = sum(float(item.score) for item in assessed if item.score is not None) / len(assessed)
        category = rubric.category_for(score)
        overall = _overall(
            status="assessed",
            score=score,
            category=category,
            summary=(
                (
                    "Trial verifiability score: the equal-weight average of non-excluded dimension scores. "
                    "Incomplete or unresolved checks can receive provisional fallback scores. "
                    "This is not an approved assessment or a finding of misconduct."
                    if trial else "The overall score is the equal-weight average of applicable dimension scores."
                )
                + ("" if category else " Category thresholds are not confirmed, so category is empty.")
            ),
            unassessed_reason=None,
            included=included,
            excluded=excluded,
            blocking=blocking,
            assessed_count=len(assessed),
        )
    else:
        overall = _overall(
            status="unable_to_assess",
            score=None,
            category=None,
            summary=_unassessed_summary(rubric, blocking, applicable),
            unassessed_reason=(
                "An overall score is produced only when the rubric is approved, every applicable "
                "dimension has a score, and no dimension is undetermined."
            ),
            included=included,
            excluded=excluded,
            blocking=blocking,
            assessed_count=len(assessed),
        )
    if trial:
        overall.calculation.method = "equal_weight_trial_dimensions"
        overall.calculation.fallback_dimension_ids = [
            item.dimension_id for item in copied if item.score is not None
            and (item.applicability == "undetermined" or item.check_status != "completed"
                 or any("FALLBACK" in rule or "UNVERIFIABLE" in rule for rule in item.score_rule_ids))
        ]
    return CredibilityScoreResult(dimensions=(copied[0], copied[1], copied[2]), overall=overall)


def _unassessed_summary(rubric: CredibilityRubric, blocking: list[str], applicable: list) -> str:
    if not rubric.can_score:
        return "The rubric is still a draft, so no dimension or overall score is given."
    if not applicable:
        return "No dimension is applicable, so there is no overall score."
    return (
        "The overall score is unavailable because an applicable dimension has no score or a dimension is undetermined."
    )


def _clear_draft_score(dimension: CredibilityDimension) -> CredibilityDimension:
    updates: dict[str, object] = {"score": None, "score_rule_ids": []}
    if dimension.applicability != "not_applicable":
        updates["score_status"] = "unable_to_assess"
    return dimension.model_copy(update=updates)


def _overall(
    *,
    status: Literal["assessed", "unable_to_assess"],
    score: float | None,
    category: str | None,
    summary: str,
    unassessed_reason: str | None,
    included: list[CredibilityDimensionId],
    excluded: list[CredibilityDimensionId],
    blocking: list[CredibilityDimensionId],
    assessed_count: int,
) -> CredibilityOverall:
    return CredibilityOverall(
        status=status,
        score=score,
        category=category,
        summary=summary,
        unassessed_reason=unassessed_reason,
        calculation=CredibilityOverallCalculation(
            method="equal_weight_applicable_dimensions",
            included_dimension_ids=included,
            excluded_dimension_ids=excluded,
            blocking_dimension_ids=blocking,
            assessed_dimension_count=assessed_count,
        ),
    )
