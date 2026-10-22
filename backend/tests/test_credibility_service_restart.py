"""An API restart turns its orphaned credibility tasks into a terminal failure."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes_credibility
from app.credibility import runtime
from app.main import app
from app.storage.credibility_tasks import CredibilityTaskStore


def _queued(task_id):
    return {
        "assessment_id": task_id, "schema_version": "credibility-0.5.0-draft",
        "analysis_mode": "credibility", "data_origin": "live", "model_id": "model_standard_v1",
        "source": {"type": "arxiv", "arxiv_id": "2106.09685v2", "upload_id": None},
        "status": "queued", "stage": "queued", "progress_percent": 0,
        "created_at": "2026-10-20T00:00:00Z", "started_at": None, "completed_at": None,
        "paper": None, "report": None, "error": None, "warnings": [],
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'credibility.db'}", connect_args={"check_same_thread": False})
    task_store = CredibilityTaskStore(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(runtime, "get_task_store", lambda: task_store)
    monkeypatch.setattr(routes_credibility, "_TASKS", {})
    monkeypatch.setattr("app.main.fail_interrupted_assessments", lambda: None)
    monkeypatch.setattr("app.storage.db.init_db", lambda: None)
    yield task_store
    engine.dispose()


def test_startup_fails_task_interrupted_mid_check_and_get_reports_it(store):
    store.create_task(_queued("cred_interrupted"), "fp", {"rubric_version": "0.2-provisional-complete"})
    lease = store.claim_task("cred_interrupted", owner=routes_credibility.API_OWNER)
    store.update_task("cred_interrupted", lease, stage="checking", progress_percent=60)
    store.save_stage("cred_interrupted", lease, "claim_check", "stage-fp", {"claims": [1]})
    routes_credibility._TASKS["cred_interrupted"] = store.load_task("cred_interrupted")

    with TestClient(app) as client:
        response = client.get("/v1/credibility/assessments/cred_interrupted")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed" and body["stage"] == "failed"
    assert body["error"] == {
        "code": "SERVICE_RESTARTED",
        "message": "The local service restarted before this check finished. Please submit it again.",
        "retryable": True, "debug_id": "cred_interrupted",
    }
    assert "cred_interrupted" not in routes_credibility._TASKS
    assert store.load_stage("cred_interrupted", "claim_check", "stage-fp") == {"claims": [1]}
    assert store.claim_task("cred_interrupted", resume=True, fingerprint="fp").attempt == 2
