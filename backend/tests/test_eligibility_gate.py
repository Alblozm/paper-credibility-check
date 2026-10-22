from __future__ import annotations

from app.eligibility.gate import EligibilityDecision, evaluate_eligibility
from app.llm.fixture import FixtureLLMClient, ScriptedLLMClient
from app.parsing.grobid import StubParser
from app.schemas.enums import ParseQuality


def _document():
    return StubParser().parse_arxiv("2501.01234")


def test_clear_ml_paper_is_eligible_after_one_bounded_call():
    client = FixtureLLMClient()
    result = evaluate_eligibility(_document(), client)
    assert result.decision is EligibilityDecision.ELIGIBLE
    assert result.classifier_calls == 1
    assert result.evidence_chunk_ids
    assert "bounded eligibility classifier" in client.calls[0][0]


def test_non_ml_classification_never_becomes_a_scoreable_document():
    client = FixtureLLMClient(eligibility_payload={
        "document_type": "research_paper",
        "language": "english",
        "ml_relevance": "no",
        "confidence": "high",
        "reason": "The paper studies geological strata without an ML method.",
        "evidence_chunk_ids": [],
    })
    result = evaluate_eligibility(_document(), client)
    assert result.decision is EligibilityDecision.NOT_ML_PAPER
    assert result.classifier_calls == 1


def test_uncertain_result_gets_exactly_one_targeted_recheck():
    uncertain = {
        "document_type": "research_paper",
        "language": "english",
        "ml_relevance": "uncertain",
        "confidence": "low",
        "reason": "The initial excerpt is ambiguous.",
        "evidence_chunk_ids": [],
    }
    eligible = {
        "document_type": "research_paper",
        "language": "english",
        "ml_relevance": "yes",
        "confidence": "medium",
        "reason": "The targeted excerpt describes model training and evaluation.",
        "evidence_chunk_ids": [],
    }
    client = ScriptedLLMClient([uncertain, eligible])
    result = evaluate_eligibility(_document(), client)
    assert result.decision is EligibilityDecision.ELIGIBLE
    assert result.classifier_calls == 2
    assert len(client.calls) == 2
    assert "targeted relevance re-check" in client.calls[1][1]


def test_insufficient_text_fails_without_calling_model():
    doc = _document()
    doc.blocks = doc.blocks[:1]
    doc.blocks[0].text = "too short"
    client = FixtureLLMClient()
    result = evaluate_eligibility(doc, client)
    assert result.decision is EligibilityDecision.INSUFFICIENT_CONTENT
    assert result.classifier_calls == 0
    assert client.calls == []


def test_poor_parse_fails_without_calling_model():
    doc = _document()
    doc.parse_quality = ParseQuality.POOR
    client = FixtureLLMClient()
    result = evaluate_eligibility(doc, client)
    assert result.decision is EligibilityDecision.POOR_PARSE_QUALITY
    assert client.calls == []


def test_unknown_classifier_chunk_is_conservatively_rejected():
    client = FixtureLLMClient(eligibility_payload={
        "document_type": "research_paper",
        "language": "english",
        "ml_relevance": "yes",
        "confidence": "high",
        "reason": "The text describes machine learning.",
        "evidence_chunk_ids": ["invented_chunk_999"],
    })
    result = evaluate_eligibility(_document(), client)
    assert result.decision is EligibilityDecision.UNABLE_TO_CONFIRM
    assert result.classifier_calls == 2


def test_model_detected_non_english_paper_is_rejected():
    client = FixtureLLMClient(eligibility_payload={
        "document_type": "research_paper",
        "language": "not_english",
        "ml_relevance": "yes",
        "confidence": "high",
        "reason": "The document is written primarily in French.",
        "evidence_chunk_ids": [],
    })
    result = evaluate_eligibility(_document(), client)
    assert result.decision is EligibilityDecision.UNSUPPORTED_LANGUAGE
    assert result.classifier_calls == 1
