"""Discovery has its own evidence contract and cannot directly change trial scores."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.credibility.code_checks import check_code
from app.credibility.context import CheckContext
from app.credibility.item_scoring import score_code_result
from app.credibility.models import (
    CodeCheckResult,
    CredibilityCodeDiscovery,
    CredibilityCodeDiscoveryBudget,
    CredibilityCodeDiscoveryCandidate,
)
from app.credibility.rubric import load_credibility_rubric
from app.parsing.grobid import StubParser


def _context(tmp_path):
    return CheckContext(
        source={"type": "pdf", "arxiv_id": None, "upload_id": "fixture"},
        repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None,
        doc=StubParser._fake(paper_id="discovery_contract", title="Offline evidence contract fixture"),
        unresolved_reason=None,
    )


def test_old_code_checkpoint_defaults_to_not_run_without_searching(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Reading an old checkpoint cannot search or call a model")

    monkeypatch.setattr("app.credibility.code_checks.LOOKUP.get", forbidden)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", forbidden)
    old = check_code(_context(tmp_path)).model_dump(mode="json")
    old.pop("discovery")
    restored = CodeCheckResult.model_validate(old)
    assert restored.discovery == CredibilityCodeDiscovery()
    assert restored.discovery.status == "not_run"
    assert restored.discovery.queries == restored.discovery.candidates == []
    assert restored.discovery.budget == CredibilityCodeDiscoveryBudget()
    assert restored.discovery.audit_ref is None


def test_official_claim_remains_candidate_until_actual_repository_proof_is_available():
    candidate = CredibilityCodeDiscoveryCandidate(
        candidate_id="candidate_1", url="https://github.com/example/paper",
        resource_kind="official_implementation", source_ids=["fetched_project_page"],
    )
    assert candidate.status == "candidate" and candidate.repository_id is None
    with pytest.raises(ValidationError, match="actual source/evidence IDs"):
        CredibilityCodeDiscoveryCandidate.model_validate({**candidate.model_dump(), "status": "verified"})
    verified = CredibilityCodeDiscoveryCandidate.model_validate({
        **candidate.model_dump(), "status": "verified", "repository_id": "code_repo_1",
        "source_ids": ["readme_at_commit"], "evidence_ids": ["actual_readme_quote"],
    })
    assert verified.status == "verified"


@pytest.mark.parametrize("kind", [
    "implementation_candidate", "third_party_reproduction", "third_party_related", "reading_list", "undetermined",
])
def test_nonofficial_search_resources_do_not_become_confirmed_repositories_or_scoring_inputs(tmp_path, kind):
    result = check_code(_context(tmp_path))
    rubric = load_credibility_rubric("0.2-provisional-complete")
    assert rubric.content_sha256 == "a30c99a871c0b98c861d7d90c48055d765829c0f015325340357be0a48752c40"
    before = score_code_result(result, rubric=rubric)
    result.discovery = CredibilityCodeDiscovery(
        status="completed", reason="One bounded discovery result.",
        candidates=[CredibilityCodeDiscoveryCandidate(
            candidate_id="candidate_1", url="https://example.org/resource", resource_kind=kind,
        )],
    )
    after = score_code_result(result, rubric=rubric)
    assert after.checks == before.checks
    assert after.dimension == before.dimension
    assert after.checks.repositories == []
    assert after.discovery.candidates[0].status == "candidate"
    assert after.discovery.candidates[0].repository_id is None


@pytest.mark.parametrize("field", list(CredibilityCodeDiscoveryBudget.model_fields))
def test_negative_discovery_budget_values_are_invalid(field):
    with pytest.raises(ValidationError):
        CredibilityCodeDiscoveryBudget.model_validate({field: -1})


def test_bounded_search_cannot_claim_exhaustive_coverage_or_add_scoring_permissions():
    with pytest.raises(ValidationError):
        CredibilityCodeDiscovery(scope_complete=True)
    with pytest.raises(ValidationError):
        CredibilityCodeDiscoveryCandidate.model_validate({
            "candidate_id": "candidate_1", "url": "https://example.org/resource", "scoring_eligible": True,
        })
