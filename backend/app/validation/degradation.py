"""证据校验失败后的维度降级。

⚠️ 契约 v0.4 **没有规定**这条规则，是本项目已识别的空白（见 notes/2026-08-30
-backend-summary.md 第 5 节 ①）。这里把两种候选做法都实现成可配置策略，
产品确认后只改 .env 的 EVIDENCE_DEGRADATION_POLICY，不改代码。

背景：契约 §15.4 要求每个有数字分数的维度至少绑一条 evidence_link。
但若校验发现该 link 指向不存在的证据并将其删除，这个维度就不再满足契约。
"""
from __future__ import annotations

from enum import Enum

from app.schemas.enums import Applicability, Confidence, Rating
from app.schemas.report import DimensionAssessment, Report
from app.validation.errors import Severity, ValidationIssue


class DegradationPolicy(str, Enum):
    #  方案 A：维度降为 uncertain，score 置 null（更保守，默认）
    TO_UNCERTAIN = "to_uncertain"
    #  方案 B：保留分数，confidence 压到 low（保留更多信息，但违反契约字面要求）
    LOWER_CONFIDENCE = "lower_confidence"


_REASON = (
    "Evidence validation failed: this dimension's evidence links are invalid or missing "
    "and cannot reliably support a score."
)


def _rebuild(dim: DimensionAssessment, **changes) -> DimensionAssessment:
    """整体重建而不是逐字段赋值。

    StrictModel 开了 validate_assignment，逐字段改会在中间态触发 §10.2 的组合校验。
    """
    data = dim.model_dump()
    data.update(changes)
    return DimensionAssessment(**data)


def apply_evidence_degradation(
    report: Report,
    policy: DegradationPolicy = DegradationPolicy.TO_UNCERTAIN,
) -> list[ValidationIssue]:
    """把没有有效证据的评分维度按策略降级，原地修改 report。"""
    issues: list[ValidationIssue] = []
    new_dims: list[DimensionAssessment] = []

    for i, dim in enumerate(report.dimensions):
        needs_degrade = dim.score is not None and not dim.evidence_links
        if not needs_degrade:
            new_dims.append(dim)
            continue

        if policy is DegradationPolicy.TO_UNCERTAIN:
            new_dims.append(
                _rebuild(
                    dim,
                    applicability=Applicability.UNCERTAIN,
                    score=None,
                    rating=Rating.UNCERTAIN,
                    confidence=Confidence.LOW,
                    uncertainty_reason=_REASON,
                )
            )
            msg = (
                f"Dimension '{dim.dimension_id}' lacks valid evidence and was downgraded to uncertain "
                "(score set to null)"
            )
        else:
            new_dims.append(_rebuild(dim, confidence=Confidence.LOW))
            msg = (
                f"Dimension '{dim.dimension_id}' lacks valid evidence; "
                "the score was kept and confidence lowered to low"
            )

        issues.append(
            ValidationIssue(
                code="DIMENSION_DEGRADED",
                path=f"dimensions[{i}]",
                message=msg,
                severity=Severity.REPAIRED,
            )
        )

    report.dimensions = new_dims
    return issues
