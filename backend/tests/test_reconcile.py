"""Code-release reconciliation must never validate unrelated artifact or result claims."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.credibility import runtime
from app.credibility.context import CheckContext
from app.credibility.item_scoring import score_check_results
from app.credibility.locate import assert_report_links
from app.credibility.models import (
    ClaimCheckResult,
    CodeCheckResult,
    CredibilityAssessmentResponse,
    CredibilityClaimChecks,
    CredibilityClaimItem,
    CredibilityCodeChecks,
    CredibilityDimension,
    CredibilityEvidence,
    CredibilityExternalSource,
    CredibilityFinding,
    CredibilityPaperLocator,
    CredibilityReferenceChecks,
    CredibilityRepository,
    ReferenceCheckResult,
)
from app.credibility.reconcile import SUPPORTED_BY_CODE_RULE, reconcile_artifact_claims
from app.credibility.rubric import load_credibility_rubric
from app.credibility.shell import PIPELINE_VERSION, CheckSet, assemble_report
from app.main import app
from app.parsing.base import ParsedDocument
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

_REPO_URL = "https://github.com/example/paper"
_STATEMENT = f"Our code is publicly available at {_REPO_URL}."


def _dimension(name: str) -> CredibilityDimension:
    return CredibilityDimension(
        dimension_id=name, applicability="applicable", applicability_reason="Checkable paper.",
        check_status="completed", score_status="unable_to_assess", score=None, weight=1,
        score_rule_ids=[], summary="Checked.", unassessed_reason=None, evidence_ids=[], finding_ids=[],
    )


def _claims(statement: str = _STATEMENT, *, outcome: str = "unable_to_determine", quote=None) -> ClaimCheckResult:
    evidence = CredibilityEvidence(
        evidence_id="claim_quote", origin="paper", mode="quote", excerpt=quote or statement,
        paper_locator=CredibilityPaperLocator(
            page_start=1, page_end=1, chunk_id="abstract", section_title="Abstract", element_label=None,
        ), source_id=None, location_confidence="high",
    )
    item = CredibilityClaimItem(
        claim_id="C1", statement=statement, importance="supporting", conditions="not stated",
        claim_evidence_ids=[evidence.evidence_id], comparison_evidence_ids=[], search_scope="Later body.",
        outcome=outcome, reason="The later body does not establish this release.",
        score_rule_id="P_C_UNVERIFIABLE", item_score=3,
    )
    finding = CredibilityFinding(
        finding_id="find_C1", dimension_id="claim_evidence", item_ids=["C1"],
        kind="concern" if outcome == "not_found" else "verification_gap", severity="info",
        certainty="unresolved", title="Release could not be verified", detail=item.reason,
        evidence_ids=[evidence.evidence_id], source_ids=[], limitations=["Paper-internal evidence only."],
    )
    dimension = _dimension("claim_evidence")
    dimension.evidence_ids = [evidence.evidence_id]
    dimension.finding_ids = [finding.finding_id]
    return ClaimCheckResult(
        applies=True, anchors_matched=True, dimension=dimension,
        checks=CredibilityClaimChecks(
            selection_scope="Abstract and introduction.", scope_complete=True, scope_limitations=[], items=[item],
        ), evidence=[evidence], findings=[finding],
    )


def _code(*, mode="paraphrase") -> CodeCheckResult:
    source = CredibilityExternalSource(
        source_id="repo_source", kind="repository", url="https://api.github.com/repos/example/paper",
        fetch_status="ok", http_status=200, retrieved_at="2026-10-20T00:00:00Z",
        observed_at="2026-10-20T00:00:00Z", is_cached=True, snapshot_ref="snapshot:test",
        association_reason="The repository identifies the paper.",
    )
    evidence = [
        CredibilityEvidence(
            evidence_id=eid, origin="external", mode=mode, excerpt=text,
            paper_locator=None, source_id=source.source_id, location_confidence="high",
        ) for eid, text in [("association", "Official paper repository."), ("implementation", "src/model.py")]
    ]
    return CodeCheckResult(
        dimension=_dimension("code_reproduction"),
        checks=CredibilityCodeChecks(
            scope_complete=True, scope_limitations=[], searches=[], code_availability="available",
            reproduction_signal="no_feedback_found", repositories=[CredibilityRepository(
                repository_id="code_repo_1", source_id=source.source_id,
                association="confirmed", association_reason="Paper identifies this implementation.",
                association_evidence_ids=["association"], availability="accessible",
                implementation_evidence_ids=["implementation"], inspected_revision="abc123",
            )], discussions=[], base_score_rule_id=None, score_rule_id=None,
        ), evidence=evidence, sources=[source],
    )


def _references() -> ReferenceCheckResult:
    return ReferenceCheckResult(
        dimension=_dimension("reference_verification"),
        checks=CredibilityReferenceChecks(
            extraction_status="complete", extraction_reason="No references.", original_total=0,
            extracted_count=0, checked_count=0, matched_count=0, conflict_count=0, unverified_count=0,
            not_checked_count=0, query_completion_ratio=None, extraction_coverage_ratio=None, gaps=[], items=[],
        ),
    )


def _context() -> CheckContext:
    paper = Paper(
        paper_id="release_paper", title="A paper", page_count=1,
        parse_quality=ParseQuality.GOOD,
    )
    return CheckContext(
        source={"type": "pdf", "arxiv_id": None, "upload_id": "test_upload"},
        repo_root=Path(__file__).resolve().parents[1], ref=None, unresolved_reason=None,
        doc=ParsedDocument(paper=paper, parse_quality=ParseQuality.GOOD),
        generated_at="2026-10-20T00:00:00Z",
    )


@pytest.mark.parametrize("outcome", ["unable_to_determine", "not_found", "partially_supported"])
@pytest.mark.parametrize("mode", ["quote", "paraphrase"])
def test_code_release_uses_actual_repository_evidence_and_preserves_original_audit(outcome, mode):
    claim, code = _claims(outcome=outcome), _code(mode=mode)
    original, original_code = claim.model_dump(), code.model_dump()
    result = reconcile_artifact_claims(claim, code)
    item = result.checks.items[0]
    assert item.outcome == "supported"
    assert item.comparison_evidence_ids == ["association", "implementation"]
    assert item.score_rule_id == SUPPORTED_BY_CODE_RULE
    assert item.item_score is None
    assert item.reason.startswith("[verified by code dimension: code_repo_1]")
    assert item.reconciled_from.outcome == outcome
    assert item.reconciled_from.reason == claim.checks.items[0].reason
    assert item.reconciled_from.item_score == 3
    assert item.reconciled_from.findings[0].model_dump() == original["findings"][0]
    assert item.reconciled_from.repository_ids == ["code_repo_1"]
    assert result.findings[0].kind == "context"
    assert result.findings[0].source_ids == ["repo_source"]
    assert result.dimension.evidence_ids == ["claim_quote", "association", "implementation"]
    assert reconcile_artifact_claims(result, code).model_dump() == result.model_dump()
    assert claim.model_dump() == original
    assert code.model_dump() == original_code


@pytest.mark.parametrize(
    "artifact", ["data", "datasets", "checkpoints", "pre-trained checkpoints", "weights", "models"],
)
def test_code_plus_other_artifacts_only_partially_supported(artifact):
    statement = f"Model code and {artifact} are open-sourced at the stated GitHub URL."
    result = reconcile_artifact_claims(_claims(statement, quote=f"{statement} {_REPO_URL}"), _code())
    item = result.checks.items[0]
    assert item.outcome == "partially_supported"
    assert "have not been verified" in item.reason
    assert item.score_rule_id == "P_C_PARTIALLY_SUPPORTED"


@pytest.mark.parametrize("statement", [
    "Our code is open source.", "The implementation is publicly available.",
    "We release our code at a public repository.",
    "The paper releases code and data for its study at a public repository.",
    "We make our code publicly available on GitHub.",
    "Code and data: https://github.com/example/paper.",
])
def test_common_release_assertions_are_recognized(statement):
    result = reconcile_artifact_claims(_claims(statement), _code())
    assert result.checks.items[0].reconciled_from is not None


@pytest.mark.parametrize("modifier", ["open-source", "as open source", "publicly"])
@pytest.mark.parametrize("artifacts,expected", [
    ("model code and pre-trained checkpoints", "partially_supported"), ("model code", "supported"),
])
def test_release_verb_with_publication_modifier_covers_actual_mamba_statement(modifier, artifacts, expected):
    statement = f"The paper releases {artifacts} {modifier} at {_REPO_URL}."
    result = reconcile_artifact_claims(_claims(statement), _code())
    assert result.checks.items[0].outcome == expected
    assert result.checks.items[0].reconciled_from is not None
    assert result.checks.items[0].score_rule_id == (
        "P_C_PARTIALLY_SUPPORTED" if expected == "partially_supported" else SUPPORTED_BY_CODE_RULE
    )


def test_generic_artifact_statement_can_use_a_precise_code_release_quote():
    result = reconcile_artifact_claims(
        _claims("Our artifacts are publicly available.", quote="Our code and checkpoints are publicly available."),
        _code(),
    )
    assert result.checks.items[0].outcome == "partially_supported"


@pytest.mark.parametrize("artifact", ["data", "checkpoints", "weights", "models"])
def test_generic_artifact_fallback_preserves_additional_artifacts_in_the_statement(artifact):
    result = reconcile_artifact_claims(
        _claims(f"Our artifacts and {artifact} are publicly available.", quote="Our code is publicly available."),
        _code(),
    )
    assert result.checks.items[0].outcome == "partially_supported"
    assert result.checks.items[0].score_rule_id == "P_C_PARTIALLY_SUPPORTED"


@pytest.mark.parametrize("statement", [
    "The model is publicly available on GitHub.",
    "Our weights are publicly available.",
    "The dataset is available at a public repository.",
    "Our code is publicly available and outperforms the baseline.",
    "Our efficient implementation is publicly available on GitHub.",
    "The code reproduces all reported results and is publicly available.",
    "The complete code is publicly available.",
    "Our code is publicly available under the MIT license.",
    "Our code will be publicly available on GitHub.",
    "We plan to release our code.",
    "Our code is not publicly available.",
    "If accepted, our code is publicly available on GitHub.",
    "Our code was previously publicly available on GitHub.",
    "Our method achieves higher accuracy; code is publicly available.",
    "Our implementation is publicly available with 3x faster inference.",
])
def test_model_data_performance_licensing_negative_future_and_conditional_claims_unchanged(statement):
    claim = _claims(statement)
    assert reconcile_artifact_claims(claim, _code()).model_dump() == claim.model_dump()


@pytest.mark.parametrize("outcome", ["contradicted", "supported", "not_checked"])
def test_existing_supported_contradicted_and_unchecked_claims_unchanged(outcome):
    claim = _claims(outcome=outcome)
    assert reconcile_artifact_claims(claim, _code()).model_dump() == claim.model_dump()


@pytest.mark.parametrize("field,value", [
    ("association", "uncertain"), ("association", "rejected"), ("availability", "inaccessible"),
    ("availability", "unknown"), ("implementation_evidence_ids", []), ("association_evidence_ids", []),
    ("implementation_evidence_ids", ["nonexistent"]),
])
def test_unconfirmed_or_unproven_repositories_do_not_modify_claims(field, value):
    claim, code = _claims(), _code()
    setattr(code.checks.repositories[0], field, value)
    assert reconcile_artifact_claims(claim, code).model_dump() == claim.model_dump()


@pytest.mark.parametrize("change", ["source_missing", "source_failed", "external_evidence_source_missing"])
def test_external_evidence_must_have_successful_sources(change):
    claim, code = _claims(), _code()
    if change == "source_missing":
        code.sources = []
    elif change == "source_failed":
        code.sources[0].fetch_status = "timeout"
    else:
        code.evidence[0].source_id = "unknown_source"
    assert reconcile_artifact_claims(claim, code).model_dump() == claim.model_dump()


def test_paper_association_evidence_and_external_implementation_paraphrases_both_work():
    claim, code = _claims(), _code()
    code.evidence[0] = claim.evidence[0].model_copy(deep=True)
    code.evidence[0].evidence_id = "association"
    result = reconcile_artifact_claims(claim, code)
    assert result.checks.items[0].outcome == "supported"


@pytest.mark.parametrize("location", [
    "https://github.com/other/repository", "https://gitlab.com/example/paper",
    "https://github.com/example/paper/tree/unverified-branch", "https://[malformed-host]",
    f"{_REPO_URL} and https://github.com/example/unverified",
])
def test_an_explicit_different_or_additional_unverified_url_is_not_reconciled(location):
    claim = _claims(f"Our code is publicly available at {location}.")
    assert reconcile_artifact_claims(claim, _code()).model_dump() == claim.model_dump()


def test_quote_url_must_also_agree_with_repository():
    claim = _claims("Our code is publicly available.", quote="Code: https://github.com/other/repository.")
    assert reconcile_artifact_claims(claim, _code()).model_dump() == claim.model_dump()


def test_future_or_negative_source_quote_cannot_be_upgraded_by_current_statement():
    claim = _claims(quote="Our code will be publicly available upon publication.")
    assert reconcile_artifact_claims(claim, _code()).model_dump() == claim.model_dump()


def test_invalid_claim_anchor_is_not_rescued_by_repository():
    claim = _claims()
    claim.checks.items[0].claim_evidence_ids = ["missing"]
    assert reconcile_artifact_claims(claim, _code()).model_dump() == claim.model_dump()


@pytest.mark.parametrize("statement,score,rule", [
    (_STATEMENT, 10, SUPPORTED_BY_CODE_RULE),
    ("Code and checkpoints are publicly available.", 7, "P_C_PARTIALLY_SUPPORTED"),
])
def test_reconciled_outcomes_use_existing_scores_and_specific_supported_rule(statement, score, rule):
    code = _code()
    claim = reconcile_artifact_claims(_claims(statement), code)
    scored, _, _ = score_check_results(
        claim, _references(), code, rubric=load_credibility_rubric("0.2-provisional-complete"),
    )
    assert scored.dimension.score == score
    assert scored.checks.items[0].score_rule_id == rule
    assert scored.dimension.score_rule_ids == [rule]
    assert scored.checks.items[0].reconciled_from.item_score == 3


def test_reconciliation_preserves_incomplete_scope_fallback():
    claim, code = _claims(), _code()
    claim.checks.scope_complete = False
    claim = reconcile_artifact_claims(claim, code)
    scored, _, _ = score_check_results(
        claim, _references(), code, rubric=load_credibility_rubric("0.2-provisional-complete"),
    )
    assert scored.dimension.score == 3
    assert "P_C_SCOPE_FALLBACK" in scored.dimension.score_rule_ids


def _assembled_report():
    claim, code, references = _claims(), _code(), _references()
    return assemble_report(
        _context(), report_id="test_report", rubric=load_credibility_rubric("0.2-provisional-complete"),
        checks=CheckSet(claims=lambda _ctx: claim, references=lambda _ctx: references, code=lambda _ctx: code),
    )[0]


def test_shell_reconciles_before_scoring_and_keeps_all_evidence_links_closed():
    report = _assembled_report()
    assert_report_links(report)
    assert report.checks.claims.items[0].outcome == "supported"
    assert report.dimensions[0].score == 10
    assert report.versions.pipeline_version == PIPELINE_VERSION == "credibility-0.13-model-release-labels"
    assert {row.evidence_id for row in report.evidence} == {"claim_quote", "association", "implementation"}


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_audit_is_exposed_only_in_v5_while_older_api_shapes_are_preserved(version, monkeypatch):
    report = _assembled_report()
    response = TypeAdapter(CredibilityAssessmentResponse).validate_python({
        "schema_version": f"credibility-0.{version}.0-draft", "analysis_mode": "credibility",
        "assessment_id": "test", "data_origin": "live", "source": {
            "type": "pdf", "arxiv_id": None, "upload_id": "test_upload",
        }, "model_id": "not_called", "status": "completed", "stage": "done", "progress_percent": 100,
        "created_at": "2026-10-20T00:00:00Z", "started_at": "2026-10-20T00:00:00Z",
        "completed_at": "2026-10-20T00:00:00Z", "paper": None, "report": report.model_dump(mode="json"),
        "warnings": [], "error": None,
    })
    item = response.model_dump(mode="json")["report"]["checks"]["claims"]["items"][0]
    assert ("reconciled_from" in item) == (version == 5)
    if version == 5:
        assert item["reconciled_from"]["outcome"] == "unable_to_determine"
    assert response.report.checks.claims.items[0].reconciled_from is not None
    # HTTP uses the same projection and must retain the audit in stored state.
    stored = {**response.model_dump(mode="json"), "report": report.model_dump(mode="json")}
    monkeypatch.setattr(runtime.get_task_store(), "load_task", lambda _assessment_id: stored)
    fetched = TestClient(app).get("/v1/credibility/assessments/test")
    assert fetched.status_code == 200
    assert fetched.json() == response.model_dump(mode="json")
    assert stored["report"]["checks"]["claims"]["items"][0]["reconciled_from"] is not None


def test_old_claim_items_without_reconciliation_audit_remain_valid():
    payload = _claims().checks.items[0].model_dump()
    payload.pop("reconciled_from")
    assert CredibilityClaimItem.model_validate(payload).reconciled_from is None


@pytest.mark.parametrize("broken_link", ["repository", "evidence", "finding_evidence", "finding_source"])
def test_audit_links_are_validated_without_weakening_current_report_link_validation(broken_link):
    report = _assembled_report()
    audit = report.checks.claims.items[0].reconciled_from
    if broken_link == "repository":
        audit.repository_ids = ["unknown_repo"]
    elif broken_link == "evidence":
        audit.comparison_evidence_ids = ["unknown_evidence"]
    elif broken_link == "finding_evidence":
        audit.findings[0].evidence_ids = ["unknown_evidence"]
    else:
        audit.findings[0].source_ids = ["unknown_source"]
    with pytest.raises(ValueError, match="claim audit"):
        assert_report_links(report)
