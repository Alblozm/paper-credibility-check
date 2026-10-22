"""HTTP integration contract, private upload bytes and input rejection."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api import routes_assessments
from app.config import get_settings
from app.llm.fixture import FixtureLLMClient
from app.main import app
from app.schemas.assessment import Assessment
from app.storage.uploads import resolve_upload

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    # 自动测试必须与开发者本机 .env 隔离，不能因为启用了 benchmark/真实模型而
    # 改变既有 HTTP 契约测试，更不能意外消耗模型 API 额度。
    monkeypatch.setattr(settings, "parser_provider", "stub")
    monkeypatch.setattr(settings, "llm_provider", "fixture")
    routes_assessments._STORE.clear()


def body(source):
    return {"source": source, "model_id": "model_fixture_v1", "venue_id": "iclr_2027_main"}


def test_queue_and_completed_are_full_snapshots(monkeypatch):
    original = routes_assessments.run_assessment
    monkeypatch.setattr(routes_assessments, "run_assessment", lambda *a, **kw: None)
    payload = body({"type": "arxiv", "arxiv_id": "2307.08122v3"})
    receipt = client.post("/v1/assessments", json=payload).json()
    task = client.get(f"/v1/assessments/{receipt['assessment_id']}").json()
    assert Assessment.model_validate(task).status.value == "queued"
    assert task["report"] is None
    assert task["model"]["model_id"] == "model_fixture_v1"
    monkeypatch.setattr(routes_assessments, "run_assessment", original)
    receipt = client.post("/v1/assessments", json=payload).json()
    task = client.get(f"/v1/assessments/{receipt['assessment_id']}").json()
    assert Assessment.model_validate(task).status.value == "completed"
    assert any(w["code"] == "DEMO_MODE" for w in task["warnings"])


def test_pdf_bytes_saved_and_usable_for_task():
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    response = client.post("/v1/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    assert response.status_code == 201
    upload = response.json()
    path = resolve_upload(upload["upload_id"])
    assert path.read_bytes() == data
    assert path.stat().st_mode & 0o777 == 0o600
    assert upload["sha256"] == hashlib.sha256(data).hexdigest()
    receipt = client.post("/v1/assessments", json=body({
        "type": "pdf", "upload_id": upload["upload_id"], "original_filename": "paper.pdf",
    })).json()
    task = client.get(f"/v1/assessments/{receipt['assessment_id']}").json()
    assert Assessment.model_validate(task).status.value == "completed"
    assert str(path) not in str(task)
    assert task["source"]["upload_id"] == upload["upload_id"]


@pytest.mark.parametrize("data", [b"", b"not pdf", b"%PDF-1.7\nbroken"])
def test_bad_pdfs_rejected(data):
    response = client.post("/v1/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    assert response.status_code == 400
    assert response.json()["detail"]["message"]


@pytest.mark.parametrize("source", [
    {"type": "arxiv", "arxiv_id": "not-an-id"},
    {"type": "pdf", "upload_id": "upl_does_not_exist"},
])
def test_invalid_sources_never_create_successful_tasks(source):
    assert client.post("/v1/assessments", json=body(source)).status_code in (400, 404)
    assert not routes_assessments._STORE


def test_arxiv_pdf_url_is_normalized_before_queueing(monkeypatch):
    monkeypatch.setattr(routes_assessments, "run_assessment", lambda *args, **kwargs: None)
    receipt = client.post(
        "/v1/assessments",
        json=body({"type": "arxiv", "arxiv_id": "https://arxiv.org/pdf/2501.01234v2.pdf"}),
    )
    assert receipt.status_code == 202
    task = client.get(f"/v1/assessments/{receipt.json()['assessment_id']}").json()
    assert task["source"]["arxiv_id"] == "2501.01234v2"
    assert task["source"]["original_url"] == "https://arxiv.org/abs/2501.01234v2"


def test_unavailable_model_rejected():
    payload = body({"type": "arxiv", "arxiv_id": "2307.08122"})
    payload["model_id"] = "model_standard_v1"
    assert client.post("/v1/assessments", json=payload).status_code == 400


def test_failure_snapshot_remains_complete(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private server detail")
    monkeypatch.setattr("app.pipeline.orchestrator.build_parser", fail)
    receipt = client.post("/v1/assessments", json=body({"type": "arxiv", "arxiv_id": "2307.08122"})).json()
    task = client.get(f"/v1/assessments/{receipt['assessment_id']}").json()
    assert Assessment.model_validate(task).status.value == "failed"
    assert task["error"]["retryable"]
    assert "private server detail" not in str(task)


def test_non_ml_gate_stops_before_scoring(monkeypatch):
    classifier_client = FixtureLLMClient(eligibility_payload={
        "document_type": "research_paper",
        "language": "english",
        "ml_relevance": "no",
        "confidence": "high",
        "reason": "This is a chemistry paper without a machine-learning method.",
        "evidence_chunk_ids": [],
    })
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _model_id: classifier_client)
    receipt = client.post(
        "/v1/assessments",
        json=body({"type": "arxiv", "arxiv_id": "2501.01234"}),
    )
    assert receipt.status_code == 202
    task = client.get(f"/v1/assessments/{receipt.json()['assessment_id']}").json()
    assert task["status"] == "failed"
    assert task["report"] is None
    assert task["error"]["code"] == "UNABLE_TO_ASSESS"
    assert "No assessment was produced" in task["error"]["message"]
    assert len(classifier_client.calls) == 1


def test_benchmark_mode_loads_real_paper_001_and_rejects_other_sources(monkeypatch):
    settings = get_settings()
    data_repo = Path(__file__).resolve().parents[2] / "data"
    parsed_dir = data_repo / "data" / "parsed" / "paper_001" / "v3"
    source_record = data_repo / "data" / "source_snapshots" / "paper_001" / "source_record.json"
    if not parsed_dir.is_dir() or not source_record.is_file():
        pytest.skip("缺少 data/ 目录；真实 Benchmark HTTP 测试需要完整仓库")
    monkeypatch.setattr(settings, "parser_provider", "benchmark")
    monkeypatch.setattr(settings, "benchmark_parsed_dir", str(parsed_dir))
    monkeypatch.setattr(settings, "benchmark_source_record", str(source_record))
    monkeypatch.setattr(settings, "benchmark_arxiv_id", "2307.08122v3")

    receipt = client.post(
        "/v1/assessments",
        json=body({"type": "arxiv", "arxiv_id": "2307.08122v3"}),
    ).json()
    task = client.get(f"/v1/assessments/{receipt['assessment_id']}").json()
    assert Assessment.model_validate(task).status.value == "completed"
    assert task["paper"]["paper_id"] == "paper_001"
    assert task["paper"]["title"] == "Tangent Transformers for Composition, Privacy and Removal"
    assert task["paper"]["page_count"] == 18

    receipt = client.post(
        "/v1/assessments",
        json=body({"type": "arxiv", "arxiv_id": "2310.01798"}),
    ).json()
    task = client.get(f"/v1/assessments/{receipt['assessment_id']}").json()
    assert Assessment.model_validate(task).status.value == "failed"
    assert task["error"]["code"] == "BENCHMARK_SOURCE_MISMATCH"
    assert task["error"]["retryable"] is False
