"""Exercise rubric selection through the actual task route without external calls."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes_credibility
from app.credibility.context import CheckContext
from app.credibility.rubric import load_credibility_rubric
from app.main import app
from app.parsing.grobid import StubParser


@pytest.fixture
def client(monkeypatch, tmp_path):
    doc = StubParser._fake(paper_id="scoring_fixture", title="Offline scoring fixture")
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2503.12345", "upload_id": None},
        repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None, doc=doc, unresolved_reason=None,
    )
    monkeypatch.setattr("app.credibility.shell.build_context", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(routes_credibility, "_TASKS", {})

    def forbidden(*args, **kwargs):
        raise AssertionError("Scoring must not call a model or an external lookup")

    monkeypatch.setattr("app.llm.deepseek.DeepSeekClient.complete_json", forbidden)
    monkeypatch.setattr("app.credibility.references._LOOKUP.lookup", forbidden)
    monkeypatch.setattr("app.credibility.code_checks.LOOKUP.get", forbidden)
    return TestClient(app)


def request():
    return {
        "schema_version": "credibility-0.1.0-draft", "analysis_mode": "credibility",
        "source": {"type": "arxiv", "arxiv_id": "2503.12345", "upload_id": None},
        "model_id": "not_called",
    }


@pytest.mark.parametrize("explicit", [False, True])
def test_api_routes_default_and_explicit_trial_policy_to_completed_report(client, explicit):
    body = request()
    if explicit:
        body["rubric_version"] = "0.2-provisional-complete"
    created = client.post("/v1/credibility/assessments", json=body)
    assert created.status_code == 202
    assert created.json()["report"] is None
    result = client.get("/v1/credibility/assessments/" + created.json()["assessment_id"]).json()
    assert result["status"] == "completed" and result["error"] is None
    report = result["report"]
    assert report["rubric_version"] == "0.2-provisional-complete"
    assert report["rubric_status"] == "provisional" and report["is_provisional"] is True
    assert report["rubric_sha256"] == load_credibility_rubric().content_sha256
    assert all(d["score_status"] == "scored" and d["score"] is not None for d in report["dimensions"])
    assert report["overall"]["score"] == pytest.approx(sum(d["score"] for d in report["dimensions"]) / 3)
    assert report["overall"]["calculation"]["method"] == "equal_weight_trial_dimensions"
    assert report["scope_complete"] is False
    assert report["checks"]["claims"]["items"] == []
    assert report["dimensions"][0]["check_status"] == "not_run"
    assert report["dimensions"][1]["check_status"] == "failed"
    assert report["versions"]["model_calls"] == []
    assert "provisional_rubric" in {w["code"] for w in result["warnings"]}
    assert any(a["type"] == "low_dimension_score" for a in report["alerts"])
    assert any(a["type"] == "assessment_gap" for a in report["alerts"])


def test_explicit_legacy_draft_stays_unscored(client):
    body = {**request(), "rubric_version": "0.1-draft"}
    created = client.post("/v1/credibility/assessments", json=body)
    result = client.get("/v1/credibility/assessments/" + created.json()["assessment_id"]).json()
    assert result["status"] == "completed"
    assert result["report"]["rubric_status"] == "draft"
    assert all(d["score"] is None for d in result["report"]["dimensions"])
    assert result["report"]["overall"]["score"] is None
    assert "draft_rubric" in {w["code"] for w in result["warnings"]}


def test_unknown_rubric_is_rejected_before_a_task_or_model_call(client):
    response = client.post("/v1/credibility/assessments", json={**request(), "rubric_version": "unknown"})
    assert response.status_code == 422
    assert routes_credibility._TASKS == {}


def test_packaged_trial_policy_is_the_data_team_snapshot():
    delivered = Path(__file__).resolve().parents[2] / "data/rubrics/credibility_v0.1/rubric.provisional-ai-batch.json"
    if not delivered.is_file():
        pytest.skip("The sibling data repository is not installed")
    assert load_credibility_rubric().content_sha256 == hashlib.sha256(delivered.read_bytes()).hexdigest()
