"""Offline integration checks for the independent reproduction-report follow-up."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.credibility import code_checks, code_model, execution, runtime
from app.credibility.code_reproduction_discovery import search_reproduction_reports
from app.credibility.context import CheckContext
from app.credibility.models import (
    CodeCheckResult,
    CredibilityCodeChecks,
    CredibilityCodeDiscoveryCandidate,
    CredibilityDimension,
    CredibilityModelCall,
)
from app.credibility.snapshots import read_snapshot
from app.llm.fixture import FixtureLLMClient
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper


def _call(purpose):
    return CredibilityModelCall(purpose=purpose, provider="fixture", model_version="offline-test")


@pytest.fixture
def report_execution(monkeypatch):
    from app.credibility import code_discovery_provider, code_reproduction_discovery

    settings = get_settings()
    for name in (
        "credibility_code_model_enabled", "credibility_code_discovery_enabled",
        "credibility_code_reproduction_search_enabled",
    ):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, "credibility_code_max_source_reads", 10)
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2401.12345v1"}, repo_root=Path("."),
        generated_at="2026-10-21T00:00:00Z", ref=None, unresolved_reason=None,
        model_id="model_standard_v1", assessment_id="offline-report-execution",
        doc=ParsedDocument(
            paper=Paper(paper_id="execution", title="A Reliable Method for Learning",
                        authors=["Alice Smith"], arxiv_id="2401.12345v1", parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(chunk_id="body", text="We evaluate our method.", page_start=1)],
        ),
    )
    base = CodeCheckResult(
        dimension=CredibilityDimension(
            dimension_id="code_reproduction", applicability="applicable", applicability_reason="Empirical study.",
            check_status="completed", score_status="scored", score=8, weight=1,
            score_rule_ids=["frozen-rule"], summary="Official implementation found.",
            unassessed_reason=None, evidence_ids=[], finding_ids=[],
        ),
        checks=CredibilityCodeChecks(
            scope_complete=False, scope_limitations=[], searches=[], code_availability="available",
            reproduction_signal="no_feedback_found", repositories=[], discussions=[],
            base_score_rule_id="frozen-rule", score_rule_id="frozen-rule",
        ),
        model_calls=[_call("paper-link")], external_request_count=3,
    )
    state = SimpleNamespace(
        ctx=ctx, base=base, settings=settings, client=SimpleNamespace(supports_native_tools=True),
        provider_options=[], report_calls=[], review_error=None, report_error=None,
        report_status="completed", report_warnings=["Snippet-only report leads remain unverified."],
        add_candidate=True,
    )
    monkeypatch.setattr(code_checks, "check_code", lambda _ctx: state.base.model_copy(deep=True))
    monkeypatch.setattr(code_checks, "complete_confirmed_repositories", lambda _ctx, result, **_kwargs: result)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _model: state.client)

    def provider(_client, **options):
        assert _client is state.client
        state.provider_options.append(options)
        return SimpleNamespace(max_requests=options["max_requests"], request_count=0)

    def review(_ctx, result, _client, **_kwargs):
        if state.review_error:
            raise state.review_error
        return SimpleNamespace(
            checks=result, model_calls=[_call("main-review")], prompt_version="offline-review",
            status="completed", audit={"attempt_count": 1, "tool_call_count": 0, "tool_calls": []}, warnings=[],
        )

    def reports(_ctx, result, client, provider, **options):
        state.report_calls.append((result.model_copy(deep=True), client, provider, options))
        if state.report_error:
            raise state.report_error
        if state.add_candidate and state.report_status == "completed":
            result.discovery.candidates.append(CredibilityCodeDiscoveryCandidate(
                candidate_id="external-report", url="https://openreview.net/forum?id=external-report",
                title="Reproduction report", resource_kind="third_party_reproduction", status="candidate",
                reason="A summary-level lead; no independent reproduction was verified.",
            ))
        return SimpleNamespace(
            result=result, model_calls=[_call("reproduction-reports")], status=state.report_status,
            audit={"status": state.report_status, "search_count": 1}, warnings=state.report_warnings,
        )

    monkeypatch.setattr(code_discovery_provider, "discovery_provider_for", provider)
    monkeypatch.setattr(code_model, "enrich_code_checks", review)
    monkeypatch.setattr(code_reproduction_discovery, "search_reproduction_reports", reports)
    return state


def _assert_scores_unchanged(result, base):
    assert result.dimension.score == base.dimension.score
    assert result.dimension.score_status == base.dimension.score_status
    assert result.dimension.score_rule_ids == base.dimension.score_rule_ids
    assert result.checks.base_score_rule_id == base.checks.base_score_rule_id
    assert result.checks.score_rule_id == base.checks.score_rule_id
    assert result.checks.reproduction_signal == base.checks.reproduction_signal


def test_available_official_code_still_searches_reports_and_merges_provenance(report_execution):
    state = report_execution
    result = execution.check_code_with_model(state.ctx)
    assert len(state.report_calls) == 1
    reviewed, client, provider, options = state.report_calls[0]
    assert reviewed.checks.code_availability == "available"
    assert client is state.client and provider.max_requests == 2
    assert options["max_source_reads"] == 10
    assert result.discovery.status == "not_needed"
    assert result.discovery.candidates[0].resource_kind == "third_party_reproduction"
    assert result.discovery.candidates[0].status == "candidate"
    assert [call.purpose for call in result.model_calls] == [
        "paper-link", "main-review", "reproduction-reports",
    ]
    assert state.report_warnings[0] in result.external_review["warnings"]
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["discovery"]["reproduction_reports"] == {"status": "completed", "search_count": 1}
    assert audit["followup"]["reproduction_reports"] == {"status": "completed", "reused": False}
    assert audit["model_call_count"] == 3
    _assert_scores_unchanged(result, state.base)


def test_main_semantic_failure_does_not_suppress_report_search(report_execution):
    state = report_execution
    state.review_error = ValueError("offline review failed")
    result = execution.check_code_with_model(state.ctx)
    assert len(state.report_calls) == 1
    assert result.dimension.check_status == "partial"
    assert result.external_review["status"] == "failed"
    assert [call.purpose for call in result.model_calls] == ["paper-link", "reproduction-reports"]
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["followup"]["error_code"] == "ValueError"
    assert audit["discovery"]["reproduction_reports"]["status"] == "completed"
    assert any("ValueError" in warning for warning in result.external_review["warnings"])
    _assert_scores_unchanged(result, state.base)


def test_fixture_with_configured_real_key_never_creates_paid_search_provider(report_execution, monkeypatch):
    from app.credibility import code_reproduction_discovery

    state = report_execution
    state.client = FixtureLLMClient()
    state.ctx.model_id = "model_fixture_v1"
    monkeypatch.setattr(state.settings, "deepseek_api_key", "offline-provider-key")
    monkeypatch.setattr(code_reproduction_discovery, "search_reproduction_reports", search_reproduction_reports)
    result = execution.check_code_with_model(state.ctx)
    assert state.provider_options == []
    assert state.client.calls == []
    assert result.external_request_count == state.base.external_request_count
    assert result.discovery.candidates == []
    assert all(query.status == "not_run" for query in result.discovery.queries)
    _assert_scores_unchanged(result, state.base)


@pytest.mark.parametrize("flag", [
    "credibility_code_model_enabled", "credibility_code_discovery_enabled",
    "credibility_code_reproduction_search_enabled",
])
def test_disabled_features_do_not_run_report_followup(report_execution, monkeypatch, flag):
    state = report_execution
    monkeypatch.setattr(state.settings, flag, False)
    result = execution.check_code_with_model(state.ctx)
    assert state.report_calls == []
    assert state.provider_options == []
    _assert_scores_unchanged(result, state.base)


def test_not_applicable_result_reaches_report_helper_without_changing_its_skip(report_execution):
    state = report_execution
    state.base.checks.code_availability = "not_applicable"
    state.base.checks.reproduction_signal = "not_applicable"
    state.base.dimension.applicability = "not_applicable"
    state.base.dimension.score_status = "not_applicable"
    state.base.dimension.score = None
    state.report_status = "not_needed"
    state.report_warnings = []
    state.add_candidate = False
    result = execution.check_code_with_model(state.ctx)
    assert len(state.report_calls) == 1
    assert state.report_calls[0][0].checks.code_availability == "not_applicable"
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["followup"]["reproduction_reports"]["status"] == "not_needed"
    assert result.discovery.candidates == []
    assert result.dimension.applicability == "not_applicable"
    _assert_scores_unchanged(result, state.base)


@pytest.mark.parametrize(("consumed", "remaining"), [(3, 2), (9, 1), (10, 0), (11, 0)])
def test_report_provider_respects_remaining_shared_source_budget(report_execution, consumed, remaining):
    state = report_execution
    state.base.external_request_count = consumed
    execution.check_code_with_model(state.ctx)
    assert len(state.provider_options) == 1
    assert state.provider_options[0]["max_requests"] == remaining
    assert state.report_calls[0][2].max_requests <= min(2, max(0, 10 - consumed))
    assert state.report_calls[0][3]["max_source_reads"] == 10


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_incomplete_report_search_preserves_scores_and_marks_execution_partial(report_execution, status):
    state = report_execution
    state.report_status = status
    result = execution.check_code_with_model(state.ctx)
    assert result.dimension.check_status == result.external_review["status"] == "partial"
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["discovery"]["reproduction_reports"]["status"] == status
    assert state.report_warnings[0] in result.external_review["warnings"]
    _assert_scores_unchanged(result, state.base)


def test_report_helper_exception_keeps_existing_review_and_records_failure(report_execution):
    state = report_execution
    state.report_error = RuntimeError("offline optional step failed")
    result = execution.check_code_with_model(state.ctx)
    assert result.dimension.check_status == result.external_review["status"] == "partial"
    assert [call.purpose for call in result.model_calls] == ["paper-link", "main-review"]
    audit = read_snapshot(result.external_review["audit_ref"]).normalized_json
    assert audit["followup"]["reproduction_reports"] == {"status": "failed", "error_code": "RuntimeError"}
    assert any("RuntimeError" in warning for warning in result.external_review["warnings"])
    _assert_scores_unchanged(result, state.base)


@pytest.mark.parametrize(("setting", "version_key", "new_value"), [
    ("credibility_code_discussion_batch_size", "code_discussion_batch_size", 6),
    ("credibility_code_max_reviewed_discussions", "code_max_reviewed_discussions", 31),
    ("credibility_code_discussion_batch_rounds", "code_discussion_batch_rounds", 3),
    ("credibility_code_reproduction_search_enabled", "code_reproduction_search_enabled", True),
])
def test_report_and_discussion_configuration_invalidates_request_versions(monkeypatch, setting, version_key, new_value):
    settings = get_settings()
    before = runtime.request_versions()
    monkeypatch.setattr(settings, setting, new_value)
    after = runtime.request_versions()
    assert before[version_key] != after[version_key]
    assert after[version_key] == new_value
    assert runtime.digest(before) != runtime.digest(after)


@pytest.mark.parametrize(("constant", "version_key"), [
    ("REPRODUCTION_VERSION", "code_reproduction_search"),
    ("PROMPT_VERSION", "code_reproduction_prompt"),
])
def test_report_implementation_and_prompt_versions_invalidate_request(monkeypatch, constant, version_key):
    from app.credibility import code_reproduction_discovery

    before = runtime.request_versions()
    assert before[version_key] == getattr(code_reproduction_discovery, constant)
    monkeypatch.setattr(code_reproduction_discovery, constant, before[version_key] + "-changed")
    after = runtime.request_versions()
    assert after[version_key] != before[version_key]
    assert runtime.digest(before) != runtime.digest(after)


def test_default_output_budget_is_16000_with_configurable_65536_limit(monkeypatch):
    monkeypatch.delenv("CREDIBILITY_CODE_MAX_OUTPUT_TOKENS", raising=False)
    assert Settings(_env_file=None).credibility_code_max_output_tokens == 16000
    configured = Settings(_env_file=None, credibility_code_max_output_tokens=65536)
    assert configured.credibility_code_max_output_tokens == 65536
    for outside in (999, 65537):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, credibility_code_max_output_tokens=outside)
