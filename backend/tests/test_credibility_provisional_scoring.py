"""Offline regressions for the benchmark's provisional credibility scoring policy."""

from __future__ import annotations

import pytest

from app.credibility.context import CheckContext
from app.credibility.item_scoring import score_check_results, score_code_result
from app.credibility.models import (
    ClaimCheckResult,
    CodeCheckResult,
    CredibilityBibliographicMetadata,
    CredibilityClaimChecks,
    CredibilityClaimItem,
    CredibilityCodeChecks,
    CredibilityDimension,
    CredibilityDiscussion,
    CredibilityReferenceChecks,
    CredibilityReferenceItem,
    CredibilityRepository,
    ReferenceCheckResult,
)
from app.credibility.rubric import load_credibility_rubric
from app.credibility.scoring import score_dimensions
from app.credibility.shell import CheckSet, assemble_report
from app.parsing.base import ParsedDocument
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper


def _dimension(dimension_id: str, *, applicability: str = "applicable") -> CredibilityDimension:
    return CredibilityDimension(
        dimension_id=dimension_id,
        applicability=applicability,
        applicability_reason="Offline scoring example.",
        check_status="completed",
        score_status="not_applicable" if applicability == "not_applicable" else "unable_to_assess",
        score=None,
        weight=1,
        score_rule_ids=[],
        summary="The check has completed; scoring has not run.",
        unassessed_reason=None,
        evidence_ids=[],
        finding_ids=[],
    )


def _claims(*outcomes: str, scope_complete: bool = True) -> ClaimCheckResult:
    return ClaimCheckResult(
        applies=True,
        anchors_matched=True,
        dimension=_dimension("claim_evidence"),
        checks=CredibilityClaimChecks(
            selection_scope="Abstract and introduction claims.",
            scope_complete=scope_complete,
            scope_limitations=[] if scope_complete else ["Some introduction blocks were unavailable."],
            items=[
                CredibilityClaimItem(
                    claim_id=f"claim_{index}",
                    statement=f"Claim {index}.",
                    importance="core",
                    conditions="Same dataset and evaluation protocol.",
                    claim_evidence_ids=[],
                    comparison_evidence_ids=[],
                    search_scope="All available results blocks.",
                    outcome=outcome,
                    reason="Validated check outcome supplied to the scorer.",
                    score_rule_id=None,
                    item_score=None,
                )
                for index, outcome in enumerate(outcomes, start=1)
            ],
        ),
        evidence=[],
        findings=[],
    )


def _references(*outcomes: str, partial: bool = False) -> ReferenceCheckResult:
    metadata = CredibilityBibliographicMetadata(
        title="A useful method", authors=["Jane Doe"], year=2024, doi=None, arxiv_id=None,
    )
    counts = {
        outcome: outcomes.count(outcome)
        for outcome in ("matched", "metadata_conflict", "unverified", "not_checked")
    }
    checked_count = len(outcomes) - counts["not_checked"]
    result = ReferenceCheckResult(
        dimension=_dimension("reference_verification"),
        checks=CredibilityReferenceChecks(
            extraction_status="partial" if partial else "complete",
            extraction_reason="Extracted located reference entries.",
            original_total=100 if partial else len(outcomes),
            extracted_count=len(outcomes),
            checked_count=checked_count,
            matched_count=counts["matched"],
            conflict_count=counts["metadata_conflict"],
            unverified_count=counts["unverified"],
            not_checked_count=counts["not_checked"],
            query_completion_ratio=checked_count / len(outcomes) if outcomes else None,
            extraction_coverage_ratio=len(outcomes) / 100 if partial else 1.0 if outcomes else None,
            gaps=["Some entries could not be separated."] if partial else [],
            items=[
                CredibilityReferenceItem(
                    reference_id=f"ref_{index}", raw_text="Jane Doe. A useful method. 2024.",
                    paper_evidence_ids=[], parsed=metadata.model_copy(deep=True), outcome=outcome,
                    reason="Public metadata check outcome.", source_ids=[], candidates=[],
                    selected_candidate_id=None, score_rule_id=None, item_score=None,
                )
                for index, outcome in enumerate(outcomes, start=1)
            ],
        ),
    )
    if partial:
        result.dimension.check_status = "partial"
    return result


def _code(
    *,
    availability: str = "available",
    association: str = "confirmed",
    repository_availability: str = "accessible",
    implementation: bool = True,
    not_applicable: bool = False,
) -> CodeCheckResult:
    repository = CredibilityRepository(
        repository_id="repo_1", source_id="src_repo_1", association=association,
        association_reason="The paper identifies its implementation repository.",
        association_evidence_ids=["ev_paper_link"], availability=repository_availability,
        implementation_evidence_ids=["ev_implementation"] if implementation else [], inspected_revision="abc123",
    )
    dimension = _dimension("code_reproduction", applicability="not_applicable" if not_applicable else "applicable")
    dimension.check_status = "partial"
    return CodeCheckResult(
        dimension=dimension,
        checks=CredibilityCodeChecks(
            scope_complete=False,
            scope_limitations=["Only public sources within the stated search limits were inspected."],
            searches=[],
            code_availability="not_applicable" if not_applicable else availability,
            reproduction_signal="not_applicable" if not_applicable else "unable_to_verify",
            repositories=[repository] if availability == "available" and not not_applicable else [],
            discussions=[], base_score_rule_id=None, score_rule_id=None,
        ),
    )


def _discussion(*, signal: str, eligible: bool, relevance: str = "relevant") -> CredibilityDiscussion:
    return CredibilityDiscussion(
        discussion_id="discussion_1", repository_id="repo_1", source_ids=[], evidence_ids=[],
        signal=signal, relevance=relevance, independence="independent",
        conditions="Same dataset, metrics and evaluation protocol as the paper.",
        resolution="open", author_response_source_ids=[], scoring_eligible=eligible,
        reason="An independently evaluated public reproduction report.",
    )


def _score(claim=None, references=None, code=None, *, version="0.2-provisional-complete", **kwargs):
    return score_check_results(
        claim if claim is not None else _claims("supported"),
        references if references is not None else _references("matched"),
        code if code is not None else _code(),
        rubric=load_credibility_rubric(version),
        **kwargs,
    )


@pytest.mark.parametrize("kwargs,expected", [
    ({}, 7),
    ({"availability": "not_found_in_scope"}, 4),
    ({"availability": "unable_to_verify"}, 3),
    ({"not_applicable": True}, None),
])
def test_code_only_acceptance_uses_existing_policy_without_mutating_evidence(kwargs, expected):
    original = _code(**kwargs)
    before = original.model_dump(mode="json")
    result = score_code_result(original, rubric=load_credibility_rubric())
    assert result.dimension.score == expected
    assert result.model_dump(mode="json") == _score(code=original)[2].model_dump(mode="json")
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("supported", 10), ("partially_supported", 7), ("not_found", 4),
     ("contradicted", 2), ("unable_to_determine", 3), ("not_checked", 3)],
)
def test_provisional_claim_mapping_consumes_final_check_outcomes(outcome, expected):
    claim, _, _ = _score(claim=_claims(outcome))
    assert claim.checks.items[0].item_score == expected
    assert claim.checks.items[0].score_rule_id
    assert claim.dimension.score == expected
    assert claim.dimension.score_status == "scored"
    assert claim.dimension.score_rule_ids


def test_claim_dimension_uses_the_worst_item_instead_of_averaging():
    claim, _, _ = _score(claim=_claims("supported", "partially_supported", "contradicted"))
    assert [item.item_score for item in claim.checks.items] == [10, 7, 2]
    assert claim.dimension.score == 2


@pytest.mark.parametrize(("scope_complete", "parse_usable"), [(False, True), (True, False), (False, False)])
def test_incomplete_claim_scope_or_unusable_parse_contributes_the_three_point_guard(scope_complete, parse_usable):
    original = _claims("supported", scope_complete=scope_complete)
    claim, _, _ = _score(claim=original, paper_parse_usable=parse_usable)
    assert claim.checks.items[0].item_score == 10
    assert claim.dimension.score == 3
    assert claim.checks.scope_complete is scope_complete
    assert claim.dimension.check_status == original.dimension.check_status


def test_partial_reference_extraction_scores_known_items_without_inventing_missing_entries():
    original = _references("matched", "metadata_conflict", "unverified", "not_checked", partial=True)
    _, references, _ = _score(references=original)
    assert [item.item_score for item in references.checks.items] == [10, 7, 4, 3]
    assert references.dimension.score == 6
    assert all(item.score_rule_id for item in references.checks.items)
    assert references.checks.extraction_status == "partial"
    assert references.dimension.check_status == "partial"
    assert references.checks.original_total == 100
    assert references.checks.extracted_count == 4
    assert references.checks.extraction_coverage_ratio == 0.04
    assert references.checks.gaps == original.checks.gaps


def test_empty_references_receive_the_provisional_unknown_score():
    _, references, _ = _score(references=_references())
    assert references.dimension.score == 3
    assert references.checks.items == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [({}, 7), ({"availability": "not_found_in_scope"}, 4),
     ({"availability": "unable_to_verify"}, 3), ({"association": "uncertain"}, 3),
     ({"repository_availability": "inaccessible"}, 3), ({"implementation": False}, 3)],
)
def test_code_base_score_requires_confirmed_accessible_implementation(kwargs, expected):
    _, _, code = _score(code=_code(**kwargs))
    assert code.dimension.score == expected
    assert code.checks.base_score_rule_id
    assert code.dimension.check_status == "partial"
    assert code.checks.scope_complete is False


@pytest.mark.parametrize(
    ("signal", "eligible", "relevance", "expected"),
    [("failure_reported", True, "relevant", 3),
     ("failure_reported", False, "relevant", 7),
     ("failure_reported", True, "irrelevant", 7),
     ("success_reported", True, "relevant", 7)],
)
def test_only_eligible_relevant_feedback_affects_the_code_minimum(signal, eligible, relevance, expected):
    original = _code()
    original.checks.reproduction_signal = signal
    original.checks.discussions = [_discussion(signal=signal, eligible=eligible, relevance=relevance)]
    _, _, code = _score(code=original)
    assert code.dimension.score == expected
    assert code.checks.discussions[0].scoring_eligible is eligible


def test_eligible_failure_takes_priority_when_success_and_failure_reports_coexist():
    original = _code()
    success = _discussion(signal="success_reported", eligible=True)
    failure = _discussion(signal="failure_reported", eligible=True)
    failure.discussion_id = "discussion_2"
    original.checks.discussions = [success, failure]
    _, _, code = _score(code=original)
    assert code.dimension.score == 3


def test_resolved_eligible_failure_still_uses_the_delivered_failure_score():
    original = _code()
    discussion = _discussion(signal="failure_reported", eligible=True)
    discussion.resolution = "resolved"
    original.checks.discussions = [discussion]
    _, _, code = _score(code=original)
    assert code.dimension.score == 3
    assert code.checks.discussions[0].resolution == "resolved"


@pytest.mark.parametrize("repository_state", ["uncertain", "missing"])
def test_feedback_for_an_unconfirmed_or_missing_repository_does_not_penalize_confirmed_code(repository_state):
    original = _code()
    discussion = _discussion(signal="failure_reported", eligible=True)
    discussion.repository_id = "repo_other"
    if repository_state == "uncertain":
        other_repository = original.checks.repositories[0].model_copy(deep=True)
        other_repository.repository_id = "repo_other"
        other_repository.association = "uncertain"
        original.checks.repositories.append(other_repository)
    original.checks.discussions = [discussion]
    _, _, code = _score(code=original)
    assert code.dimension.score == 7
    assert code.checks.discussions[0].repository_id == "repo_other"


def test_not_applicable_code_is_excluded_from_the_total():
    rubric = load_credibility_rubric("0.2-provisional-complete")
    results = _score(
        claim=_claims("partially_supported"), references=_references("matched"), code=_code(not_applicable=True),
    )
    assert results[2].dimension.score is None
    assert results[2].dimension.score_status == "not_applicable"
    scored = score_dimensions([result.dimension for result in results], rubric=rubric)
    assert scored.overall.score == 8.5
    assert scored.overall.calculation.excluded_dimension_ids == ["code_reproduction"]
    assert scored.overall.calculation.assessed_dimension_count == 2


def test_provisional_total_is_available_without_rewriting_incomplete_check_scope():
    rubric = load_credibility_rubric("0.2-provisional-complete")
    results = _score(claim=_claims("supported", scope_complete=False), references=_references("matched"))
    scored = score_dimensions([result.dimension for result in results], rubric=rubric)
    assert scored.overall.score == pytest.approx((3 + 10 + 7) / 3)
    assert results[0].checks.scope_complete is False
    assert results[2].checks.scope_complete is False
    assert results[2].dimension.check_status == "partial"


@pytest.mark.parametrize(
    ("outcome", "parse_quality"),
    [("unable_to_determine", ParseQuality.GOOD), ("supported", ParseQuality.PARTIAL)],
)
def test_report_scope_stays_incomplete_for_unresolved_claims_or_partial_parse(tmp_path, outcome, parse_quality):
    claim = _claims(outcome, scope_complete=True)
    references = _references("matched")
    code = _code(not_applicable=True)
    ctx = CheckContext(
        source={}, repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None,
        doc=ParsedDocument(
            paper=Paper(paper_id="scope_example", parse_quality=parse_quality),
            parse_quality=parse_quality,
        ),
        unresolved_reason=None,
    )
    report, _ = assemble_report(
        ctx, report_id="scope_report", rubric=load_credibility_rubric("0.2-provisional-complete"),
        checks=CheckSet(claims=lambda ctx: claim, references=lambda ctx: references, code=lambda ctx: code),
    )
    assert report.scope_complete is False
    assert report.checks.claims.scope_complete is True
    assert claim.checks.scope_complete is True
    assert report.checks.claims.items[0].outcome == outcome
    assert report.dimensions[0].score == 3
    assert report.overall.score == 6.5
    assert report.is_provisional is True


@pytest.mark.parametrize(
    ("score", "category"),
    [(0, "critical_concerns"), (3, "significant_concerns"),
     (6, "some_concerns"), (8, "no_significant_concerns")],
)
def test_provisional_categories_include_their_lower_boundary(score, category):
    rubric = load_credibility_rubric("0.2-provisional-complete")
    dimensions = [_dimension(dimension_id) for dimension_id in (
        "claim_evidence", "reference_verification", "code_reproduction",
    )]
    for dimension in dimensions:
        dimension.score = score
        dimension.score_status = "scored"
    assert score_dimensions(dimensions, rubric=rubric).overall.category == category


def test_total_and_category_use_the_unrounded_applicable_dimension_average():
    rubric = load_credibility_rubric("0.2-provisional-complete")
    dimensions = [_dimension(dimension_id) for dimension_id in (
        "claim_evidence", "reference_verification", "code_reproduction",
    )]
    for dimension, score in zip(dimensions, (8, 8, 7.999), strict=True):
        dimension.score = score
        dimension.score_status = "scored"
    scored = score_dimensions(dimensions, rubric=rubric)
    assert scored.overall.score == pytest.approx((8 + 8 + 7.999) / 3)
    assert scored.overall.score < 8
    assert scored.overall.category == "some_concerns"


def test_draft_rubric_keeps_item_dimension_and_overall_scores_empty():
    rubric = load_credibility_rubric("0.1-draft")
    assert rubric.status == "draft"
    results = _score(version="0.1-draft")
    for result in results:
        assert result.dimension.score is None
    assert results[0].checks.items[0].item_score is None
    assert results[1].checks.items[0].item_score is None
    assert results[2].checks.base_score_rule_id is None
    overall = score_dimensions([result.dimension for result in results], rubric=rubric).overall
    assert overall.score is None and overall.category is None


def test_scoring_deep_copies_check_results_and_does_not_change_the_inputs():
    originals = (_claims("supported"), _references("matched"), _code())
    snapshots = [result.model_dump(mode="json") for result in originals]
    scored = _score(*originals)
    assert [result.model_dump(mode="json") for result in originals] == snapshots
    assert all(result is not original for result, original in zip(scored, originals, strict=True))
    scored[0].checks.items[0].statement = "Changed only in the scored copy."
    scored[1].checks.items[0].parsed.authors.append("Another Author")
    scored[2].checks.repositories[0].implementation_evidence_ids.append("ev_new")
    assert [result.model_dump(mode="json") for result in originals] == snapshots
