"""The public ratio describes extracted items without changing trial scoring."""
from __future__ import annotations

import pytest

from app.credibility.coverage import build_reference_summary
from app.credibility.models import CredibilityReferenceChecks, CredibilityReferenceItem


def _item(index, outcome, reason):
    return CredibilityReferenceItem(
        reference_id=f"ref_{index:03d}", raw_text=f"Reference {index}", paper_evidence_ids=[],
        parsed={"title": None, "authors": [], "year": None, "doi": None, "arxiv_id": None},
        outcome=outcome, reason=reason, source_ids=[f"src_{index:03d}"], candidates=[],
        selected_candidate_id=None, score_rule_id=None, item_score=None,
    )


def _checks(items=(), *, extraction_status="complete", gaps=()):
    items = list(items)
    counts = {outcome: sum(item.outcome == outcome for item in items)
              for outcome in ("matched", "unverified", "metadata_conflict", "not_checked")}
    count = len(items)
    return CredibilityReferenceChecks(
        extraction_status=extraction_status, extraction_reason="Fixture", original_total=count,
        extracted_count=count, checked_count=count - counts["not_checked"], matched_count=counts["matched"],
        conflict_count=counts["metadata_conflict"], unverified_count=counts["unverified"],
        not_checked_count=counts["not_checked"], query_completion_ratio=None,
        extraction_coverage_ratio=None, gaps=list(gaps), items=items,
    )


def test_rate_includes_all_extracted_items_and_preserves_order_reasons_and_source_links():
    checks = _checks([
        _item(1, "matched", "Identity confirmed"),
        _item(2, "unverified", "[source_fetch_failed] Official source timed out."),
        _item(3, "metadata_conflict", "[year_mismatch] Cited 2020; official version is 2021."),
        _item(4, "not_checked", "[insufficient_metadata] Missing identifying fields."),
    ])
    before = checks.model_dump(mode="json")
    summary = build_reference_summary(checks)
    assert summary.verification_rate == 1 / 4
    assert summary.denominator == "extracted_references"
    assert (summary.extracted_count, summary.matched_count, summary.unverified_count,
            summary.metadata_conflict_count, summary.not_checked_count) == (4, 1, 1, 1, 1)
    assert summary.extraction_complete is True
    assert [item.reference_id for item in summary.unresolved_items] == ["ref_002", "ref_003", "ref_004"]
    assert [item.reason_code for item in summary.unresolved_items] == [
        "source_fetch_failed", "year_mismatch", "insufficient_metadata",
    ]
    for actual, expected in zip(summary.unresolved_items, checks.items[1:], strict=True):
        assert actual.reason == expected.reason and actual.source_ids == expected.source_ids
    assert checks.model_dump(mode="json") == before


@pytest.mark.parametrize("status", ["complete", "partial", "failed", "not_applicable"])
def test_zero_denominator_is_null_even_when_extraction_is_complete(status):
    summary = build_reference_summary(_checks(extraction_status=status))
    assert summary.verification_rate is None
    assert summary.unresolved_items == []
    assert summary.extraction_complete == (status == "complete")


@pytest.mark.parametrize("status,gaps", [("partial", ()), ("failed", ()), ("complete", ("page missing",))])
def test_partial_or_gapped_extraction_never_claims_full_coverage(status, gaps):
    summary = build_reference_summary(_checks([_item(1, "matched", "confirmed")], extraction_status=status, gaps=gaps))
    assert summary.verification_rate == 1.0
    assert summary.extraction_complete is False


@pytest.mark.parametrize("outcome,reason,expected", [
    ("unverified", "No good candidate was found.", "undetermined_reason"),
    ("unverified", "[unknown_provider_error] failure", "undetermined_reason"),
    ("unverified", "[year_mismatch missing closing bracket", "undetermined_reason"),
    ("metadata_conflict", "Metadata differs.", "metadata_conflict"),
    ("not_checked", "Not checked.", "not_checked"),
])
def test_legacy_and_unknown_reasons_are_not_guessed_from_free_text(outcome, reason, expected):
    item = build_reference_summary(_checks([_item(1, outcome, reason)])).unresolved_items[0]
    assert item.reason_code == expected and item.reason == reason


@pytest.mark.parametrize("reason_code", [
    "target_not_found_in_scope", "year_mismatch", "author_mismatch", "version_evidence_missing",
    "source_fetch_failed", "insufficient_metadata", "personal_communication", "extraction_gap",
    "metadata_conflict", "budget_exhausted", "identifier_mismatch", "invalid_arxiv_id", "title_mismatch",
    "not_checked", "undetermined_reason",
])
def test_reason_codes_are_accepted_without_reinterpreting_the_explanation(reason_code):
    reason = f"[{reason_code}] Original explanation."
    summary = build_reference_summary(_checks([_item(1, "unverified", reason)]))
    assert summary.unresolved_items[0].reason_code == reason_code
    assert summary.unresolved_items[0].reason == reason
