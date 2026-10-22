"""契约 §15.4 证据验收 + 降级 + 交叉校验 + 总分重算。"""
from __future__ import annotations

import copy
import json

import pytest

from app.schemas.enums import Applicability, ChecklistStatus, Confidence, Rating
from app.validation.degradation import DegradationPolicy
from app.validation.report_validator import validate_report_payload

SOURCE = (
    "We propose a new method. Code is available at https://github.com/example/repo. "
    "We train with learning rate 1e-4 and batch size 64 on the CIFAR dataset. "
    "Our method improves accuracy by 2.1 points over the strongest baseline."
)


def test_valid_report_passes(example_report):
    result = validate_report_payload(example_report)
    assert result.ok
    assert result.report.overall_assessment.overall_score == 7.2


def test_non_json_string_is_reported(example_report):
    result = validate_report_payload("not json at all {")
    assert not result.ok
    assert result.issues[0].code == "MODEL_OUTPUT_NOT_JSON"


def test_dangling_evidence_link_is_dropped(example_report):
    example_report["dimensions"][0]["evidence_links"][0]["evidence_id"] = "ev_does_not_exist"
    result = validate_report_payload(example_report)
    codes = {i.code for i in result.issues}
    assert "EVIDENCE_DANGLING_REF" in codes
    # 悬空 link 被删掉后该维度没有证据，随即触发降级
    assert "DIMENSION_DEGRADED" in codes


def test_degradation_to_uncertain(example_report):
    example_report["dimensions"][1]["evidence_links"] = []
    result = validate_report_payload(example_report, policy=DegradationPolicy.TO_UNCERTAIN)
    dim = result.report.dimensions[1]
    assert dim.applicability is Applicability.UNCERTAIN
    assert dim.score is None
    assert dim.rating is Rating.UNCERTAIN
    assert dim.uncertainty_reason


def test_degradation_lower_confidence_keeps_score(example_report):
    example_report["dimensions"][1]["evidence_links"] = []
    result = validate_report_payload(example_report, policy=DegradationPolicy.LOWER_CONFIDENCE)
    dim = result.report.dimensions[1]
    assert dim.score == 7
    assert dim.confidence is Confidence.LOW


def test_overall_recomputed_after_degradation(example_report):
    """维度被降级后总分必须跟着变，且 is_provisional 置为 true。"""
    example_report["dimensions"][1]["evidence_links"] = []
    result = validate_report_payload(example_report)
    overall = result.report.overall_assessment
    assert overall.overall_score == 7.3          # (8+7+8+6)/4
    assert overall.is_provisional is True
    assert overall.calculation.assessed_dimension_count == 4


def test_model_supplied_overall_score_is_ignored(example_report):
    """模型算错也没关系——后端一律重算。"""
    example_report["overall_assessment"]["overall_score"] = 9.9
    example_report["overall_assessment"]["rating"] = "strong"
    result = validate_report_payload(example_report)
    assert result.report.overall_assessment.overall_score == 7.2
    assert result.report.overall_assessment.rating is Rating.GOOD


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    "computed_fields",
    [
        {"overall_score": None, "rating": "good"},
        {
            "overall_score": "not a score",
            "rating": "not a rating",
            "is_provisional": "not a boolean",
            "calculation": {
                "method": None,
                "assessed_dimension_count": 999,
                "excluded_dimension_ids": "not an array",
            },
        },
        {"overall_score": 100, "rating": "strong", "calculation": None},
    ],
)
def test_backend_owned_overall_fields_cannot_block_report(example_report, computed_fields, as_json):
    example_report["overall_assessment"].update(computed_fields)
    before = copy.deepcopy(example_report)
    raw = json.dumps(example_report) if as_json else example_report

    result = validate_report_payload(raw)

    assert result.ok
    overall = result.report.overall_assessment
    assert overall.overall_score == 7.2
    assert overall.rating is Rating.GOOD
    assert overall.is_provisional is False
    assert overall.calculation.model_dump() == {
        "method": "weighted_average", "assessed_dimension_count": 5, "excluded_dimension_ids": [],
    }
    for field in ("summary", "reading_recommendation", "recommendation_reason", "confidence"):
        assert overall.model_dump(mode="json")[field] == before["overall_assessment"][field]
    assert example_report == before


def test_missing_backend_owned_overall_fields_are_recomputed(example_report):
    for field in ("overall_score", "rating", "is_provisional", "calculation"):
        del example_report["overall_assessment"][field]

    result = validate_report_payload(example_report)

    assert result.ok
    assert result.report.overall_assessment.overall_score == 7.2
    assert result.report.overall_assessment.calculation.assessed_dimension_count == 5


def test_overall_placeholder_normalization_preserves_evidence_gate(example_report):
    example_report["overall_assessment"].update(overall_score=None, rating="good")
    # Removing evidence must still remove scored dimensions from the final average.
    example_report["evidence"] = []
    example_report["strengths"] = []
    example_report["concerns"] = []
    before = copy.deepcopy(example_report)

    result = validate_report_payload(example_report)

    assert result.ok
    assert all(d.applicability is Applicability.UNCERTAIN for d in result.report.dimensions)
    overall = result.report.overall_assessment
    assert overall.overall_score is None
    assert overall.rating is Rating.UNABLE_TO_ASSESS
    assert overall.is_provisional is True
    assert overall.calculation.assessed_dimension_count == 0
    assert sum(i.code == "DIMENSION_DEGRADED" for i in result.issues) == 5
    assert example_report == before


@pytest.mark.parametrize("field", ["summary", "reading_recommendation", "recommendation_reason", "confidence"])
def test_overall_judgment_fields_remain_required(example_report, field):
    example_report["overall_assessment"].update(overall_score=None, rating="good")
    del example_report["overall_assessment"][field]

    result = validate_report_payload(example_report)

    assert not result.ok
    assert any(i.path == f"overall_assessment.{field}" and i.code == "SCHEMA_MISSING" for i in result.issues)


@pytest.mark.parametrize("container", ["report", "overall_assessment", "calculation"])
def test_overall_normalization_does_not_hide_extra_fields(example_report, container):
    example_report["overall_assessment"].update(overall_score=None, rating="good")
    target = example_report if container == "report" else example_report["overall_assessment"]
    if container == "calculation":
        target = target["calculation"]
    target["unexpected_field"] = "must remain an error"

    result = validate_report_payload(example_report)

    assert not result.ok
    assert any(i.code == "SCHEMA_EXTRA_FORBIDDEN" and i.path.endswith("unexpected_field") for i in result.issues)


def test_overall_normalization_does_not_hide_dimension_errors(example_report):
    example_report["overall_assessment"].update(overall_score=None, rating="good")
    example_report["dimensions"][0]["score"] = None

    result = validate_report_payload(example_report)

    assert not result.ok
    assert any(i.path == "dimensions.0" for i in result.blocking_issues)


def test_missing_evidence_on_major_finding_still_blocks_report(example_report):
    example_report["overall_assessment"].update(overall_score=None, rating="good")
    example_report["strengths"][0]["evidence_links"] = []

    result = validate_report_payload(example_report)

    assert not result.ok
    assert any(i.code == "MAJOR_FINDING_WITHOUT_EVIDENCE" for i in result.blocking_issues)


@pytest.mark.parametrize("overall", [None, "not an object"])
def test_overall_assessment_container_still_requires_an_object(example_report, overall):
    example_report["overall_assessment"] = overall

    result = validate_report_payload(example_report)

    assert not result.ok
    assert any(i.path == "overall_assessment" for i in result.blocking_issues)


def test_versioned_rubric_weights_override_model_weights(example_report):
    weights = {
        "research_value": 0.4,
        "soundness_evidence": 0.15,
        "originality_positioning": 0.15,
        "clarity_usability": 0.15,
        "reproducibility_transparency": 0.15,
    }
    result = validate_report_payload(example_report, dimension_weights=weights)
    assert result.ok
    assert {dimension.dimension_id: dimension.weight for dimension in result.report.dimensions} == weights
    assert any(issue.code == "RUBRIC_WEIGHT_CORRECTED" for issue in result.issues)


def test_quote_not_found_downgrades_location_confidence(example_report):
    example_report["evidence"][0]["evidence_mode"] = "quote"
    example_report["evidence"][0]["excerpt"] = "This sentence never appears in the paper at all."
    result = validate_report_payload(example_report, source_text=SOURCE)
    assert result.report.evidence[0].location_confidence is Confidence.LOW
    assert any(i.code == "EVIDENCE_EXCERPT_NOT_FOUND" for i in result.issues)


def test_quote_found_keeps_confidence(example_report):
    example_report["evidence"][1]["evidence_mode"] = "quote"
    example_report["evidence"][1]["excerpt"] = "Our method improves accuracy by 2.1 points"
    result = validate_report_payload(example_report, source_text=SOURCE)
    assert result.report.evidence[1].location_confidence is Confidence.HIGH


# 2026-10-20 真实 pypdf 抽取（paper_002 / paper_041）：原文都在论文里，只是排版不同
PDF_SOURCE = (
    "In the context of reasoning, our research indicates that LLMs struggle to self-\n"
    "correct their responses without external feedback.\n"
    "The title, “ Large Language Models Cannot Self-Correct Reasoning Yet ”, is\n"
    "not an outright dismissal of self-correction techniques.\n"
    "Ground truth values inOPEN ES-\nTIMATE were estimated from finite samples."
)


@pytest.mark.parametrize("excerpt", [
    "LLMs struggle to self-correct their responses without external feedback.",
    "The title, “Large Language Models Cannot Self-Correct Reasoning Yet”, is not an outright dismissal",
    "Ground truth values in OPEN ESTIMATE were estimated from finite samples.",
])
def test_pdf_typography_quote_keeps_confidence(example_report, excerpt):
    example_report["evidence"][1]["evidence_mode"] = "quote"
    example_report["evidence"][1]["excerpt"] = excerpt
    result = validate_report_payload(example_report, source_text=PDF_SOURCE)
    assert result.report.evidence[1].location_confidence is Confidence.HIGH
    assert not any(i.code == "EVIDENCE_EXCERPT_NOT_FOUND" for i in result.issues)


@pytest.mark.parametrize("excerpt", [
    "Ground truth values in OPEN ESTIMATE were estimated from 5 finite samples.",
    "Reasoning Yet”, is an outright dismissal of self-correction techniques.",
    "LLMs manage to self-correct their responses without external feedback.",
])
def test_spacing_tolerance_does_not_accept_changed_quote(example_report, excerpt):
    example_report["evidence"][1]["evidence_mode"] = "quote"
    example_report["evidence"][1]["excerpt"] = excerpt
    result = validate_report_payload(example_report, source_text=PDF_SOURCE)
    assert result.report.evidence[1].location_confidence is Confidence.LOW


def test_invented_page_number_is_nulled(example_report):
    result = validate_report_payload(example_report, known_pages={1, 2, 3})
    ev = {e.evidence_id: e for e in result.report.evidence}
    assert ev["ev_02"].page_number is None       # 原本是 6
    assert ev["ev_01"].page_number == 1
    assert any(i.code == "EVIDENCE_PAGE_UNKNOWN" for i in result.issues)


def test_checklist_available_without_signal_is_downgraded(example_report):
    """模型说有随机种子，但原文里没有 —— 降为 unable_to_verify。"""
    example_report["reproducibility_checklist"][2]["status"] = "available"
    result = validate_report_payload(example_report, source_text=SOURCE)
    seed_check = result.report.reproducibility_checklist[2]
    assert seed_check.status is ChecklistStatus.UNABLE_TO_VERIFY
    assert any(i.code == "CHECKLIST_DOWNGRADED" for i in result.issues)


def test_checklist_with_signal_is_kept(example_report):
    result = validate_report_payload(example_report, source_text=SOURCE)
    assert result.report.reproducibility_checklist[0].status is ChecklistStatus.AVAILABLE


def test_repaired_issues_become_warnings(example_report):
    example_report["dimensions"][0]["evidence_links"][0]["evidence_id"] = "ev_nope"
    result = validate_report_payload(example_report)
    warnings = result.to_warnings()
    assert warnings
    assert {w.code for w in warnings} & {"EVIDENCE_DROPPED", "DIMENSION_DEGRADED"}
