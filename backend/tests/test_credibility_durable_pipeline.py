"""Offline end-to-end acceptance of durable credibility execution boundaries.

Check producers are deterministic substitutes; SQL transactions, artifact files,
report assembly, score rules and HTTP route validation are real.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from threading import Lock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.api import routes_credibility
from app.config import get_settings
from app.credibility import runtime, shell
from app.credibility.context import CheckContext
from app.credibility.models import (
    ClaimCheckResult,
    CodeCheckResult,
    CredibilityAssessment,
    CredibilityDimension,
    CredibilityDiscussion,
    CredibilityExternalSource,
    CredibilityModelCall,
    ReferenceCheckResult,
)
from app.credibility.rubric import load_credibility_rubric
from app.credibility.snapshots import SnapshotError, capture_snapshot, read_snapshot, verify_report_snapshots
from app.main import app
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.paper import Paper
from app.storage.credibility_tasks import CredibilityStageRow, CredibilityTaskStore

SOURCE = {"type": "arxiv", "arxiv_id": "2503.12345v1", "upload_id": None}
TIME = "2026-10-19T00:00:00Z"


def _dimension(dimension_id):
    return CredibilityDimension(
        dimension_id=dimension_id, applicability="applicable",
        applicability_reason="Test paper contains this material.",
        check_status="completed", score_status="unable_to_assess", score=None, weight=1,
        score_rule_ids=[], summary="Validated deterministic test check.", unassessed_reason=None,
        evidence_ids=[], finding_ids=[],
    )


def _paper_evidence(evidence_id, chunk, page, excerpt):
    return {
        "evidence_id": evidence_id, "origin": "paper", "mode": "quote", "excerpt": excerpt,
        "paper_locator": {"page_start": page, "page_end": page, "chunk_id": chunk,
                          "section_title": "Abstract" if page == 1 else "Results", "element_label": None},
        "source_id": None, "location_confidence": "high",
    }


def _source(source_id, url, kind):
    ref = capture_snapshot(
        kind=kind, raw=b"immutable external response", normalized_json={"project": "test"},
        requested_url=url, final_url=url, fetch_status="ok", http_status=200, retrieved_at=TIME,
        observed_at=TIME, is_cached=False, metadata={"source_id": source_id, "source_url": url},
    )
    return CredibilityExternalSource(
        source_id=source_id, kind=kind, url=url, fetch_status="ok", http_status=200,
        retrieved_at=TIME, observed_at=TIME, is_cached=False, snapshot_ref=ref,
        association_reason="Direct source used by this test check.",
    )


def _results():
    claim = ClaimCheckResult.model_validate({
        "applies": True, "anchors_matched": True, "dimension": _dimension("claim_evidence"),
        "checks": {"selection_scope": "All abstract and introduction claims.", "scope_complete": True,
                   "scope_limitations": [], "items": [{
                       "claim_id": "c1", "statement": "Our method improves accuracy.", "importance": "core",
                       "conditions": "Same held-out evaluation.", "claim_evidence_ids": ["claim_ev"],
                       "comparison_evidence_ids": ["result_ev"], "search_scope": "Results section.",
                       "outcome": "supported", "reason": "The reported accuracy increases from 80 to 84.",
                       "score_rule_id": None, "item_score": None,
                   }]},
        "evidence": [
            _paper_evidence("claim_ev", "abstract", 1, "Our method improves accuracy."),
            _paper_evidence("result_ev", "results", 2, "Accuracy increases from 80 to 84."),
        ], "findings": [], "generation_origin": "prepared", "prompt_version": "offline-acceptance-v1",
    })
    metadata_source = _source("metadata_1", "https://api.crossref.org/works/10.test/paper", "metadata")
    refs = ReferenceCheckResult.model_validate({
        "dimension": _dimension("reference_verification"),
        "checks": {"extraction_status": "complete", "extraction_reason": "One reference was extracted.",
                   "original_total": 1, "extracted_count": 1, "checked_count": 1,
                   "matched_count": 1, "conflict_count": 0, "unverified_count": 0, "not_checked_count": 0,
                   "query_completion_ratio": 1, "extraction_coverage_ratio": 1, "gaps": [], "items": [{
                       "reference_id": "r1", "raw_text": "Doe. Test paper. 2025.", "paper_evidence_ids": [],
                       "parsed": {"title": "Test paper", "authors": ["Doe"], "year": 2025,
                                  "doi": "10.test/paper", "arxiv_id": None},
                       "outcome": "matched", "reason": "The DOI and title match.",
                       "source_ids": [metadata_source.source_id], "candidates": [], "selected_candidate_id": None,
                       "score_rule_id": None, "item_score": None,
                   }]}, "sources": [metadata_source],
    })
    repo_source = _source("repository_1", "https://github.com/example/test", "repository")
    code = CodeCheckResult.model_validate({
        "dimension": _dimension("code_reproduction"),
        "checks": {"scope_complete": False, "scope_limitations": ["No code execution was attempted."],
                   "searches": [], "code_availability": "available", "reproduction_signal": "unable_to_verify",
                   "repositories": [{"repository_id": "repo_1", "source_id": repo_source.source_id,
                                     "association": "confirmed",
                                     "association_reason": "The paper links the repository.",
                                     "association_evidence_ids": ["code_association_ev"], "availability": "accessible",
                                     "implementation_evidence_ids": ["implementation_ev"],
                                     "inspected_revision": "0123456789abcdef"}], "discussions": [],
                   "base_score_rule_id": None, "score_rule_id": None},
        "sources": [repo_source], "evidence": [
            _paper_evidence("code_association_ev", "abstract", 1, "Our method improves accuracy."), {
            "evidence_id": "implementation_ev", "origin": "external", "mode": "quote",
            "excerpt": "immutable external response", "paper_locator": None,
            "source_id": repo_source.source_id, "location_confidence": "high",
        }],
    })
    return {"claim_check": claim, "reference_check": refs, "code_check": code}


def _queued(assessment_id):
    return {
        "schema_version": "credibility-0.2.0-draft", "analysis_mode": "credibility",
        "assessment_id": assessment_id, "data_origin": "live", "source": SOURCE,
        "model_id": "not_called", "status": "queued", "stage": "queued", "progress_percent": 0,
        "created_at": TIME, "started_at": None, "completed_at": None, "paper": None, "report": None,
        "warnings": [], "error": None,
    }


@pytest.fixture
def durable_pipeline(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "credibility_workers", 1)
    monkeypatch.setattr(settings, "credibility_code_model_enabled", False)
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    url = f"sqlite:///{tmp_path / 'tasks.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = CredibilityTaskStore(sessions)
    ctx = CheckContext(
        source=SOURCE, repo_root=tmp_path, generated_at=TIME, ref=None, unresolved_reason=None,
        doc=ParsedDocument(
            paper=Paper(paper_id="durable_paper", title="Durable test paper", authors=[], language="en",
                        page_count=2, abstract="Our method improves accuracy.", arxiv_id=SOURCE["arxiv_id"],
                        doi=None, sections=[], parse_quality="good", parse_warnings=[]),
            source_sha256="f" * 64, parser_name="acceptance-parser-v1", blocks=[
                TextBlock("Our method improves accuracy.", 1, chunk_id="abstract", section_role="abstract"),
                TextBlock("Accuracy increases from 80 to 84.", 2, chunk_id="results", section_role="results"),
            ],
        ),
    )
    state = SimpleNamespace(store=store, sessions=sessions, context=ctx, url=url, results=_results(),
                            calls=Counter(), failing=set(), lock=Lock(), build_calls=0, root=tmp_path)

    def producer(name):
        def run(branch):
            with state.lock:
                state.calls[name] += 1
            if name in state.failing:
                raise RuntimeError(f"Simulated {name} transient failure")
            return state.results[name].model_copy(deep=True)
        return run

    state.checks = shell.CheckSet(producer("claim_check"), producer("reference_check"), producer("code_check"))

    def build_context(*args, **kwargs):
        state.build_calls += 1
        return copy.deepcopy(state.context)

    monkeypatch.setattr(shell, "build_context", build_context)
    monkeypatch.setattr(runtime, "get_task_store", lambda: state.store)
    actual_build = shell.build_completed_assessment
    monkeypatch.setattr(routes_credibility, "build_completed_assessment",
                        lambda **kwargs: actual_build(checks=state.checks, **kwargs))

    def forbid(*args, **kwargs):
        raise AssertionError("The durable fixture must not use a real model or network")

    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", forbid)
    monkeypatch.setattr("app.credibility.references._LOOKUP.lookup", forbid)
    monkeypatch.setattr("app.credibility.code_checks.LOOKUP.get", forbid)
    yield state
    engine.dispose()


def _build(state, assessment_id, lease, *, fp="request-fingerprint"):
    return shell.build_completed_assessment(
        assessment_id=assessment_id, model_id="not_called", source=SOURCE, created_at=TIME,
        started_at=TIME, completed_at=TIME, checks=state.checks,
        rubric=load_credibility_rubric(), execution=runtime.StageExecution(state.store, assessment_id, lease, fp),
        schema_version=state.store.load_task(assessment_id)["schema_version"],
    )


def test_completed_http_report_survives_empty_process_cache_and_new_database_sessions(durable_pipeline, monkeypatch):
    state = durable_pipeline
    first_client = TestClient(app)
    response = first_client.post("/v1/credibility/assessments", json={
        "schema_version": "credibility-0.2.0-draft", "analysis_mode": "credibility", "source": SOURCE,
        "model_id": "not_called", "rubric_version": "0.2-provisional-complete",
    })
    assert response.status_code == 202
    assessment_id = response.json()["assessment_id"]
    before = first_client.get(f"/v1/credibility/assessments/{assessment_id}").json()
    assert before["status"] == "completed"
    assert before["report"]["overall"]["score"] == 9
    stored = state.store.load_task(assessment_id)
    stored_snapshot = copy.deepcopy(stored)
    assert stored["report"]["reference_verification_summary"]["verification_rate"] == 1.0
    assert stored["report"]["code_discovery"]["status"] == "not_run"
    stored["report"].pop("reference_verification_summary")
    stored["report"].pop("code_discovery")
    for item in stored["report"]["checks"]["claims"]["items"]:
        assert item.pop("reconciled_from") is None
    assert stored == before
    counts = dict(state.calls)
    new_engine = create_engine(state.url)
    state.store = CredibilityTaskStore(sessionmaker(bind=new_engine))
    monkeypatch.setattr(routes_credibility, "_TASKS", {})
    try:
        after = TestClient(app).get(f"/v1/credibility/assessments/{assessment_id}")
        assert after.status_code == 200 and after.json() == before
        assert dict(state.calls) == counts
        assert runtime.replay_task(assessment_id, store=state.store)["network_calls"] == 0
        assert state.store.load_task(assessment_id) == stored_snapshot
    finally:
        new_engine.dispose()


def test_failed_branch_keeps_other_two_results_and_resume_skips_their_producers(durable_pipeline):
    state = durable_pipeline
    assessment_id = "cred_recover_failed_branch"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    state.failing.add("claim_check")
    partial = _build(state, assessment_id, lease)
    state.store.finish_task(assessment_id, lease, partial)
    report = partial["report"]
    assert report["execution"]["checks"]["claim_check"]["status"] == "failed"
    assert report["checks"]["references"]["items"][0]["outcome"] == "matched"
    assert report["checks"]["code"]["repositories"][0]["association"] == "confirmed"
    assert [d["score"] for d in report["dimensions"]] == [3, 10, 7]
    assert report["coverage"][0]["technical_failure_count"] >= 1
    state.failing.clear()
    resumed_lease = state.store.claim_task(assessment_id, resume=True, fingerprint="request-fingerprint")
    recovered = _build(state, assessment_id, resumed_lease)
    state.store.finish_task(assessment_id, resumed_lease, recovered)
    assert state.build_calls == 1
    assert state.calls == {"claim_check": 2, "reference_check": 1, "code_check": 1}
    assert recovered["report"]["overall"]["score"] == 9
    execution = recovered["report"]["execution"]["checks"]
    assert execution["claim_check"]["reused"] is False
    assert execution["reference_check"]["reused"] is True
    assert execution["code_check"]["reused"] is True


def test_serial_and_bounded_parallel_execution_have_identical_checked_results(durable_pipeline, monkeypatch):
    state = durable_pipeline
    reports = []
    for workers in (1, 3):
        monkeypatch.setattr(get_settings(), "credibility_workers", workers)
        assessment_id = f"cred_workers_{workers}"
        state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
        lease = state.store.claim_task(assessment_id)
        reports.append(_build(state, assessment_id, lease)["report"])
    for key in ("checks", "dimensions", "evidence", "sources", "findings", "overall", "coverage", "alerts"):
        assert reports[0][key] == reports[1][key]
    assert reports[0]["execution"]["workers"] == 1
    assert reports[1]["execution"]["workers"] == 3


def test_coverage_explanations_do_not_change_frozen_trial_scores(durable_pipeline):
    state = durable_pipeline
    claim = state.results["claim_check"]
    claim.checks.scope_complete = False
    claim.checks.scope_limitations = ["Some introduction pages were not parsed."]
    refs = state.results["reference_check"]
    refs.checks.items[0].outcome = "not_checked"
    refs.checks.matched_count = refs.checks.checked_count = 0
    refs.checks.not_checked_count = 1
    refs.checks.query_completion_ratio = 0
    refs.dimension.check_status = "partial"
    state.store.create_task(_queued("cred_coverage"), "request-fingerprint", {})
    report = _build(state, "cred_coverage", state.store.claim_task("cred_coverage"))["report"]
    assert [d["score"] for d in report["dimensions"]] == [3, 3, 7]
    assert report["overall"]["score"] == pytest.approx(13 / 3)
    coverage = {row["dimension_id"]: row for row in report["coverage"]}
    assert coverage["claim_evidence"]["scope_complete"] is False
    assert coverage["claim_evidence"]["outcomes"] == {"supported": 1}
    assert coverage["reference_verification"]["completion_ratio"] == 0
    assert coverage["reference_verification"]["unresolved_count"] == 1
    assert coverage["code_reproduction"]["scope_complete"] is False
    assert any("FALLBACK" in rule for rule in coverage["claim_evidence"]["fallback_rule_ids"])


def test_changed_stage_fingerprint_recomputes_and_never_reuses_old_results(durable_pipeline):
    state = durable_pipeline
    assessment_id = "cred_changed_fp"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    _build(state, assessment_id, lease)
    _build(state, assessment_id, lease, fp="changed-parser-model-prompt")
    assert state.build_calls == 2
    assert state.calls == {"claim_check": 2, "reference_check": 2, "code_check": 2}


def test_checkpoint_blob_corruption_is_rejected_before_reuse(durable_pipeline):
    state = durable_pipeline
    assessment_id = "cred_corrupt_checkpoint"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    _build(state, assessment_id, lease)
    saved = state.store.load_stage(assessment_id, "parsing", "request-fingerprint")
    manifest = read_snapshot(saved["artifact_ref"]).manifest
    digest = manifest["blobs"]["normalized_json"]["sha256"]
    blob = state.root / "artifacts" / "objects" / "sha256" / digest[:2] / digest
    blob.write_bytes(b'{"tampered":true}')
    with pytest.raises(SnapshotError, match="integrity"):
        _build(state, assessment_id, lease)
    assert state.build_calls == 1
    assert state.calls == {"claim_check": 1, "reference_check": 1, "code_check": 1}


def test_checkpoint_from_another_task_is_rejected_even_if_database_pointer_is_swapped(durable_pipeline):
    state = durable_pipeline
    for assessment_id in ("cred_owner", "cred_other"):
        state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
        _build(state, assessment_id, state.store.claim_task(assessment_id))
    other = state.store.load_stage("cred_other", "parsing", "request-fingerprint")
    with state.sessions.begin() as session:
        session.execute(update(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == "cred_owner", CredibilityStageRow.stage == "parsing",
        ).values(payload=other))
    with pytest.raises(ValueError, match="does not belong"):
        runtime.StageExecution(state.store, "cred_owner", None, "request-fingerprint").load(
            "parsing", "request-fingerprint",
        )


def test_cached_external_source_identity_tampering_is_rejected(durable_pipeline):
    state = durable_pipeline
    source = state.results["reference_check"].sources[0].model_dump(mode="json")
    source["url"] = "https://different.example/unrelated-evidence"
    with pytest.raises(SnapshotError, match="URL"):
        verify_report_snapshots({"sources": [source]})


def test_replay_validates_source_identity_not_only_blob_hash(durable_pipeline):
    state = durable_pipeline
    assessment_id = "cred_replay_tamper"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    completed = _build(state, assessment_id, lease)
    completed["report"]["sources"][0]["url"] = "https://different.example/unrelated-evidence"
    CredibilityAssessment.model_validate(completed)
    state.store.finish_task(assessment_id, lease, completed)
    with pytest.raises(SnapshotError, match="URL"):
        runtime.replay_task(assessment_id, store=state.store)


def test_partial_external_branch_is_preserved_but_retried_on_resume(durable_pipeline):
    state = durable_pipeline
    refs = state.results["reference_check"]
    source = refs.sources[0]
    source.fetch_status, source.http_status = "rate_limited", 429
    source.snapshot_ref = capture_snapshot(
        kind="metadata", raw=b"rate limited", requested_url=source.url, final_url=source.url,
        fetch_status="rate_limited", http_status=429, is_cached=False,
        metadata={"source_id": source.source_id, "source_url": source.url},
    )
    refs.checks.items[0].outcome = "not_checked"
    refs.checks.matched_count = refs.checks.checked_count = 0
    refs.checks.not_checked_count = 1
    refs.checks.query_completion_ratio = 0
    refs.dimension.check_status = "partial"
    assessment_id = "cred_partial_external"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    partial = _build(state, assessment_id, lease)
    assert partial["report"]["execution"]["checks"]["reference_check"]["status"] == "partial"
    assert partial["report"]["dimensions"][1]["check_status"] == "partial"
    state.store.finish_task(assessment_id, lease, partial)
    # Switch only the reference producer to a successful response.
    state.results["reference_check"] = _results()["reference_check"]
    resumed = _build(state, assessment_id, state.store.claim_task(assessment_id, resume=True))
    assert state.calls == {"claim_check": 1, "reference_check": 2, "code_check": 1}
    assert resumed["report"]["overall"]["score"] == 9
    with state.sessions() as session:
        checkpoints = session.scalars(select(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == assessment_id,
            CredibilityStageRow.stage == "reference_check",
        )).all()
        assert len(checkpoints) == 1 and checkpoints[0].status == "completed"


def test_model_judgment_retry_reuses_valid_extraction_checkpoint(durable_pipeline, monkeypatch):
    """An expensive successful extraction survives a later model-stage failure."""
    from app.credibility.claim_model import generate_claim_candidates
    from app.llm.base import LLMResponse
    from app.llm.retry import LLMUnavailable, RetryPolicy

    state = durable_pipeline
    assessment_id = "cred_model_stage_recovery"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    ctx = copy.deepcopy(state.context)
    runtime.StageExecution(state.store, assessment_id, lease, "request-fingerprint").bind(ctx)
    monkeypatch.setattr("app.credibility.claim_model.log_run", lambda record: None)
    extraction = {
        "selection_scope": "All abstract and introduction claims.", "scope_complete": True, "scope_limitations": [],
        "claims": [{"claim_id": "new_c1", "statement": "Our method improves accuracy.", "importance": "core",
                    "claim_conditions": {}, "claim_evidence": [{
                        "evidence_id": "new_abstract_ev", "mode": "quote", "chunk_id": "abstract",
                        "excerpt": "Our method improves accuracy.",
                    }]}],
    }
    judgment = {"judgments": [{
        "claim_id": "new_c1", "comparison_evidence": [{
            "evidence_id": "new_results_ev", "mode": "quote", "chunk_id": "results",
            "excerpt": "Accuracy increases from 80 to 84.",
        }], "search_scope": "All later body blocks.", "proposed_outcome": "supported",
        "reason": "The results describe improved accuracy.",
    }]}

    class OfflineDeepSeekClient:
        model_id = "model_standard_v1"
        model_version = "deepseek-checkpoint-test"

        def __init__(self, responses):
            self.responses, self.called_stages = list(responses), []

        def complete_json(self, system_prompt, user_prompt, **kwargs):
            task = json.loads(user_prompt)
            self.called_stages.append("extraction" if "untrusted_abstract_and_introduction" in task else "judgment")
            payload = self.responses.pop(0)
            if isinstance(payload, Exception):
                raise payload
            return LLMResponse(json.dumps(payload), self.model_id, self.model_version)

    first = OfflineDeepSeekClient([extraction, LLMUnavailable("Simulated judgment outage")])
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0)
    with pytest.raises(LLMUnavailable):
        generate_claim_candidates(ctx, first, retry_policy=policy)
    assert first.called_stages == ["extraction", "judgment"]
    state.store.release_task(assessment_id, lease)
    resumed_lease = state.store.claim_task(assessment_id)
    runtime.StageExecution(state.store, assessment_id, resumed_lease, "request-fingerprint").bind(ctx)
    second = OfflineDeepSeekClient([judgment])
    generated = generate_claim_candidates(ctx, second, retry_policy=policy)
    assert second.called_stages == ["judgment"]
    assert generated.candidates.claims[0].proposed_outcome == "supported"
    assert [call.purpose for call in generated.model_calls] == ["claim_extraction", "claim_judgment"]


def test_same_rubric_version_with_changed_policy_bytes_changes_task_fingerprint(monkeypatch):
    version = "0.2-provisional-complete"
    monkeypatch.setattr("app.credibility.rubric.load_credibility_rubric",
                        lambda requested: SimpleNamespace(content_sha256="a" * 64))
    original = runtime.request_fingerprint(SOURCE, "model_standard_v1", version, {"pipeline": "unchanged"})
    monkeypatch.setattr("app.credibility.rubric.load_credibility_rubric",
                        lambda requested: SimpleNamespace(content_sha256="b" * 64))
    changed = runtime.request_fingerprint(SOURCE, "model_standard_v1", version, {"pipeline": "unchanged"})
    assert original != changed


def test_replay_rejects_modified_trial_score_without_network_or_model(durable_pipeline):
    state = durable_pipeline
    assessment_id = "cred_score_tamper"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    completed = _build(state, assessment_id, lease)
    completed["report"]["dimensions"][0]["score"] = 2
    state.store.finish_task(assessment_id, lease, completed)
    with pytest.raises(ValueError, match="deterministic replay"):
        runtime.replay_task(assessment_id, store=state.store)


@pytest.mark.parametrize(("producer_status", "stage_status"), [
    ("failed", "failed"), ("not_run", "partial"), ("running", "partial"), ("partial", "partial"),
])
def test_returned_incomplete_producer_result_is_never_cached_as_completed(
    durable_pipeline, producer_status, stage_status,
):
    state = durable_pipeline
    assessment_id = f"cred_returned_{producer_status}"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    from app.credibility.references import _empty_failed

    state.results["reference_check"] = _empty_failed("Producer did not finish", [], check_status=producer_status)
    incomplete = _build(state, assessment_id, lease)
    assert incomplete["report"]["dimensions"][1]["check_status"] == producer_status
    assert incomplete["report"]["execution"]["checks"]["reference_check"]["status"] == stage_status
    with state.sessions() as session:
        checkpoint = session.scalar(select(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == assessment_id,
            CredibilityStageRow.stage == "reference_check",
        ))
        assert checkpoint.status == stage_status
        assert state.store.load_stage(assessment_id, "reference_check", checkpoint.fingerprint) is None
    state.store.finish_task(assessment_id, lease, incomplete)
    assert assessment_id in {row["assessment_id"] for row in state.store.list_recoverable_tasks()}
    state.results["reference_check"] = _results()["reference_check"]
    resumed = _build(state, assessment_id, state.store.claim_task(assessment_id, resume=True))
    assert state.calls == {"claim_check": 1, "reference_check": 2, "code_check": 1}
    assert resumed["report"]["execution"]["checks"]["reference_check"]["status"] == "completed"


def test_legacy_completed_flags_cannot_hide_failed_dimension_or_reuse_its_checkpoint(durable_pipeline):
    from app.credibility.references import _empty_failed

    state = durable_pipeline
    assessment_id = "cred_legacy_bad_completion"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    state.results["reference_check"] = _empty_failed("Failed before any source was fetched", [])
    old = _build(state, assessment_id, lease)
    # Reproduce the earlier worker bug in both durable records: empty failed
    # result but stage/summary flagged completed.
    with state.sessions() as session:
        checkpoint = session.scalar(select(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == assessment_id,
            CredibilityStageRow.stage == "reference_check",
        ))
        fingerprint = checkpoint.fingerprint
    runtime.StageExecution(state.store, assessment_id, lease, "request-fingerprint").record(
        "reference_check", fingerprint, {"result": state.results["reference_check"].model_dump(mode="json")},
        status="completed",
    )
    old["report"]["execution"]["checks"]["reference_check"]["status"] = "completed"
    state.store.finish_task(assessment_id, lease, old)
    assert assessment_id in {row["assessment_id"] for row in state.store.list_recoverable_tasks()}
    state.results["reference_check"] = _results()["reference_check"]
    resumed = _build(state, assessment_id, state.store.claim_task(assessment_id, resume=True))
    assert state.calls == {"claim_check": 1, "reference_check": 2, "code_check": 1}
    event = resumed["report"]["execution"]["checks"]["reference_check"]
    assert event["status"] == "completed" and event["reused"] is False
    assert resumed["report"]["checks"]["references"]["matched_count"] == 1


@pytest.mark.parametrize(("status", "applicability", "expected"), [
    ("completed", "undetermined", "completed"),
    ("partial", "applicable", "partial"),
    ("completed", "not_applicable", "completed"),
    ("not_run", "not_applicable", "completed"),
    ("not_run", "undetermined", "partial"),
    ("failed", "not_applicable", "failed"),
])
@pytest.mark.parametrize("check_name", ["claim_check", "reference_check", "code_check"])
def test_execution_state_distinguishes_unscored_inapplicable_and_unexecuted_checks(
    durable_pipeline, status, applicability, expected, check_name,
):
    from app.credibility.execution import _result_state

    result = durable_pipeline.results[check_name].model_copy(deep=True)
    result.dimension.check_status, result.dimension.applicability = status, applicability
    result.dimension.score_status = "unable_to_assess"
    assert _result_state(result) == expected


@pytest.mark.parametrize("legacy_completed_flag", [False, True])
def test_reference_budget_exhaustion_is_partial_and_resume_retries_only_that_branch(
    durable_pipeline, legacy_completed_flag,
):
    """All requests can be OK while supplementary work still has not finished."""
    state = durable_pipeline
    refs = state.results["reference_check"]
    refs.dimension.check_status = "partial"
    refs.dimension.summary = "The supplementary reference deadline was exhausted."
    refs.checks.items[0].outcome = "unverified"
    refs.checks.items[0].reason = "[budget_exhausted] Remaining official sources were not queried."
    refs.checks.matched_count, refs.checks.unverified_count = 0, 1
    assert all(source.fetch_status == "ok" for source in refs.sources)
    assert refs.checks.extraction_status == "complete" and refs.checks.not_checked_count == 0
    previous = []
    original_reference_producer = state.checks.references

    def reference_producer(branch):
        previous.append(copy.deepcopy(branch.previous_check))
        return original_reference_producer(branch)

    state.checks = shell.CheckSet(state.checks.claims, reference_producer, state.checks.code)
    assessment_id = f"cred_reference_budget_{int(legacy_completed_flag)}"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    partial = _build(state, assessment_id, lease)
    assert partial["report"]["execution"]["checks"]["reference_check"]["status"] == "partial"
    assert partial["report"]["coverage"][1]["technical_failure_count"] == 0
    assert partial["report"]["reference_verification_summary"]["unresolved_items"][0][
        "reason_code"
    ] == "budget_exhausted"
    with state.sessions() as session:
        checkpoint = session.scalar(select(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == assessment_id,
            CredibilityStageRow.stage == "reference_check",
        ))
        fingerprint = checkpoint.fingerprint
        assert checkpoint.status == "partial"
    assert state.store.load_stage(assessment_id, "reference_check", fingerprint) is None
    if legacy_completed_flag:
        # An older worker had no failed HTTP sources and mislabelled this
        # partial producer completed. The reader must revalidate its payload.
        runtime.StageExecution(state.store, assessment_id, lease, "request-fingerprint").record(
            "reference_check", fingerprint, {"result": refs.model_dump(mode="json")}, status="completed",
        )
        # Revalidate that old checkpoint while this attempt still owns its
        # lease; discovery of historical tasks has a separate storage policy.
        resumed_lease = lease
    else:
        state.store.finish_task(assessment_id, lease, partial)
        assert assessment_id in {row["assessment_id"] for row in state.store.list_recoverable_tasks()}
        resumed_lease = state.store.claim_task(assessment_id, resume=True)
    state.results["reference_check"] = _results()["reference_check"]
    resumed = _build(state, assessment_id, resumed_lease)
    assert state.calls == {"claim_check": 1, "reference_check": 2, "code_check": 1}
    assert previous[0] is None
    assert previous[1]["checks"]["items"][0]["reason"].startswith("[budget_exhausted]")
    event = resumed["report"]["execution"]["checks"]["reference_check"]
    assert event["status"] == "completed" and event["reused"] is False
    assert resumed["report"]["checks"]["references"]["matched_count"] == 1
    assert state.store.load_stage_record(assessment_id, "reference_check", fingerprint)["status"] == "completed"


def test_recovered_reference_failure_is_audited_and_completed_checkpoint_is_reused(durable_pipeline):
    state = durable_pipeline
    refs = state.results["reference_check"]
    failed = refs.sources[0]
    failed.fetch_status, failed.http_status = "rate_limited", 429
    failed.snapshot_ref = capture_snapshot(
        kind="metadata", raw=b"rate limited before successful official fallback",
        requested_url=failed.url, final_url=failed.url, retrieved_at=TIME,
        fetch_status="rate_limited", http_status=429, is_cached=False,
        metadata={"source_id": failed.source_id, "source_url": failed.url},
    )
    confirmed = _source("official_confirmed", "https://arxiv.org/abs/2503.12345v1", "metadata")
    refs.sources.append(confirmed)
    refs.checks.items[0].source_ids.append(confirmed.source_id)
    refs.checks.items[0].reason = "Official-source identity proof recovered the failed Crossref attempt."
    assert refs.dimension.check_status == "completed" and refs.checks.items[0].outcome == "matched"
    assessment_id = "cred_recovered_reference_attempt"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    state.failing.add("claim_check")
    first = _build(state, assessment_id, lease)
    assert first["report"]["execution"]["checks"]["reference_check"]["status"] == "completed"
    assert first["report"]["coverage"][1]["technical_failure_count"] == 1
    assert first["report"]["reference_verification_summary"]["verification_rate"] == 1.0
    state.store.finish_task(assessment_id, lease, first)
    state.failing.clear()
    resumed = _build(state, assessment_id, state.store.claim_task(assessment_id, resume=True))
    assert state.calls == {"claim_check": 2, "reference_check": 1, "code_check": 1}
    assert resumed["report"]["execution"]["checks"]["reference_check"]["reused"] is True
    assert resumed["report"]["coverage"][1]["technical_failure_count"] == 1
    assert {source["fetch_status"] for source in resumed["report"]["sources"]} == {"ok", "rate_limited"}
    assert resumed["report"]["dimensions"][1]["score"] == 10


def test_code_sources_still_require_success_even_when_reference_failures_can_be_recovered(durable_pipeline):
    from app.credibility.execution import _result_state

    result = durable_pipeline.results["code_check"]
    result.sources[0].fetch_status = "timeout"
    assert result.dimension.check_status == "completed"
    assert _result_state(result) == "partial"


def test_code_retry_cannot_reuse_failed_empty_base_facts(durable_pipeline, monkeypatch):
    from app.credibility.execution import check_code_with_model

    state = durable_pipeline
    failed = state.results["code_check"].model_copy(deep=True)
    failed.dimension.check_status = "failed"
    failed.sources = []
    ctx = copy.deepcopy(state.context)
    ctx.previous_check = failed.model_dump(mode="json")
    base_calls = []

    def base(branch):
        base_calls.append(branch)
        return state.results["code_check"].model_copy(deep=True)

    monkeypatch.setattr("app.credibility.code_checks.check_code", base)
    result = check_code_with_model(ctx)
    assert len(base_calls) == 1
    assert result.dimension.check_status == "completed"
    assert result.sources


def test_legacy_bad_completed_checkpoint_can_be_replaced_by_a_new_truthful_failure(durable_pipeline):
    from app.credibility.references import _empty_failed

    state = durable_pipeline
    assessment_id = "cred_legacy_failed_again"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    state.results["reference_check"] = _empty_failed("Nothing could be fetched", [])
    _build(state, assessment_id, lease)
    with state.sessions() as session:
        record = session.scalar(select(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == assessment_id,
            CredibilityStageRow.stage == "reference_check",
        ))
        fingerprint = record.fingerprint
    runtime.StageExecution(state.store, assessment_id, lease, "request-fingerprint").record(
        "reference_check", fingerprint, {"result": state.results["reference_check"].model_dump(mode="json")},
        status="completed",
    )
    retried = _build(state, assessment_id, lease)
    assert retried["report"]["execution"]["checks"]["reference_check"]["status"] == "failed"
    assert state.store.load_stage(assessment_id, "reference_check", fingerprint) is None
    assert state.store.load_stage_record(assessment_id, "reference_check", fingerprint)["status"] == "failed"
    assert state.calls["reference_check"] == 2


def test_completed_producers_with_bounded_scope_are_not_offered_for_technical_retry(durable_pipeline):
    from app.storage.credibility_tasks import LeaseConflict

    state = durable_pipeline
    assessment_id = "cred_bounded_scope"
    state.results["claim_check"].checks.scope_complete = False
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    complete = _build(state, assessment_id, lease)
    report = complete["report"]
    assert all(row["check_status"] == "completed" for row in report["dimensions"])
    assert report["checks"]["claims"]["scope_complete"] is False
    assert report["checks"]["code"]["scope_complete"] is False
    assert report["scope_complete"] is False
    assert all(event["status"] == "completed" for event in report["execution"]["checks"].values())
    assert all(row["technical_failure_count"] == 0 for row in report["coverage"])
    state.store.finish_task(assessment_id, lease, complete)
    assert state.store.list_recoverable_tasks() == []
    with pytest.raises(LeaseConflict):
        state.store.claim_task(assessment_id, resume=True)


def test_code_facts_recovery_restores_public_auth_proof_in_cold_process_without_metadata_get(
    durable_pipeline, monkeypatch,
):
    import httpx
    from pydantic import SecretStr

    from app.credibility.code_lookup import PublicCodeLookup
    from app.credibility.execution import check_code_with_model
    from app.credibility.snapshots import derive_snapshot

    state = durable_pipeline
    settings = get_settings()
    monkeypatch.setattr(settings, "credibility_code_model_enabled", True)
    monkeypatch.setattr(settings, "credibility_code_tool_mode", "prefetch")
    monkeypatch.setattr(settings, "github_token", SecretStr("offline_auth_credential"))
    metadata = {"id": 123, "private": False, "full_name": "public/repository"}
    api_url = "https://api.github.com/repos/public/repository"
    response_ref = capture_snapshot(
        kind="public_code_response", raw=json.dumps(metadata).encode(), normalized_json=metadata,
        requested_url=api_url, final_url=api_url, fetch_status="ok", http_status=200,
    )
    previous = state.results["code_check"].model_copy(deep=True)
    previous.sources[0].url = api_url
    previous.sources[0].snapshot_ref = derive_snapshot(
        response_ref, kind="repository",
        metadata={"source_id": previous.sources[0].source_id, "source_url": api_url},
    )
    previous.external_review = {"mode": "prefetch", "status": "failed"}
    ctx = copy.deepcopy(state.context)
    ctx.model_id = "model_standard_v1"
    ctx.previous_check = previous.model_dump(mode="json")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/repos/public/repository/issues/186/comments"
        assert request.headers.get("authorization") == "Bearer offline_auth_credential"
        return httpx.Response(200, json=[])

    actual_client = httpx.Client
    monkeypatch.setattr("app.credibility.code_lookup.httpx.Client", lambda **kwargs: actual_client(
        transport=httpx.MockTransport(handler), **kwargs))
    cold = PublicCodeLookup(interval=0)
    monkeypatch.setattr(cold, "_public", lambda _: True)
    monkeypatch.setattr("app.credibility.code_lookup.LOOKUP", cold)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _: object())

    def forbid_base(_):
        raise AssertionError("Successful base facts must not be fetched again")

    monkeypatch.setattr("app.credibility.code_checks.check_code", forbid_base)
    # This fixture isolates restoring public-auth proof in a cold process;
    # repository continuation has its own bounded collection tests below.
    monkeypatch.setattr("app.credibility.code_checks.complete_confirmed_repositories",
                        lambda _ctx, result, **_kwargs: result)

    def reviewed(_ctx, result, _client, **_kwargs):
        assert cold.get(api_url + "/issues/186/comments").status == "ok"
        return SimpleNamespace(checks=result, model_calls=[], prompt_version="offline",
                               mode="prefetch", status="completed", audit={"status": "completed"}, warnings=[])

    monkeypatch.setattr("app.credibility.code_model.enrich_code_checks", reviewed)
    result = check_code_with_model(ctx)
    assert result.external_review["base_facts_reused"] is True
    assert result.external_review["status"] == "completed"
    assert cold._public_repositories == {"public/repository"}
    assert len(requests) == 1


@pytest.mark.parametrize("first_attempts,second_expected", [(1, True), (3, False)])
@pytest.mark.parametrize("first_status", ["completed", "partial"])
def test_newly_confirmed_repository_is_collected_then_semantically_reviewed_with_shared_budgets(
    durable_pipeline, monkeypatch, first_attempts, second_expected, first_status,
):
    from app.credibility import code_checks, code_model
    from app.credibility.execution import check_code_with_model

    state, settings = durable_pipeline, get_settings()
    monkeypatch.setattr(settings, "credibility_code_model_enabled", True)
    monkeypatch.setattr(settings, "credibility_code_max_rounds", 3)
    monkeypatch.setattr(settings, "credibility_code_max_tool_calls", 6)
    base = state.results["code_check"].model_copy(deep=True)
    base.checks.repositories[0].association = "uncertain"
    base.external_request_count = 10
    monkeypatch.setattr(code_checks, "check_code", lambda _ctx: base.model_copy(deep=True))
    ctx = copy.deepcopy(state.context)
    ctx.model_id = "model_standard_v1"
    client = object()
    clients = []
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _model: clients.append(client) or client)
    events, options = [], []

    def semantic(_ctx, result, used_client, **kwargs):
        assert used_client is client
        options.append(kwargs)
        index = len(options)
        events.append(f"semantic_{index}")
        enriched = result.model_copy(deep=True)
        if index == 1:
            assert enriched.checks.repositories[0].association == "uncertain"
            enriched.checks.repositories[0].association = "confirmed"
            attempts, tool_count = first_attempts, 2
        else:
            assert kwargs["target_repository_ids"] == []
            assert kwargs["target_discussion_ids"] == ["new_thread"]
            assert kwargs["max_rounds"] == 2 and kwargs["max_tool_calls"] == 4
            assert enriched.external_request_count == 17
            enriched.checks.discussions[0].relevance = "relevant"
            attempts, tool_count = 1, 1
        enriched.external_request_count += tool_count
        call = CredibilityModelCall(purpose=f"semantic_pass_{index}", provider="fixture", model_version="offline")
        audit = {"attempt_count": attempts, "tool_call_count": tool_count,
                 "attempts": [{"pass": index}], "rounds": [{"pass": index}],
                 "tool_calls": [{"pass": index}] * tool_count, "attempt_refs": []}
        status = first_status if index == 1 else "completed"
        if status == "partial":
            enriched.dimension.check_status = "partial"
            audit["output_snapshot_ref"] = capture_snapshot(
                kind="external_model_output", normalized_json={"confirmed_repository": "repo_1"},
            )
        return SimpleNamespace(checks=enriched, model_calls=[call], prompt_version="offline-two-pass",
                               mode="prefetch", status=status, audit=audit, warnings=[])

    def collect(_ctx, result, *, max_requests):
        events.append("collect_confirmed")
        assert max_requests == 40 and result.external_request_count == 12
        assert result.checks.repositories[0].association == "confirmed"
        enriched = result.model_copy(deep=True)
        enriched.dimension.check_status = "completed"
        enriched.external_request_count += 5
        enriched.followup_state["repo_1"] = {"inspection_status": "completed", "inspected_subdir": "code"}
        enriched.checks.discussions.append(CredibilityDiscussion(
            discussion_id="new_thread", repository_id="repo_1", source_ids=[], evidence_ids=[],
            signal="discussion_only", relevance="undetermined", independence="unknown", conditions="Unknown",
            resolution="unknown", author_response_source_ids=[], scoring_eligible=False, reason="New issue read.",
        ))
        return enriched

    monkeypatch.setattr(code_model, "enrich_code_checks", semantic)
    monkeypatch.setattr(code_checks, "complete_confirmed_repositories", collect, raising=False)
    result = check_code_with_model(ctx)
    assert clients == [client]
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert events == (["semantic_1", "collect_confirmed", "semantic_2"] if second_expected
                      else ["semantic_1", "collect_confirmed"])
    assert len(audit["passes"]) == (2 if second_expected else 1)
    assert len(result.model_calls) == (2 if second_expected else 1)
    assert audit["attempt_count"] == (2 if second_expected else 3)
    assert audit["followup"]["new_discussion_ids"] == ["new_thread"]
    assert result.followup_state["repo_1"]["inspected_subdir"] == "code"
    assert result.checks.discussions[0].scoring_eligible is False
    if second_expected:
        assert result.external_review["status"] == first_status
        assert result.dimension.check_status == first_status
        assert result.external_request_count == 18
        assert len(audit["tool_calls"]) == 3 and len(audit["rounds"]) == 2
    else:
        assert result.external_review["status"] == result.dimension.check_status == "partial"
        assert result.external_request_count == 17
        assert any("Model-round budget exhausted" in reason for reason in result.checks.scope_limitations)


def test_semantic_continuation_can_use_archived_evidence_with_no_external_request_budget(
    durable_pipeline, monkeypatch,
):
    from app.credibility import code_checks, code_model
    from app.credibility.execution import check_code_with_model

    state, settings = durable_pipeline, get_settings()
    monkeypatch.setattr(settings, "credibility_code_model_enabled", True)
    base = state.results["code_check"].model_copy(deep=True)
    base.external_request_count = 40
    monkeypatch.setattr(code_checks, "check_code", lambda _ctx: base.model_copy(deep=True))
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _model: object())
    options = []

    def semantic(_ctx, result, _client, **kwargs):
        options.append(kwargs)
        assert kwargs["max_tool_calls"] == 0
        return SimpleNamespace(checks=result, model_calls=[], prompt_version="archived-only", mode="prefetch",
                               status="not_needed", audit={"attempt_count": 0, "tool_calls": []}, warnings=[])

    monkeypatch.setattr(code_model, "enrich_code_checks", semantic)
    monkeypatch.setattr(code_checks, "complete_confirmed_repositories", lambda _ctx, result, **kwargs: result,
                        raising=False)
    ctx = copy.deepcopy(state.context)
    ctx.model_id = "model_standard_v1"
    result = check_code_with_model(ctx)
    assert len(options) == 1 and result.external_request_count == 40
    assert result.external_review["status"] == "not_needed"


@pytest.mark.parametrize("status", ["partial", "failed", "unsupported"])
def test_unvalidated_semantic_result_cannot_trigger_confirmed_repository_continuation(
    durable_pipeline, monkeypatch, status,
):
    from app.credibility import code_checks, code_model
    from app.credibility.execution import check_code_with_model

    state = durable_pipeline
    monkeypatch.setattr(get_settings(), "credibility_code_model_enabled", True)
    monkeypatch.setattr(code_checks, "check_code", lambda _ctx: state.results["code_check"].model_copy(deep=True))
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _model: object())
    monkeypatch.setattr(code_model, "enrich_code_checks", lambda _ctx, result, _client, **kwargs: SimpleNamespace(
        checks=result, model_calls=[], prompt_version="unvalidated", mode="prefetch", status=status,
        audit={"attempt_count": 1}, warnings=[],
    ))
    continued = []
    monkeypatch.setattr(code_checks, "complete_confirmed_repositories",
                        lambda *_args, **_kwargs: continued.append(True))
    ctx = copy.deepcopy(state.context)
    ctx.model_id = "model_standard_v1"
    result = check_code_with_model(ctx)
    assert continued == [] and result.external_review["status"] == status
    assert result.dimension.check_status == "partial"


def test_repository_continuation_state_survives_checkpoint_without_public_repository_fields(durable_pipeline):
    state = durable_pipeline
    followup = {"repo_1": {"requested_revision": "release/v1", "inspected_subdir": "experiments",
                           "implementation_checked": True, "discussions_checked": False,
                           "inspection_status": "partial"}}
    state.results["code_check"].followup_state = copy.deepcopy(followup)
    state.results["code_check"].dimension.check_status = "partial"
    assessment_id = "cred_internal_repository_continuation"
    state.store.create_task(_queued(assessment_id), "request-fingerprint", {})
    lease = state.store.claim_task(assessment_id)
    completed = _build(state, assessment_id, lease)
    public_repository = completed["report"]["checks"]["code"]["repositories"][0]
    assert "followup_state" not in completed["report"]["checks"]["code"]
    assert "requested_revision" not in public_repository and "inspected_subdir" not in public_repository
    with state.sessions() as session:
        record = session.scalar(select(CredibilityStageRow).where(
            CredibilityStageRow.assessment_id == assessment_id, CredibilityStageRow.stage == "code_check",
        ))
        fingerprint = record.fingerprint
    saved = runtime.StageExecution(state.store, assessment_id, lease, "request-fingerprint").load_partial(
        "code_check", fingerprint,
    )
    assert saved["result"]["followup_state"] == followup
    assert CodeCheckResult.model_validate(saved["result"]).followup_state == followup


@pytest.mark.parametrize("first_attempts", [1, 3])
def test_dependency_association_precedes_only_confirmed_discussion_review(
    durable_pipeline, monkeypatch, first_attempts,
):
    """A long dependency README and its issues cannot crowd the paper's review."""
    from app.credibility import code_checks, code_model
    from app.credibility.execution import check_code_with_model

    state, settings = durable_pipeline, get_settings()
    monkeypatch.setattr(settings, "credibility_code_model_enabled", True)
    monkeypatch.setattr(settings, "credibility_code_discovery_enabled", False)
    monkeypatch.setattr(settings, "credibility_code_max_rounds", 3)
    monkeypatch.setattr(settings, "credibility_code_max_tool_calls", 6)
    base = state.results["code_check"].model_copy(deep=True)
    dependency = base.checks.repositories[0].model_copy(deep=True)
    dependency.repository_id, dependency.association = "dependency", "uncertain"
    dependency.association_evidence_ids, dependency.implementation_evidence_ids = [], []
    source = _source("dependency_source", "https://github.com/example/dependency", "repository")
    dependency.source_id = source.source_id
    base.sources.append(source)
    base.checks.repositories.append(dependency)
    for did, rid in [("paper_thread", "repo_1"), ("dependency_thread", "dependency")]:
        base.checks.discussions.append(CredibilityDiscussion(
            discussion_id=did, repository_id=rid, source_ids=[], evidence_ids=[],
            signal="discussion_only", relevance="undetermined", independence="unknown",
            conditions="Unknown", resolution="unknown", author_response_source_ids=[],
            scoring_eligible=False, reason="Requires paper-specific review.",
        ))
    monkeypatch.setattr(code_checks, "check_code", lambda _ctx: base.model_copy(deep=True))
    monkeypatch.setattr(code_checks, "complete_confirmed_repositories", lambda _ctx, result, **_: result)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _: object())
    observed = []

    def semantic(_ctx, result, _client, **options):
        observed.append(options)
        output = result.model_copy(deep=True)
        if len(observed) == 1:
            assert options["target_discussion_ids"] == []
            output.checks.repositories[1].association = "rejected"
            attempts = first_attempts
        else:
            assert options["target_repository_ids"] == []
            assert options["target_discussion_ids"] == ["paper_thread"]
            assert options["max_rounds"] == 2 and options["max_tool_calls"] == 6
            output.checks.discussions[0].relevance = "relevant"
            attempts = 1
        return SimpleNamespace(
            checks=output, model_calls=[], prompt_version="offline-phase-test", mode="prefetch",
            status="completed", warnings=[],
            audit={"attempt_count": attempts, "tool_call_count": 0, "rounds": [], "tool_calls": []},
        )

    monkeypatch.setattr(code_model, "enrich_code_checks", semantic)
    ctx = copy.deepcopy(state.context)
    ctx.model_id = "model_standard_v1"
    result = check_code_with_model(ctx)
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["followup"]["deferred_discussion_ids"] == ["paper_thread"]
    assert result.checks.repositories[1].association == "rejected"
    assert result.checks.discussions[1].relevance == "undetermined"
    assert result.checks.discussions[0].scoring_eligible is False
    assert len(observed) == (2 if first_attempts == 1 else 1)
    if first_attempts == 3:
        assert result.dimension.check_status == "partial"
        assert audit["remaining_model_rounds"] == 0
    else:
        assert audit["remaining_model_rounds"] == 1


def test_resumed_partial_keeps_rejected_dependency_discussions_out_of_review_budget(
    durable_pipeline, monkeypatch,
):
    """A resumed association result must not re-admit the excluded dependency."""
    from app.credibility import code_checks, code_model
    from app.credibility.execution import check_code_with_model

    state, settings = durable_pipeline, get_settings()
    monkeypatch.setattr(settings, "credibility_code_model_enabled", True)
    monkeypatch.setattr(settings, "credibility_code_discovery_enabled", False)
    monkeypatch.setattr(settings, "credibility_code_max_rounds", 3)
    monkeypatch.setattr(settings, "credibility_code_max_tool_calls", 6)
    previous = state.results["code_check"].model_copy(deep=True)
    previous.dimension.check_status = "partial"
    previous.external_request_count = 12
    dependency = previous.checks.repositories[0].model_copy(deep=True)
    dependency.repository_id, dependency.association = "dependency", "rejected"
    dependency.association_evidence_ids, dependency.implementation_evidence_ids = [], []
    source = _source("dependency_source", "https://github.com/example/dependency", "repository")
    dependency.source_id = source.source_id
    previous.sources.append(source)
    previous.checks.repositories.append(dependency)
    for did, rid in [("paper_thread", "repo_1"), ("dependency_thread", "dependency")]:
        previous.checks.discussions.append(CredibilityDiscussion(
            discussion_id=did, repository_id=rid, source_ids=[], evidence_ids=[],
            signal="discussion_only", relevance="undetermined", independence="unknown",
            conditions="Unknown", resolution="unknown", author_response_source_ids=[],
            scoring_eligible=False, reason="Retained after the prior execution exhausted its model rounds.",
        ))
    original_dependency_thread = previous.checks.discussions[1].model_dump()
    monkeypatch.setattr(code_checks, "check_code", lambda _ctx: (_ for _ in ()).throw(
        AssertionError("Verified successful facts should be reused on this continuation")))
    monkeypatch.setattr(code_checks, "complete_confirmed_repositories", lambda _ctx, result, **_: result)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _: object())
    observed = []

    def semantic(_ctx, result, _client, **options):
        observed.append(options)
        assert options["target_discussion_ids"] == ["paper_thread"]
        assert options["max_rounds"] == 3 and options["max_tool_calls"] == 6
        assert result.external_request_count == 0
        _, targets = code_model._targets(result, discussion_ids=options["target_discussion_ids"])
        assert [item.discussion_id for item in targets] == ["paper_thread"]
        output = result.model_copy(deep=True)
        output.dimension.check_status = "completed"
        output.checks.discussions[0].relevance = "relevant"
        output.external_request_count = 1
        return SimpleNamespace(
            checks=output, model_calls=[CredibilityModelCall(purpose="paper_thread_review", provider="fixture",
                                                           model_version="offline")],
            prompt_version="offline-resume-review", status="completed", warnings=[],
            audit={"attempt_count": 1, "tool_call_count": 1, "tool_calls": [{"target": "paper_thread"}]},
        )

    monkeypatch.setattr(code_model, "enrich_code_checks", semantic)
    ctx = copy.deepcopy(state.context)
    ctx.model_id, ctx.previous_check = "model_standard_v1", previous.model_dump(mode="json")
    result = check_code_with_model(ctx)
    assert len(observed) == 1
    assert result.external_review["base_facts_reused"] is True
    assert result.external_review["status"] == "completed"
    assert result.checks.discussions[1].model_dump() == original_dependency_thread
    assert len(result.checks.discussions) == 2 and result.external_request_count == 1
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["remaining_model_rounds"] == 2 and audit["remaining_tool_calls"] == 5
