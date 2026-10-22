"""契约 §10.2 / §11 的结构约束。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.enums import CHECKLIST_IDS, DIMENSION_IDS
from app.schemas.report import DimensionAssessment, Report


def test_contract_example_is_valid(example_report):
    report = Report.model_validate(example_report)
    assert [d.dimension_id for d in report.dimensions] == list(DIMENSION_IDS)
    assert sorted(c.check_id for c in report.reproducibility_checklist) == sorted(CHECKLIST_IDS)


def test_applicable_dimension_requires_score(example_report):
    bad = example_report["dimensions"][0] | {"score": None}
    with pytest.raises(ValidationError):
        DimensionAssessment.model_validate(bad)


def test_uncertain_dimension_requires_reason(example_report):
    bad = example_report["dimensions"][0] | {
        "applicability": "uncertain",
        "score": None,
        "rating": "uncertain",
        "uncertainty_reason": "",
    }
    with pytest.raises(ValidationError):
        DimensionAssessment.model_validate(bad)


def test_uncertain_dimension_rejects_score(example_report):
    bad = example_report["dimensions"][0] | {
        "applicability": "uncertain",
        "rating": "uncertain",
        "uncertainty_reason": "解析不完整",
    }
    with pytest.raises(ValidationError):
        DimensionAssessment.model_validate(bad)


def test_score_zero_is_rejected(example_report):
    """契约 §10.4：禁止用 0 表示无法判断。"""
    bad = example_report["dimensions"][0] | {"score": 0}
    with pytest.raises(ValidationError):
        DimensionAssessment.model_validate(bad)


def test_missing_dimension_is_rejected(example_report):
    example_report["dimensions"] = example_report["dimensions"][:4]
    with pytest.raises(ValidationError):
        Report.model_validate(example_report)


def test_extra_field_is_rejected(example_report):
    """同义不同名的字段不能悄悄混进来。"""
    example_report["result_status"] = "ok"
    with pytest.raises(ValidationError):
        Report.model_validate(example_report)
