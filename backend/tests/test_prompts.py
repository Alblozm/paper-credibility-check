from __future__ import annotations

from app.llm.prompts import PROMPT_VERSION, REPORT_SCHEMA_GUIDE, SYSTEM_PROMPT, build_analysis_prompt
from app.parsing.anchors import build_anchored_text
from app.parsing.grobid import StubParser
from app.schemas.enums import CHECKLIST_IDS, DIMENSION_IDS


def test_system_prompt_states_product_and_evidence_boundaries():
    assert PROMPT_VERSION == "analysis-v0.4"
    assert "valid JSON object only" in SYSTEM_PROMPT
    assert "not an official ICLR review" in SYSTEM_PROMPT
    assert "Do not predict accept/reject" in SYSTEM_PROMPT
    assert "external SOTA leaderboards" in SYSTEM_PROMPT
    assert "Copy the chunk id" in SYSTEM_PROMPT
    assert "Do not invent page numbers" in SYSTEM_PROMPT


def test_report_contract_contains_exact_nested_fields():
    """The real-model prompt must not refer to undefined Python object names only."""
    required_fields = {
        '"display_name"',
        '"applicability"',
        '"confidence"',
        '"detail"',
        '"title"',
        '"importance"',
        '"why_it_matters"',
    }
    for field in required_fields:
        assert field in REPORT_SCHEMA_GUIDE
    assert "[five fixed DimensionAssessment objects]" not in REPORT_SCHEMA_GUIDE
    assert "[six fixed ReproducibilityCheck objects]" not in REPORT_SCHEMA_GUIDE
    assert '"additionalProperties":false' in REPORT_SCHEMA_GUIDE


def test_analysis_prompt_contains_contract_rubric_and_anchors():
    doc = StubParser().parse_arxiv("2501.01234")
    anchored_text = build_anchored_text(doc)

    prompt = build_analysis_prompt(doc, anchored_text)

    assert "Paper metadata:" in prompt
    assert "parse_quality: partial" in prompt
    assert "Full paper text with location anchors:" in prompt
    assert "[p1|paper_2501.01234_chunk_000]" in prompt
    assert "validates against this JSON Schema" in prompt

    for dimension_id in DIMENSION_IDS:
        assert dimension_id in prompt
    for checklist_id in CHECKLIST_IDS:
        assert checklist_id in prompt

    assert "## Common score anchors" in prompt
    assert "VERSIONED RUBRIC (iclr_inspired_due_diligence / 0.1-draft" in prompt
    assert "SOTA" in prompt
    assert "unable_to_verify" in prompt
