"""Specific, English input errors and the shared ML gate for the credibility route."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes_credibility
from app.config import get_settings
from app.credibility.context import CheckContext, ParseFailure, _parse_live
from app.eligibility.gate import EligibilityDecision, EligibilityResult
from app.main import app
from app.parsing.arxiv import ArxivDownloadError, ArxivRetriever
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import Confidence
from app.schemas.paper import Paper, Section

client = TestClient(app)


def _request(source):
    return {"analysis_mode": "credibility", "source": source, "model_id": "model_standard_v1"}


@pytest.fixture
def live_model(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_provider", "deepseek")
    monkeypatch.setattr(get_settings(), "deepseek_api_key", "offline-test-key")
    monkeypatch.setattr(routes_credibility, "_TASKS", {})
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: model_id)


def _ctx():
    paper = Paper(
        paper_id="ricci", title="The entropy formula for the Ricci flow", authors=[], language="en",
        page_count=2, abstract="We study the Ricci flow.", arxiv_id="math/0211159", doi=None,
        sections=[Section(section_id="abs", title="Abstract", level=1, page_start=1, page_end=1)],
        parse_quality="good", parse_warnings=[],
    )
    doc = ParsedDocument(
        paper=paper, source_sha256="a" * 64, parser_name="live-test",
        blocks=[TextBlock(text="We study the Ricci flow.", page_start=1, chunk_id="abs_1",
                          section_id="abs", section_title="Abstract", section_role="abstract")],
    )
    return CheckContext(
        source={"type": "arxiv", "arxiv_id": "math/0211159", "upload_id": None},
        repo_root=Path("/isolated-test"), generated_at="2026-10-20T00:00:00Z",
        ref=None, doc=doc, unresolved_reason=None,
    )


def _verdict(decision):
    return EligibilityResult(decision=decision, confidence=Confidence.HIGH, reason_code="TEST", reason="test")


def test_invalid_arxiv_id_is_rejected_before_queueing(live_model):
    response = client.post("/v1/credibility/assessments",
                           json=_request({"type": "arxiv", "arxiv_id": "notanid", "upload_id": None}))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_ARXIV_ID"


def test_missing_upload_is_rejected_before_queueing(live_model):
    response = client.post("/v1/credibility/assessments",
                           json=_request({"type": "pdf", "arxiv_id": None, "upload_id": "upl_" + "0" * 32}))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UPLOAD_NOT_FOUND"


def test_original_url_is_accepted_and_stored_as_a_normalized_id(live_model, monkeypatch):
    monkeypatch.setattr(routes_credibility, "_run", lambda *args, **kwargs: None)
    response = client.post("/v1/credibility/assessments", json=_request({
        "type": "arxiv", "arxiv_id": None, "original_url": "https://arxiv.org/abs/2106.09685", "upload_id": None,
    }))
    assert response.status_code == 202
    assert response.json()["source"] == {"type": "arxiv", "arxiv_id": "2106.09685", "upload_id": None}


def test_validation_and_routing_errors_share_the_code_message_envelope():
    invalid = client.post("/v1/credibility/assessments", json={})
    assert invalid.status_code == 422
    detail = invalid.json()["detail"]
    assert detail["code"] == "INVALID_REQUEST" and detail["message"].startswith("The request is invalid")
    assert detail["errors"]
    not_allowed = client.delete("/v1/credibility/assessments")
    assert not_allowed.status_code == 405
    assert not_allowed.json()["detail"]["code"] == "HTTP_405"


def test_non_ml_paper_fails_the_gate_before_any_check(live_model, monkeypatch):
    monkeypatch.setattr(get_settings(), "credibility_eligibility_gate_enabled", True)
    monkeypatch.setattr("app.credibility.shell.build_context", lambda *args, **kwargs: _ctx())
    monkeypatch.setattr("app.eligibility.gate.evaluate_eligibility",
                        lambda *args, **kwargs: _verdict(EligibilityDecision.NOT_ML_PAPER))

    def forbidden(*args, **kwargs):
        raise AssertionError("an out-of-scope paper must not be checked")

    monkeypatch.setattr("app.credibility.shell.assemble_report", forbidden)
    created = client.post("/v1/credibility/assessments",
                          json=_request({"type": "arxiv", "arxiv_id": "math/0211159", "upload_id": None}))
    task = client.get("/v1/credibility/assessments/" + created.json()["assessment_id"]).json()
    assert task["status"] == "failed" and task["report"] is None
    assert task["error"]["code"] == "UNABLE_TO_ASSESS" and task["error"]["retryable"] is False
    assert "machine-learning paper" in task["error"]["message"]
    assert task["paper"]["title"] == "The entropy formula for the Ricci flow"


def test_eligible_paper_continues_to_the_checks(live_model, monkeypatch):
    monkeypatch.setattr(get_settings(), "credibility_eligibility_gate_enabled", True)
    monkeypatch.setattr("app.credibility.shell.build_context", lambda *args, **kwargs: _ctx())
    calls = []
    monkeypatch.setattr("app.eligibility.gate.evaluate_eligibility",
                        lambda *args, **kwargs: calls.append(1) or _verdict(EligibilityDecision.ELIGIBLE))

    def reached(*args, **kwargs):
        raise RuntimeError("checks reached")

    monkeypatch.setattr("app.credibility.shell.assemble_report", reached)
    created = client.post("/v1/credibility/assessments",
                          json=_request({"type": "arxiv", "arxiv_id": "1706.03762", "upload_id": None}))
    task = client.get("/v1/credibility/assessments/" + created.json()["assessment_id"]).json()
    assert calls == [1]
    assert task["error"]["code"] == "INTERNAL_ERROR"


def test_page_limit_reaches_the_credibility_task_as_a_specific_error(live_model, monkeypatch):
    def unavailable(*args, **kwargs):
        return CheckContext(
            source={}, repo_root=Path("/isolated-test"), generated_at="t", ref=None, doc=None,
            unresolved_reason=ParseFailure("The paper has 116 pages; the limit is 60.", code="PAGE_LIMIT_EXCEEDED"),
        )

    monkeypatch.setattr("app.credibility.shell.build_context", unavailable)
    created = client.post("/v1/credibility/assessments",
                          json=_request({"type": "arxiv", "arxiv_id": "2310.02255v3", "upload_id": None}))
    task = client.get("/v1/credibility/assessments/" + created.json()["assessment_id"]).json()
    assert task["status"] == "failed"
    assert task["error"] | {"debug_id": None} == {
        "code": "PAGE_LIMIT_EXCEEDED", "message": "The paper has 116 pages; the limit is 60.",
        "retryable": False, "debug_id": None,
    }


def test_live_parse_keeps_the_retrieval_error_code(monkeypatch):
    class Parser:
        def parse_arxiv(self, _source):
            raise ArxivDownloadError("The paper has 116 pages; the limit is 60.", code="PAGE_LIMIT_EXCEEDED")

    monkeypatch.setattr(get_settings(), "parser_provider", "generic")
    monkeypatch.setattr("app.pipeline.orchestrator.build_parser", lambda: Parser())
    doc, reason = _parse_live({"type": "arxiv", "arxiv_id": "2310.02255v3"}, None)
    assert doc is None and reason.code == "PAGE_LIMIT_EXCEEDED" and reason.retryable is False


def test_retriever_page_limit_is_not_retryable(tmp_path):
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=output.getvalue()))
    with httpx.Client(transport=transport) as http:
        retriever = ArxivRetriever(tmp_path, max_bytes=1024 * 1024, max_pages=2, client=http)
        with pytest.raises(ArxivDownloadError) as caught:
            retriever.retrieve("2501.01234v1")
    assert caught.value.code == "PAGE_LIMIT_EXCEEDED" and caught.value.retryable is False
    assert "3 pages; the limit is 2" in str(caught.value)


def test_history_lists_finished_checks_newest_first_with_search(live_model, monkeypatch):
    monkeypatch.setattr("app.credibility.shell.build_context", lambda *args, **kwargs: _ctx())
    monkeypatch.setattr("app.credibility.shell.assemble_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    first = client.post("/v1/credibility/assessments",
                        json=_request({"type": "arxiv", "arxiv_id": "1706.03762", "upload_id": None})).json()
    monkeypatch.setattr(routes_credibility, "_run", lambda *args, **kwargs: None)
    client.post("/v1/credibility/assessments",
                json=_request({"type": "arxiv", "arxiv_id": "2106.09685", "upload_id": None}))
    listing = client.get("/v1/credibility/assessments").json()
    assert listing["total"] == 1  # the queued task is not listed
    row = listing["items"][0]
    assert row["assessment_id"] == first["assessment_id"] and row["status"] == "failed"
    assert row["error_code"] == "INTERNAL_ERROR" and row["arxiv_id"] == "1706.03762"
    assert client.get("/v1/credibility/assessments?search=nomatch").json()["total"] == 0
    assert client.get("/v1/credibility/assessments?search=1706").json()["total"] == 1
