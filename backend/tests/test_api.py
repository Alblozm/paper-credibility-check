"""接口冒烟测试。前端可以直接照 /openapi.json 开发。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["schema_version"] == "0.4"


def test_venues_returns_single_iclr_option():
    r = client.get("/v1/venues")
    assert r.status_code == 200
    venues = r.json()
    assert len(venues) == 1
    assert venues[0]["code"] == "ICLR"
    assert venues[0]["is_available"] is True


def test_models_never_expose_secrets():
    r = client.get("/v1/models")
    body = r.text.lower()
    assert "api_key" not in body and "authorization" not in body


def test_models_offer_configured_deepseek_and_gpt(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "deepseek_api_key", "configured-deepseek-key")
    monkeypatch.setattr(settings, "openai_api_key", "configured-openai-key")

    models = client.get("/v1/models").json()
    available = {item["model_id"]: item for item in models if item["is_available"]}

    assert {"model_standard_v1", "model_gpt_v1"} <= available.keys()
    assert available["model_gpt_v1"]["provider"] == "openai"
    assert available["model_gpt_v1"]["is_default"] is True


def test_validate_report_endpoint(example_report):
    r = client.post("/internal/validate-report", json={"report": example_report})
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    assert payload["report"]["overall_assessment"]["overall_score"] == 7.2


def test_create_assessment_runs_end_to_end():
    r = client.post(
        "/v1/assessments",
        json={
            "schema_version": "0.4",
            "source": {
                "type": "arxiv",
                "arxiv_id": "2501.01234",
                "original_url": "https://arxiv.org/abs/2501.01234",
                "upload_id": None,
                "original_filename": None,
            },
            "venue_id": "iclr_2027_main",
            "model_id": "model_fixture_v1",
        },
    )
    assert r.status_code == 202
    assessment_id = r.json()["assessment_id"]

    got = client.get(f"/v1/assessments/{assessment_id}")
    assert got.status_code == 200
    body = got.json()
    # TestClient 会同步跑完 BackgroundTasks
    assert body["status"] == "completed"
    assert body["stage"] == "report_ready"
    assert body["progress_percent"] == 100
    assert body["report"]["overall_assessment"]["overall_score"] is not None


def test_unknown_assessment_returns_404():
    assert client.get("/v1/assessments/asmt_nope").status_code == 404


def test_saved_review_survives_in_memory_store_clear(monkeypatch):
    from app.api import routes_assessments
    from app.storage.assessment_history import save_assessment
    from app.storage.db import SessionLocal
    from app.storage.models import AssessmentSnapshotRow

    monkeypatch.setattr(routes_assessments, "save_assessment", save_assessment)

    response = client.post(
        "/v1/assessments",
        json={
            "schema_version": "0.4",
            "source": {
                "type": "arxiv", "arxiv_id": "2501.01234",
                "original_url": "https://arxiv.org/abs/2501.01234",
                "upload_id": None, "original_filename": None,
            },
            "venue_id": "iclr_2027_main",
            "model_id": "model_fixture_v1",
        },
    )
    assert response.status_code == 202
    assessment_id = response.json()["assessment_id"]
    try:
        original = client.get(f"/v1/assessments/{assessment_id}").json()
        assert original["status"] == "completed"
        routes_assessments._STORE.pop(assessment_id)
        recovered = client.get(f"/v1/assessments/{assessment_id}")
        assert recovered.status_code == 200
        assert recovered.json() == original

        history = client.get("/v1/assessments", params={"search": original["paper"]["title"]})
        assert history.status_code == 200
        assert any(item["assessment_id"] == assessment_id for item in history.json()["items"])
    finally:
        routes_assessments._STORE.pop(assessment_id, None)
        with SessionLocal.begin() as session:
            row = session.get(AssessmentSnapshotRow, assessment_id)
            if row:
                session.delete(row)
