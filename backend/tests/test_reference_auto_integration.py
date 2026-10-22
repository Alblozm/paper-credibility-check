"""Automatic source recovery, scoring and observability use the production path."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.credibility import reference_arxiv, reference_publishers, references
from app.credibility.context import CheckContext
from app.credibility.coverage import build_reference_summary
from app.credibility.item_scoring import score_reference_result
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_sources import ReferenceConfirmation, ReferenceFallback, ReferenceObservation
from app.credibility.rubric import load_credibility_rubric
from app.credibility.snapshots import SnapshotError, capture_snapshot, read_snapshot, verify_report_snapshots
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

STAMP = "2026-10-19T01:02:03Z"
TITLE = "A reliable reference with independent source evidence"


def context(raw=None):
    return CheckContext(source={}, repo_root=Path("."), generated_at=STAMP, ref=None, unresolved_reason=None,
        doc=ParsedDocument(paper=Paper(paper_id="auto-reference", parse_quality=ParseQuality.GOOD), blocks=[TextBlock(
            text=raw or f"Jane Doe and John Smith. {TITLE}. 2020.", chunk_id="references-1", page_start=1,
            page_end=1, section_role="references", section_title="References",
        )]))


def lookup(monkeypatch, *, status="ok", candidates=()):
    ref = capture_snapshot(kind="crossref_response", raw=b'{"message":{"items":[]}}',
        normalized_json={"message": {"items": []}}, fetch_status=status, http_status=200 if status == "ok" else None,
        retrieved_at=STAMP, requested_url="https://api.crossref.org/works?query=test")
    response = references._LookupResult("https://api.crossref.org/works?query=test", status,
        200 if status == "ok" else None, STAMP, tuple(candidates), False, ref)
    real = references.CrossrefLookup()
    monkeypatch.setattr(real, "lookup", lambda *args, **kwargs: response)
    monkeypatch.setattr(references, "_LOOKUP", real)
    monkeypatch.setattr(get_settings(), "reference_multisource_enabled", True)
    # Version relation lookup is separate from the new recovery orchestrator.
    monkeypatch.setattr(references, "resolve_versions", lambda *args, **kwargs:
                        __import__("app.credibility.reference_versions", fromlist=["VersionResolution"])
                        .VersionResolution({}, []))


def fallback(provider, *, status="ok", confirm=False, reason_code="target_not_found_in_scope"):
    url = "https://arxiv.org/abs/2001.12345" if provider == "arxiv" else "https://proceedings.mlr.press/v1/paper.html"
    ref = capture_snapshot(kind="test_official_source", raw=b"actual fixture response",
        normalized_json={"title": TITLE, "authors": ["Jane Doe", "John Smith"], "year": 2020},
        fetch_status=status, http_status=200 if status == "ok" else 403, retrieved_at=STAMP,
        requested_url=url, final_url=url)
    result = ReferenceFallback(observations=[ReferenceObservation(url, status, 200 if status == "ok" else 403,
        STAMP, False, ref, provider)], reason_code=reason_code, reason=f"{provider}: {reason_code}.")
    if confirm:
        proof = capture_snapshot(kind="test_official_confirmation", raw=b"actual fixture response",
            normalized_json={"source_snapshot_ref": ref}, parent_ref=ref, fetch_status="ok", http_status=200,
            retrieved_at=STAMP, requested_url=url, final_url=url)
        result.confirmations.append(ReferenceConfirmation(CredibilityBibliographicMetadata(
            title=TITLE, authors=["Jane Doe", "John Smith"], year=2020, doi=None,
            arxiv_id="2001.12345" if provider == "arxiv" else None), proof, url,
            "Verified from a captured official source.", provider))
    return result


def score(result):
    return score_reference_result(result, rubric=load_credibility_rubric())


def test_arxiv_recovery_uses_actual_orchestrator_and_does_not_call_next_provider(monkeypatch):
    lookup(monkeypatch)
    recovered = fallback("arxiv", confirm=True)
    calls = []
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: calls.append(kwargs) or recovered)
    monkeypatch.setattr(reference_publishers, "resolve_publishers",
                        lambda *args, **kwargs: pytest.fail("No next source"))
    result = references.check_references(context())
    assert result.checks.matched_count == 1 and result.checks.unverified_count == 0
    assert result.dimension.check_status == "completed" and result.findings == []
    assert calls[0]["raw_text"] == result.checks.items[0].raw_text
    item = result.checks.items[0]
    chosen = next(c for c in item.candidates if c.candidate_id == item.selected_candidate_id)
    evidence = next(e for e in result.evidence if e.evidence_id in chosen.evidence_ids)
    assert evidence.source_id == chosen.source_id
    assert (read_snapshot(result.sources[-1].snapshot_ref).manifest["parent_ref"]
            == recovered.confirmations[0].snapshot_ref)
    assert verify_report_snapshots(result)["verified_count"] == 3
    assert build_reference_summary(result.checks).verification_rate == 1
    assert score(result).dimension.score == 10


def test_publisher_confirmation_recovers_primary_failure_without_erasing_failed_attempt(monkeypatch):
    lookup(monkeypatch, status="timeout")
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: fallback("arxiv"))
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs:
                        fallback("publisher", confirm=True))
    result = references.check_references(context())
    assert result.checks.matched_count == 1 and result.checks.not_checked_count == 0
    assert result.dimension.check_status == "completed"
    assert [s.fetch_status for s in result.sources] == ["timeout", "ok", "ok", "ok"]
    assert score(result).dimension.score == 10
    assert verify_report_snapshots(result)["verified_count"] == 4
    assert not build_reference_summary(result.checks).unresolved_items


def test_unresolved_actual_year_conflict_is_reported_and_keeps_four_point_rule(monkeypatch):
    lookup(monkeypatch)
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs:
                        fallback("arxiv", reason_code="year_mismatch"))
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs: fallback("publisher"))
    result = references.check_references(context())
    assert result.checks.unverified_count == 1 and result.dimension.check_status == "completed"
    summary = build_reference_summary(result.checks)
    assert summary.verification_rate == 0 and summary.unresolved_items[0].reason_code == "year_mismatch"
    assert summary.unresolved_items[0].source_ids == result.checks.items[0].source_ids
    assert score(result).dimension.score == 4


def test_all_provider_failures_remain_not_checked_and_score_three(monkeypatch):
    lookup(monkeypatch, status="timeout")
    for provider, module in (("arxiv", reference_arxiv), ("publisher", reference_publishers)):
        monkeypatch.setattr(module, "resolve_" + ("arxiv" if provider == "arxiv" else "publishers"),
                            lambda *args, _provider=provider, **kwargs:
                            fallback(_provider, status="access_denied", reason_code="source_fetch_failed"))
    result = references.check_references(context())
    assert result.checks.not_checked_count == 1 and result.dimension.check_status == "partial"
    assert score(result).dimension.score == 3
    assert build_reference_summary(result.checks).unresolved_items[0].reason_code == "source_fetch_failed"


def test_arxiv_outage_is_not_hidden_by_successful_empty_publication_search(monkeypatch):
    lookup(monkeypatch)
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs:
                        fallback("arxiv", status="timeout", reason_code="source_fetch_failed"))
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs: fallback("publisher"))
    result = references.check_references(context())
    summary = build_reference_summary(result.checks)
    assert summary.unresolved_items[0].reason_code == "source_fetch_failed"
    assert result.dimension.check_status == "partial" and score(result).dimension.score == 4


@pytest.mark.parametrize("cause", ["author_mismatch", "version_evidence_missing", "source_fetch_failed"])
def test_an_unbound_cited_arxiv_id_cannot_be_replaced_with_a_title_search(monkeypatch, cause):
    lookup(monkeypatch)
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs:
                        fallback("arxiv", reason_code=cause))
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs:
                        pytest.fail("An unbound explicit identifier must not be replaced"))
    result = references.check_references(context(
        f"Jane Doe and John Smith. {TITLE}. 2020. arXiv:2001.12345."
    ))
    assert result.checks.items[0].parsed.arxiv_id == "2001.12345"
    assert result.checks.unverified_count == 1 and result.checks.matched_count == 0
    assert build_reference_summary(result.checks).unresolved_items[0].reason_code == cause
    assert score(result).dimension.score == 4


def test_official_publication_date_can_confirm_an_independently_bound_arxiv_identity(monkeypatch):
    lookup(monkeypatch)
    arxiv = fallback("arxiv", reason_code="year_mismatch")
    arxiv.identity_snapshot_ref = arxiv.observations[0].snapshot_ref
    publication = fallback("publisher", confirm=True)
    original_proof = publication.confirmations[0].snapshot_ref
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: arxiv)
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs: publication)
    result = references.check_references(context(
        f"Jane Doe and John Smith. {TITLE}. 2020. arXiv:2001.12345."
    ))
    item = result.checks.items[0]
    selected = next(c for c in item.candidates if c.candidate_id == item.selected_candidate_id)
    source = next(s for s in result.sources if s.source_id == selected.source_id)
    proof = read_snapshot(read_snapshot(source.snapshot_ref).manifest["parent_ref"])
    assert proof.normalized_json["arxiv_identity_snapshot_ref"] == arxiv.identity_snapshot_ref
    assert proof.normalized_json["publication_confirmation_snapshot_ref"] == original_proof
    assert selected.metadata.arxiv_id == "2001.12345" and item.parsed.year == 2020
    assert score(result).dimension.score == 10


def test_private_communication_stays_explicit_unknown_without_extra_requests(monkeypatch):
    lookup(monkeypatch)
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: pytest.fail("Private communication"))
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs: pytest.fail("Private"))
    result = references.check_references(context("Jane Doe. Personal communication, March 2024."))
    assert result.checks.unverified_count == 1 and len(result.sources) == 1
    assert build_reference_summary(result.checks).unresolved_items[0].reason_code == "personal_communication"
    assert score(result).dimension.score == 4


def test_doi_title_conflict_is_not_silently_overridden_by_supplementary_matches(monkeypatch):
    metadata = CredibilityBibliographicMetadata(title="Completely unrelated quantum spectroscopy result",
        authors=["Jane Doe"], year=2020, doi="10.1000/test", arxiv_id=None)
    lookup(monkeypatch, candidates=(metadata,))
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: pytest.fail("Existing conflict"))
    result = references.check_references(context(f"Jane Doe. {TITLE}. 2020. doi:10.1000/test"))
    assert result.checks.conflict_count == 1
    assert score(result).dimension.score == 7
    assert build_reference_summary(result.checks).unresolved_items[0].reason_code == "metadata_conflict"


def test_confirmation_with_failed_snapshot_is_never_accepted(monkeypatch):
    lookup(monkeypatch)
    bad = fallback("arxiv", confirm=True)
    failed = capture_snapshot(kind="bad_confirmation", raw=b"failure", fetch_status="error", http_status=503)
    from dataclasses import replace

    bad.confirmations[0] = replace(bad.confirmations[0], snapshot_ref=failed)
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: bad)
    with pytest.raises(SnapshotError, match="successful source proof"):
        references.check_references(context())


def test_no_source_lookup_after_fallback_deadline_and_extraction_stays_separate(monkeypatch):
    lookup(monkeypatch)
    base = references._check_references(context())
    clock = iter([0, 2])
    monkeypatch.setattr(references.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(get_settings(), "reference_fallback_deadline_seconds", 1)
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: pytest.fail("Budget exhausted"))
    result = references._supplement_references(context(), base)
    assert result.dimension.check_status == "partial" and result.checks.extraction_status == "complete"
    assert build_reference_summary(result.checks).unresolved_items[0].reason_code == "budget_exhausted"
    assert score(result).dimension.score == 4
