"""Live-claim API wiring stays offline while exercising real routing/validation."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routes_credibility
from app.config import get_settings
from app.credibility.claims import ClaimCandidateSet
from app.credibility.context import CheckContext
from app.credibility.models import CredibilityModelCall
from app.llm.retry import LLMOutputInvalid, LLMUnavailable
from app.main import app
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.paper import Paper, Section

client = TestClient(app)


@pytest.fixture
def live_ctx(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_provider", "deepseek")
    monkeypatch.setattr(get_settings(), "deepseek_api_key", "offline-test-key")
    monkeypatch.setattr(routes_credibility, "_TASKS", {})
    paper = Paper(
        paper_id="fresh_paper", title="Fresh research", authors=[], language="en", page_count=2,
        abstract="Our model improves accuracy.", arxiv_id="2503.12345v1", doi=None,
        sections=[Section(section_id="abs", title="Abstract", level=1, page_start=1, page_end=1)],
        parse_quality="good", parse_warnings=[],
    )
    doc = ParsedDocument(
        paper=paper, source_sha256="f" * 64, parser_name="live-test",
        blocks=[
            TextBlock(
                text="Our model improves accuracy.", page_start=1, chunk_id="fresh_abs",
                section_id="abs", section_title="Abstract", section_role="abstract",
            ),
            TextBlock(
                text="Accuracy increased from 80 to 84 under the same test protocol.", page_start=2,
                chunk_id="fresh_results", section_title="Results", section_role="results",
            ),
        ],
    )
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2503.12345v1", "upload_id": None},
        repo_root=Path("/isolated-test"), generated_at="2026-10-19T00:00:00Z",
        ref=None, doc=doc, unresolved_reason=None,
    )
    monkeypatch.setattr("app.credibility.shell.build_context", lambda *args, **kwargs: ctx)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: model_id)

    def forbidden(*args, **kwargs):
        raise AssertionError("A live request must never read prepared claims")

    monkeypatch.setattr("app.credibility.claims.load_prepared_claims", forbidden)
    return ctx


def _request(model_id="model_standard_v1"):
    return {
        "schema_version": "credibility-0.2.0-draft", "analysis_mode": "credibility",
        "source": {"type": "arxiv", "arxiv_id": "2503.12345v1", "upload_id": None},
        "model_id": model_id, "rubric_version": "0.1-draft",
    }


def _post_and_poll():
    response = client.post("/v1/credibility/assessments", json=_request())
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    return client.get("/v1/credibility/assessments/" + response.json()["assessment_id"]).json()


def _generation(ctx):
    candidate = ClaimCandidateSet.model_validate({
        "paper_version_id": ctx.paper_version_id, "origin": "model",
        "selection_scope": "Abstract and introduction verifiable claims.", "scope_complete": True,
        "scope_limitations": [], "claims": [{
            "claim_id": "fresh_c1", "statement": "Our model improves accuracy.", "importance": "core",
            "claim_conditions": {"metric": "accuracy", "dataset": None, "baseline": None, "setting": "same test"},
            "claim_evidence": [{
                "evidence_id": "fresh_claim", "mode": "quote", "chunk_id": "fresh_abs",
                "excerpt": "Our model improves accuracy.",
            }],
            "comparison_evidence": [{
                "evidence_id": "fresh_comparison", "mode": "quote", "chunk_id": "fresh_results",
                "excerpt": "Accuracy increased from 80 to 84 under the same test protocol.",
                "conditions": {"metric": "accuracy", "dataset": None, "baseline": None, "setting": "same test"},
            }],
            "search_scope": "All supplied results blocks.", "proposed_outcome": "supported",
            "reason": "The stated increase appears in the results under the same test protocol.",
            "review": {"status": "pending", "reviewer": None, "reviewed_at": None, "note": None},
        }],
    })
    return SimpleNamespace(
        candidates=candidate, prompt_version="live-claim-test-v1",
        model_calls=[
            CredibilityModelCall(purpose=purpose, provider="deepseek", model_version="test-real-model-route")
            for purpose in ["claim_extraction", "claim_judgment"]
        ],
    )


def test_live_api_uses_generated_claims_and_reports_actual_model_metadata(live_ctx, monkeypatch):
    calls = []

    def generate(ctx, model_client, **kwargs):
        assert ctx.model_id == model_client == "model_standard_v1"
        calls.append(ctx.paper_version_id)
        return _generation(ctx)

    monkeypatch.setattr("app.credibility.claim_model.generate_claim_candidates", generate)
    result = _post_and_poll()
    assert result["status"] == "completed" and result["stage"] == "done"
    assert result["paper"]["paper_id"] == "fresh_paper"
    assert calls == [live_ctx.paper_version_id]
    report = result["report"]
    assert report["versions"]["prompt_version"] == "live-claim-test-v1"
    assert [call["purpose"] for call in report["versions"]["model_calls"]] == [
        "claim_extraction", "claim_judgment",
    ]
    assert report["checks"]["claims"]["items"][0]["claim_id"] == "fresh_c1"
    assert {e["paper_locator"]["page_start"] for e in report["evidence"]} == {1, 2}
    assert not any("No model was called" in text for text in report["limitations"])
    assert "claims_not_prepared" not in {w["code"] for w in result["warnings"]}
    assert report["findings"][0]["certainty"] == "unresolved"


@pytest.mark.parametrize("error,code", [
    (LLMUnavailable("provider unavailable"), "MODEL_UNAVAILABLE"),
    (LLMOutputInvalid("invalid model output"), "MODEL_OUTPUT_INVALID"),
])
def test_live_model_failure_is_isolated_without_prepared_fallback(live_ctx, monkeypatch, error, code):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("app.credibility.claim_model.generate_claim_candidates", fail)
    result = _post_and_poll()
    assert result["status"] == "completed" and result["error"] is None
    report = result["report"]
    assert report["dimensions"][0]["check_status"] == "failed"
    assert report["checks"]["claims"]["items"] == []
    assert report["execution"]["checks"]["claim_check"]["error"]["code"] == code
    # This fixture has no reference section; that producer independently
    # returns extraction failure. Do not hide it as completed merely because
    # it has no external sources, or attribute it to the model exception.
    reference_event = report["execution"]["checks"]["reference_check"]
    assert reference_event["status"] == report["dimensions"][1]["check_status"] == "failed"
    assert reference_event["reused"] is False and "error" not in reference_event
    assert report["execution"]["checks"]["code_check"]["status"] != "failed"


def test_live_parse_failure_is_not_a_completed_synthetic_report(live_ctx):
    live_ctx.doc = None
    live_ctx.unresolved_reason = "Unreadable paper"
    result = _post_and_poll()
    assert result["status"] == "failed" and result["error"]["code"] == "PARSE_FAILED"
    assert result["report"] is None


def test_production_request_cannot_select_prepared_demo(live_ctx):
    response = client.post("/v1/credibility/assessments", json=_request("not_called"))
    assert response.status_code == 400
    assert routes_credibility._TASKS == {}


def test_unavailable_model_is_rejected_before_job_creation():
    response = client.post("/v1/credibility/assessments", json=_request())
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "OPTION_UNAVAILABLE"
