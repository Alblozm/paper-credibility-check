"""Real HTTP serialization preserves all five credibility report contracts."""
from __future__ import annotations

import copy
from collections import Counter

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.api import routes_credibility
from app.config import get_settings
from app.credibility import runtime
from app.credibility.context import CheckContext
from app.credibility.models import CredibilityAssessmentResponse, CredibilityCodeDiscovery, ReferenceCheckResult
from app.credibility.shell import CheckSet
from app.main import app
from app.parsing.grobid import StubParser

LEGACY = "credibility-0.1.0-draft"
V2 = "credibility-0.2.0-draft"
V3 = "credibility-0.3.0-draft"
V4 = "credibility-0.4.0-draft"
CURRENT = "credibility-0.5.0-draft"
VERSIONS = (LEGACY, V2, V3, V4, CURRENT)
V2_REPORT_FIELDS = {"coverage", "execution", "external_review"}
SUMMARY_FIELD = "reference_verification_summary"
DISCOVERY_FIELD = "code_discovery"
NEW_REPORT_FIELDS = V2_REPORT_FIELDS | {SUMMARY_FIELD, DISCOVERY_FIELD}
SOURCE = {"type": "arxiv", "arxiv_id": "2503.12345v1", "upload_id": None}


@pytest.fixture
def contract_client(tmp_path, monkeypatch):
    calls = Counter()
    monkeypatch.setattr(get_settings(), "credibility_code_model_enabled", False)
    ctx = CheckContext(
        source=SOURCE, repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None,
        doc=StubParser._fake(paper_id="schema_contract_fixture", title="Offline API contract fixture"),
        unresolved_reason=None,
    )
    monkeypatch.setattr("app.credibility.shell.build_context", lambda *args, **kwargs: copy.deepcopy(ctx))
    monkeypatch.setattr(routes_credibility, "_TASKS", {})

    def forbidden(name):
        def fail(*args, **kwargs):
            calls[name] += 1
            raise AssertionError("Contract serialization must not call a model or fetch external sources")
        return fail

    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", forbidden("model"))
    monkeypatch.setattr("app.credibility.references._LOOKUP.lookup", forbidden("reference"))
    monkeypatch.setattr("app.credibility.code_checks.LOOKUP.get", forbidden("code"))
    yield TestClient(app)
    # Producer exceptions are isolated by the pipeline, so explicitly check no
    # accidental external call was swallowed into a fallback report.
    assert calls == {}


def _submit_and_get(client, schema, *, rubric="0.2-provisional-complete"):
    created = client.post("/v1/credibility/assessments", json={
        "schema_version": schema, "analysis_mode": "credibility", "source": SOURCE,
        "model_id": "not_called", "rubric_version": rubric,
    })
    assert created.status_code == 202
    queued = created.json()
    assert queued["schema_version"] == schema
    assert queued["status"] == queued["stage"] == "queued" and queued["progress_percent"] == 0
    assert queued["report"] is None and queued["error"] is None
    fetched = client.get(f"/v1/credibility/assessments/{queued['assessment_id']}")
    assert fetched.status_code == 200
    finished = fetched.json()
    assert finished["assessment_id"] == queued["assessment_id"]
    assert finished["schema_version"] == schema
    assert finished["status"] == "completed" and finished["stage"] == "done"
    assert finished["progress_percent"] == 100 and finished["error"] is None
    return finished


@pytest.mark.parametrize("schema", VERSIONS)
@pytest.mark.parametrize("rubric", ["0.1-draft", "0.2-provisional-complete"])
def test_post_and_get_honor_requested_schema_independently_of_scoring_policy(contract_client, schema, rubric):
    finished = _submit_and_get(contract_client, schema, rubric=rubric)
    report = finished["report"]
    assert report["rubric_version"] == rubric
    assert {d["dimension_id"] for d in report["dimensions"]} == {
        "claim_evidence", "reference_verification", "code_reproduction",
    }
    if schema == LEGACY:
        assert NEW_REPORT_FIELDS.isdisjoint(report)
    else:
        assert V2_REPORT_FIELDS <= report.keys()
        assert len(report["coverage"]) == 3
        assert report["execution"]["workers"] == 1
        assert set(report["execution"]["checks"]) == {"claim_check", "reference_check", "code_check"}
        assert report["external_review"] is None
    if schema in {V3, V4, CURRENT}:
        summary = report[SUMMARY_FIELD]
        assert summary == {
            "verification_rate": None, "denominator": "extracted_references",
            "extracted_count": 0, "matched_count": 0, "unverified_count": 0,
            "not_checked_count": 0, "metadata_conflict_count": 0,
            "extraction_complete": False, "unresolved_items": [],
        }
    else:
        assert SUMMARY_FIELD not in report
    if schema == CURRENT:
        discovery = CredibilityCodeDiscovery.model_validate(report[DISCOVERY_FIELD])
        assert discovery.status == "disabled" and discovery.reason
        assert discovery.queries == discovery.candidates == discovery.limitations == []
        assert discovery.scope_complete is False and discovery.audit_ref is None
        assert all(value == 0 for value in discovery.budget.model_dump().values())
    else:
        assert DISCOVERY_FIELD not in report
    if rubric == "0.1-draft":
        assert report["overall"]["score"] is None
    else:
        assert report["overall"]["score"] is not None
    parsed = TypeAdapter(CredibilityAssessmentResponse).validate_python(finished)
    assert parsed.model_dump(mode="json") == finished


@pytest.mark.parametrize("schema", VERSIONS)
def test_schema_projection_survives_cache_clear_without_losing_internal_execution_record(
    contract_client, monkeypatch, schema,
):
    finished = _submit_and_get(contract_client, schema)
    assessment_id = finished["assessment_id"]
    stored = runtime.get_task_store().load_task(assessment_id)
    assert stored["schema_version"] == schema
    assert NEW_REPORT_FIELDS <= stored["report"].keys()
    assert len(stored["report"]["coverage"]) == 3
    monkeypatch.setattr(routes_credibility, "_TASKS", {})
    fetched = contract_client.get(f"/v1/credibility/assessments/{assessment_id}")
    assert fetched.status_code == 200 and fetched.json() == finished
    assert runtime.get_task_store().load_task(assessment_id) == stored


def test_schema_versions_preserve_the_same_scoring_and_evidence_contract(contract_client):
    old = _submit_and_get(contract_client, LEGACY)["report"]
    previous = _submit_and_get(contract_client, V2)["report"]
    third = _submit_and_get(contract_client, V3)["report"]
    fourth = _submit_and_get(contract_client, V4)["report"]
    new = _submit_and_get(contract_client, CURRENT)["report"]
    assert set(new) - set(old) == NEW_REPORT_FIELDS
    assert set(previous) - set(old) == V2_REPORT_FIELDS
    assert set(new) - set(previous) == {SUMMARY_FIELD, DISCOVERY_FIELD}
    assert set(new) - set(third) == {DISCOVERY_FIELD}
    assert set(third) == set(fourth)
    assert set(old) - set(new) == set()
    for key in ("rubric_id", "rubric_version", "rubric_status", "rubric_sha256", "scope_complete", "is_provisional",
                "dimensions", "checks", "evidence", "sources", "findings", "overall", "alerts", "limitations"):
        assert old[key] == previous[key] == third[key] == fourth[key] == new[key], key


def test_unknown_schema_is_rejected_before_task_creation(contract_client):
    response = contract_client.post("/v1/credibility/assessments", json={
        "schema_version": "credibility-0.6.0-draft", "analysis_mode": "credibility",
        "source": SOURCE, "model_id": "not_called",
    })
    assert response.status_code == 422
    assert routes_credibility._TASKS == {}
    assert runtime.get_task_store().list_recoverable_tasks() == []


def test_omitted_schema_uses_v5_for_creation_and_saved_result(contract_client):
    created = contract_client.post("/v1/credibility/assessments", json={
        "analysis_mode": "credibility", "source": SOURCE, "model_id": "not_called",
    })
    assert created.status_code == 202
    assert created.json()["schema_version"] == CURRENT
    fetched = contract_client.get(f"/v1/credibility/assessments/{created.json()['assessment_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["schema_version"] == CURRENT
    assert SUMMARY_FIELD in fetched.json()["report"]
    assert DISCOVERY_FIELD in fetched.json()["report"]


@pytest.mark.parametrize("schema", VERSIONS[:-1])
def test_old_saved_report_can_be_read_without_adding_new_fields_to_old_projection(contract_client, monkeypatch, schema):
    finished = _submit_and_get(contract_client, schema)
    stored = runtime.get_task_store().load_task(finished["assessment_id"])
    if schema in {LEGACY, V2}:
        stored["report"].pop(SUMMARY_FIELD)
    stored["report"].pop(DISCOVERY_FIELD)
    monkeypatch.setattr(runtime.get_task_store(), "load_task", lambda _assessment_id: copy.deepcopy(stored))
    fetched = contract_client.get(f"/v1/credibility/assessments/{finished['assessment_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == finished


def test_nonempty_summary_and_fixed_trial_scores_survive_all_five_http_projections(contract_client, monkeypatch):
    """Source changes affect checks; serialization alone cannot affect scores."""
    items = []
    reasons = [
        ("matched", "Official identity confirmed."),
        ("unverified", "[personal_communication] No public bibliographic record is expected."),
        ("metadata_conflict", "[year_mismatch] Cited year differs from the official date."),
        ("not_checked", "[budget_exhausted] Bounded lookup deadline exhausted."),
    ]
    for index, (outcome, reason) in enumerate(reasons, 1):
        items.append({
            "reference_id": f"ref_{index:03d}", "raw_text": f"Reference {index}", "paper_evidence_ids": [],
            "parsed": {"title": None, "authors": [], "year": None, "doi": None, "arxiv_id": None},
            "outcome": outcome, "reason": reason, "source_ids": [], "candidates": [],
            "selected_candidate_id": None, "score_rule_id": None, "item_score": None,
        })
    result = ReferenceCheckResult.model_validate({
        "dimension": {
            "dimension_id": "reference_verification", "applicability": "applicable",
            "applicability_reason": "Four extracted references.", "check_status": "partial",
            "score_status": "pending", "score": None, "weight": 1, "score_rule_ids": [],
            "summary": "Fixture", "unassessed_reason": None, "evidence_ids": [], "finding_ids": [],
        },
        "checks": {
            "extraction_status": "complete", "extraction_reason": "Four references extracted.",
            "original_total": 4, "extracted_count": 4, "checked_count": 3, "matched_count": 1,
            "conflict_count": 1, "unverified_count": 1, "not_checked_count": 1,
            "query_completion_ratio": 0.75, "extraction_coverage_ratio": 1, "gaps": [], "items": items,
        },
    })
    original = routes_credibility.build_completed_assessment

    def assemble_fixed_checks(**kwargs):
        kwargs["checks"] = CheckSet(references=lambda _ctx: result.model_copy(deep=True))
        return original(**kwargs)

    monkeypatch.setattr(routes_credibility, "build_completed_assessment", assemble_fixed_checks)
    assessments = [_submit_and_get(contract_client, version) for version in VERSIONS]
    reports = [assessment["report"] for assessment in assessments]
    for report in reports:
        assert report["dimensions"][1]["score"] == 6
        assert [item["item_score"] for item in report["checks"]["references"]["items"]] == [10, 4, 7, 3]
        assert report["checks"] == reports[0]["checks"]
        assert report["dimensions"] == reports[0]["dimensions"]
        assert report["overall"] == reports[0]["overall"]
    summary = reports[2][SUMMARY_FIELD]
    assert summary["verification_rate"] == 0.25 and summary["extraction_complete"] is True
    assert summary["metadata_conflict_count"] == summary["unverified_count"] == summary["not_checked_count"] == 1
    assert [item["reason_code"] for item in summary["unresolved_items"]] == [
        "personal_communication", "year_mismatch", "budget_exhausted",
    ]
    assert SUMMARY_FIELD not in reports[0] and SUMMARY_FIELD not in reports[1]
    assert reports[2][SUMMARY_FIELD] == reports[3][SUMMARY_FIELD]
    assert set(reports[2]) == set(reports[3])
    assert reports[3][SUMMARY_FIELD] == reports[4][SUMMARY_FIELD]
    assert set(reports[4]) - set(reports[3]) == {DISCOVERY_FIELD}


def test_nonempty_discovery_survives_http_storage_and_only_the_v5_projection(contract_client, monkeypatch):
    from app.credibility.code_checks import check_code

    original = routes_credibility.build_completed_assessment

    def fixed_code(ctx):
        result = check_code(ctx)
        result.dimension.check_status = "partial"
        result.checks.code_availability = "unable_to_verify"
        result.sources = [
            {
                "source_id": sid, "kind": "metadata", "url": f"https://example.org/{sid}",
                "fetch_status": status, "http_status": http, "retrieved_at": ctx.generated_at,
                "observed_at": ctx.generated_at, "is_cached": False, "snapshot_ref": None,
                "association_reason": "Offline search response fixture, not repository proof.",
            }
            for sid, status, http in (("empty", "ok", 200), ("limited", "rate_limited", 429), ("hit", "ok", 200))
        ]
        result.discovery = CredibilityCodeDiscovery.model_validate({
            "status": "partial", "reason": "One channel was rate limited; other results are candidates only.",
            "queries": [
                {"query_id": "q1", "provider": "github", "query": "exact title", "status": "completed",
                 "result_count": 0, "source_ids": ["empty"], "reason": "Successful bounded empty result."},
                {"query_id": "q2", "provider": "web", "query": "paper id", "status": "failed",
                 "result_count": 0, "source_ids": ["limited"], "reason": "Rate limited, not proof of absence."},
                {"query_id": "q3", "provider": "github", "query": "title authors", "status": "completed",
                 "result_count": 1, "source_ids": ["hit"], "reason": "One unverified resource discovered."},
            ],
            "candidates": [{
                "candidate_id": "candidate_1", "url": "https://github.com/example/paper-resources",
                "title": "Paper resources", "query_ids": ["q3"], "source_ids": ["hit"],
                "reason": "A search hit alone does not establish an official implementation.",
            }],
            "budget": {"max_queries": 3, "queries_used": 3, "max_candidates": 2,
                       "max_source_reads": 40, "source_reads_used": 3,
                       "max_model_rounds": 4, "model_rounds_used": 1,
                       "max_tool_calls": 8, "tool_calls_used": 3},
            "limitations": ["Rate limited channel; candidate content was not inspected."],
        })
        return result

    def assemble_fixed_checks(**kwargs):
        kwargs["checks"] = CheckSet(code=fixed_code)
        return original(**kwargs)

    monkeypatch.setattr(routes_credibility, "build_completed_assessment", assemble_fixed_checks)
    assessments = [_submit_and_get(contract_client, version) for version in VERSIONS]
    reports = [assessment["report"] for assessment in assessments]
    for report in reports:
        assert report["checks"] == reports[0]["checks"]
        assert report["dimensions"] == reports[0]["dimensions"]
        assert report["overall"] == reports[0]["overall"]
        assert report["checks"]["code"]["repositories"] == []
        assert report["checks"]["code"]["code_availability"] == "unable_to_verify"
    assert all(DISCOVERY_FIELD not in report for report in reports[:4])
    discovery = reports[4][DISCOVERY_FIELD]
    assert discovery["status"] == "partial" and discovery["scope_complete"] is False
    assert [query["status"] for query in discovery["queries"]] == ["completed", "failed", "completed"]
    assert [query["result_count"] for query in discovery["queries"]] == [0, 0, 1]
    assert discovery["candidates"][0]["status"] == "candidate"
    assert discovery["candidates"][0]["resource_kind"] == "undetermined"
    assert discovery["candidates"][0]["repository_id"] is None
    for assessment in assessments:
        assessment_id = assessment["assessment_id"]
        stored = runtime.get_task_store().load_task(assessment_id)
        assert stored["report"][DISCOVERY_FIELD] == discovery
        monkeypatch.setattr(routes_credibility, "_TASKS", {})
        fetched = contract_client.get(f"/v1/credibility/assessments/{assessment_id}")
        assert fetched.status_code == 200 and fetched.json() == assessment
