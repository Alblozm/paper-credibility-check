"""契约 §10.3 / §10.4 / §10.5 / §16.1 的分数计算。

设计决定：**模型返回的 overall_score、rating、is_provisional 一律丢弃并重算。**
模型的算术不可信，而这部分 100% 可自动化。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.schemas.enums import Applicability, Rating
from app.schemas.report import DimensionAssessment, OverallCalculation

MIN_ASSESSED_DIMENSIONS = 3  # 契约 §10.4


def rating_for_score(score: int) -> Rating:
    """契约 §10.3 维度分数 → 等级。"""
    if not 1 <= score <= 10:
        raise ValueError(f"dimension score out of range: {score}")
    if score >= 9:
        return Rating.STRONG
    if score >= 7:
        return Rating.GOOD
    if score >= 5:
        return Rating.ADEQUATE
    return Rating.WEAK


def rating_for_overall(score: float | None) -> Rating:
    """契约 §16.1 总分 → 等级。"""
    if score is None:
        return Rating.UNABLE_TO_ASSESS
    if score >= 9.0:
        return Rating.STRONG
    if score >= 7.0:
        return Rating.GOOD
    if score >= 5.0:
        return Rating.ADEQUATE
    return Rating.WEAK


def compute_overall_score(
    dimensions: list[DimensionAssessment],
) -> tuple[float | None, Rating, OverallCalculation]:
    """契约 §10.5：已评估维度的归一化加权平均，保留一位小数。

    uncertain / not_applicable 不参与计算；有效维度少于三个时返回 None +
    unable_to_assess。
    """
    included = [
        d
        for d in dimensions
        if d.applicability is Applicability.APPLICABLE and d.score is not None
    ]
    excluded = [d.dimension_id for d in dimensions if d not in included]

    calculation = OverallCalculation(
        method="weighted_average",
        assessed_dimension_count=len(included),
        excluded_dimension_ids=excluded,
    )

    if len(included) < MIN_ASSESSED_DIMENSIONS:
        return None, Rating.UNABLE_TO_ASSESS, calculation

    # 全程用 Decimal：float 会让 (8+7+8+6)/4 算成 7.249999999999999，
    # 再舍入就变成 7.2，与手算结果对不上。
    weight_sum = sum(Decimal(str(d.weight)) for d in included)
    if weight_sum <= 0:
        return None, Rating.UNABLE_TO_ASSESS, calculation

    raw = sum(Decimal(d.score) * Decimal(str(d.weight)) for d in included) / weight_sum
    # ROUND_HALF_UP 而不是内置 round()：Python 的 round(7.25, 1) 是 7.2（银行家舍入），
    # 对用户可见的分数来说反直觉。
    score = float(raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return score, rating_for_overall(score), calculation


def compute_is_provisional(dimensions: list[DimensionAssessment]) -> bool:
    """契约 §10.4：任一维度 uncertain 或 not_applicable 即为暂定结果。"""
    return any(d.applicability is not Applicability.APPLICABLE for d in dimensions)
