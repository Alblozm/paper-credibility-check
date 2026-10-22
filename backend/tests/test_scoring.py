"""契约 §10.3 / §10.4 / §10.5 / §16.1。"""
from __future__ import annotations

import pytest

from app.schemas.enums import Rating
from app.schemas.report import Report
from app.validation.scoring import (
    compute_is_provisional,
    compute_overall_score,
    rating_for_overall,
    rating_for_score,
)


@pytest.mark.parametrize(
    "score,expected",
    [(10, Rating.STRONG), (9, Rating.STRONG), (8, Rating.GOOD), (7, Rating.GOOD),
     (6, Rating.ADEQUATE), (5, Rating.ADEQUATE), (4, Rating.WEAK), (1, Rating.WEAK)],
)
def test_rating_for_score(score, expected):
    assert rating_for_score(score) is expected


def test_rating_for_overall_boundaries():
    assert rating_for_overall(9.0) is Rating.STRONG
    assert rating_for_overall(8.9) is Rating.GOOD
    assert rating_for_overall(4.9) is Rating.WEAK
    assert rating_for_overall(None) is Rating.UNABLE_TO_ASSESS


def test_weighted_average_matches_contract_example(example_report):
    report = Report.model_validate(example_report)
    score, rating, calc = compute_overall_score(report.dimensions)
    assert score == 7.2                      # (8+7+7+8+6)/5
    assert rating is Rating.GOOD
    assert calc.assessed_dimension_count == 5
    assert calc.excluded_dimension_ids == []


def test_fewer_than_three_scored_dimensions_is_unable_to_assess(example_report):
    for dim in example_report["dimensions"][:3]:
        dim.update(
            applicability="uncertain",
            score=None,
            rating="uncertain",
            uncertainty_reason="解析不完整，无法判断",
        )
    report = Report.model_validate(example_report)
    score, rating, calc = compute_overall_score(report.dimensions)
    assert score is None
    assert rating is Rating.UNABLE_TO_ASSESS
    assert calc.assessed_dimension_count == 2
    assert len(calc.excluded_dimension_ids) == 3


def test_half_up_rounding(example_report):
    """7.25 必须进位到 7.3，而不是内置 round() 的 7.2。"""
    example_report["dimensions"][1].update(
        applicability="uncertain", score=None, rating="uncertain",
        uncertainty_reason="证据不足",
    )
    report = Report.model_validate(example_report)
    score, _, _ = compute_overall_score(report.dimensions)
    assert score == 7.3          # (8+7+8+6)/4 = 7.25


def test_is_provisional_when_any_dimension_not_applicable(example_report):
    report = Report.model_validate(example_report)
    assert compute_is_provisional(report.dimensions) is False

    example_report["dimensions"][4].update(
        applicability="not_applicable",
        score=None,
        rating="not_applicable",
        summary="理论论文，没有实验可供复现性评估。",
        uncertainty_reason=None,
    )
    report2 = Report.model_validate(example_report)
    assert compute_is_provisional(report2.dimensions) is True
