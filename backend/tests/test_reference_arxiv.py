"""Official arXiv fallback: offline sources, strict identities and finite scope."""
from __future__ import annotations

from dataclasses import replace
from xml.sax.saxutils import escape

import pytest

from app.credibility import reference_arxiv as resolver
from app.credibility import reference_publisher_http as publisher_http
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_publisher_http import PublisherFetch
from app.credibility.reference_sources import ReferenceFallback
from app.credibility.reference_version_transport import VersionFetch
from app.credibility.snapshots import capture_snapshot, read_snapshot

TITLE = "Learning adaptable representations with language models"
AUTHORS = ["Jane Doe", "John Smith"]


def _parsed(*, title=TITLE, authors=None, year=2020, arxiv_id="2012.12345", doi=None):
    return CredibilityBibliographicMetadata(title=title, authors=AUTHORS if authors is None else authors,
                                           year=year, arxiv_id=arxiv_id, doi=doi)


def _record(*, title=TITLE, authors=None, identifier="2012.12345v2", doi=None,
            published="2020-12-01T00:00:00Z", updated="2022-03-01T00:00:00Z"):
    return {"id": identifier, "title": title, "authors": AUTHORS if authors is None else authors,
            "published": published, "updated": updated, "doi": doi}


def _fetch(*records, status="ok", code=200):
    entries = []
    for row in records:
        entries.append("<entry>" + f"<id>http://arxiv.org/abs/{escape(row['id'])}</id>"
                       + f"<title>{escape(row['title'])}</title>"
                       + "".join(f"<author><name>{escape(author)}</name></author>" for author in row["authors"])
                       + f"<published>{row['published']}</published><updated>{row['updated']}</updated>"
                       + (f"<arxiv:doi>{escape(row['doi'])}</arxiv:doi>" if row["doi"] else "") + "</entry>")
    raw = ("<feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom'>"
           + "".join(entries) + "</feed>").encode()
    url, stamp = "https://export.arxiv.org/api/query?id_list=2012.12345&max_results=5", "2026-10-19T00:00:00Z"
    ref = capture_snapshot(kind="reference_version_metadata", raw=raw,
                           normalized_json={"entries": list(records)} if status == "ok" else None,
                           requested_url=url, final_url=url, retrieved_at=stamp,
                           fetch_status=status, http_status=code)
    return VersionFetch(url, status, code, stamp, False, ref, list(records))


def _supply(monkeypatch, *responses, history=None):
    calls, iterator = [], iter(responses)

    def fetch(params):
        calls.append(params)
        return next(iterator)

    monkeypatch.setattr(resolver, "fetch_arxiv", fetch)
    monkeypatch.setattr(resolver, "_history", lambda *_: history)
    return calls


def test_explicit_identifier_confirms_and_archives_real_atom_proof(monkeypatch):
    observation = _fetch(_record())
    calls = _supply(monkeypatch, observation)
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert calls == [{"id_list": "2012.12345", "max_results": "1"}]
    assert result.reason_code == "confirmed" and len(result.confirmations) == 1
    confirmation = result.confirmations[0]
    assert confirmation.metadata.arxiv_id == "2012.12345v2"
    assert confirmation.metadata.year == 2020
    proof = read_snapshot(confirmation.snapshot_ref)
    assert proof.raw == read_snapshot(observation.snapshot_ref).raw
    assert proof.normalized_json["selected_record"] == _record()
    assert proof.normalized_json["confirmed_metadata"] == confirmation.metadata.model_dump(mode="json")
    assert proof.normalized_json["selected_snapshot_ref"] == observation.snapshot_ref
    assert proof.manifest["parent_ref"] == observation.snapshot_ref
    assert result.observations[0].provider == "arxiv"
    assert result.identity_snapshot_ref == observation.snapshot_ref


def test_no_identifier_searches_exact_title_and_first_author(monkeypatch):
    calls = _supply(monkeypatch, _fetch(_record()))
    result = resolver.resolve_arxiv(_parsed(arxiv_id=None), raw_text=TITLE)
    assert result.reason_code == "confirmed"
    assert calls == [{"search_query": f'ti:"{TITLE}" AND au:"doe"', "max_results": "5"}]
    assert result.identity_snapshot_ref is None  # No cited ID exists to bind.


def test_empty_phrase_result_has_only_one_long_word_conjunction_retry(monkeypatch):
    calls = _supply(monkeypatch, _fetch(), _fetch(_record()))
    result = resolver.resolve_arxiv(_parsed(arxiv_id=None), raw_text=TITLE)
    assert result.reason_code == "confirmed" and len(calls) == 2
    assert 'ti:"representations" AND' in calls[1]["search_query"]
    assert calls[1]["search_query"].endswith('AND au:"doe"')


def test_typography_normalization_does_not_require_lowering_title_identity(monkeypatch):
    _supply(monkeypatch, _fetch(_record()))
    result = resolver.resolve_arxiv(_parsed(title="Learning adapt able representations with languagemodels"),
                                     raw_text=TITLE)
    assert result.reason_code == "confirmed"


@pytest.mark.parametrize("authors", [["Jane Doe"], ["Jane Doe", "Mary Smith"], ["Wrong Author", "John Smith"]])
def test_full_cited_authors_cannot_be_silently_dropped(monkeypatch, authors):
    calls = _supply(monkeypatch, _fetch(_record()))
    result = resolver.resolve_arxiv(_parsed(authors=authors), raw_text=TITLE)
    assert not result.confirmations and result.reason_code == "author_mismatch"
    assert len(calls) == 1
    assert result.identity_snapshot_ref is None


def test_explicit_et_al_allows_only_a_strict_correct_author_subset(monkeypatch):
    _supply(monkeypatch, _fetch(_record(authors=[*AUTHORS, "Alice Roe"])))
    result = resolver.resolve_arxiv(_parsed(authors=["Jane Doe"]), raw_text="Jane Doe et al. " + TITLE)
    assert result.reason_code == "confirmed"
    assert result.confirmations[0].metadata.authors == [*AUTHORS, "Alice Roe"]


@pytest.mark.parametrize("authors,marker", [
    (["Jane Doe"], "et alpha"), (["Jane Doe", "Jane Doe"], "et al."),
    (["Jane Doe", "A Wrong Author"], "et al."),
])
def test_et_al_cannot_hide_wrong_listed_authors_or_duplicate_names(monkeypatch, authors, marker):
    _supply(monkeypatch, _fetch(_record(authors=[*AUTHORS, "Alice Roe"])))
    result = resolver.resolve_arxiv(_parsed(authors=authors), raw_text=f"Jane Doe {marker} {TITLE}")
    assert not result.confirmations and result.reason_code == "author_mismatch"


def test_same_identifier_and_first_family_names_allow_omitted_middle_name(monkeypatch):
    _supply(monkeypatch, _fetch(_record(authors=["Jane Isabella Doe", "John Quentin Smith"])))
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert result.reason_code == "confirmed"


def test_other_arxiv_identifier_is_never_accepted_even_when_metadata_are_identical(monkeypatch):
    _supply(monkeypatch, _fetch(_record(identifier="2201.99999v2")))
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert result.reason_code == "identifier_mismatch" and not result.confirmations
    assert result.identity_snapshot_ref is None


def test_unrelated_search_result_authors_do_not_imply_a_citation_author_conflict(monkeypatch):
    unrelated = _fetch(_record(title="An unrelated broad search result", authors=["Alice Roe", "Alex Brown"]))
    calls = _supply(monkeypatch, unrelated, unrelated)
    result = resolver.resolve_arxiv(_parsed(arxiv_id=None), raw_text=TITLE)
    assert not result.confirmations and result.reason_code == "target_not_found_in_scope"
    assert len(calls) == 2


def test_exact_title_search_result_still_reports_incompatible_authors(monkeypatch):
    wrong_authors = _fetch(_record(authors=["Alice Roe", "Alex Brown"]))
    _supply(monkeypatch, wrong_authors, wrong_authors)
    result = resolver.resolve_arxiv(_parsed(arxiv_id=None), raw_text=TITLE)
    assert not result.confirmations and result.reason_code == "author_mismatch"


def test_conflicting_explicit_doi_remains_unverified(monkeypatch):
    _supply(monkeypatch, _fetch(_record(doi="10.1234/other")))
    result = resolver.resolve_arxiv(_parsed(doi="10.1234/cited"), raw_text=TITLE)
    assert result.reason_code == "identifier_mismatch" and not result.confirmations
    assert result.identity_snapshot_ref is None


def test_year_before_original_submission_is_not_tolerated(monkeypatch):
    calls = _supply(monkeypatch, _fetch(_record()), _fetch(_record(identifier="2012.12345v1")))
    result = resolver.resolve_arxiv(_parsed(year=2019), raw_text=TITLE)
    assert result.reason_code == "year_mismatch" and not result.confirmations
    assert len(calls) == 2


def test_missing_original_year_remains_null_in_confirmation(monkeypatch):
    _supply(monkeypatch, _fetch(_record()))
    result = resolver.resolve_arxiv(_parsed(year=None), raw_text=TITLE)
    assert result.reason_code == "confirmed" and result.confirmations[0].metadata.year is None
    proof = read_snapshot(result.confirmations[0].snapshot_ref).normalized_json
    assert proof["year_basis"] == "citation_year_missing"
    assert proof["cited_year"] is None


def test_original_submission_year_with_later_title_is_explicitly_annotated(monkeypatch):
    _supply(monkeypatch, _fetch(_record()))
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert "first-submission year" in result.confirmations[0].reason


def test_same_id_old_title_requires_actual_pinned_metadata(monkeypatch):
    latest = _fetch(_record(title="A substantially renamed publication", identifier="2012.12345v3"))
    old = _fetch(_record(identifier="2012.12345v1", updated="2020-12-01T00:00:00Z"))
    calls = _supply(monkeypatch, latest, old)
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert result.reason_code == "confirmed" and len(calls) == 2
    assert calls[1] == {"id_list": "2012.12345v1,2012.12345v2", "max_results": "2"}
    confirmation = result.confirmations[0]
    assert confirmation.metadata.title == TITLE and confirmation.metadata.arxiv_id.endswith("v1")
    assert "older version" in confirmation.reason
    proof = read_snapshot(confirmation.snapshot_ref).normalized_json
    assert len(proof["version_records"]) == 2


def test_same_authors_and_identifier_cannot_confirm_an_unseen_title(monkeypatch):
    latest = _fetch(_record(title="A substantially renamed publication", identifier="2012.12345v3"))
    older = _fetch(_record(title="A different title", identifier="2012.12345v1"))
    _supply(monkeypatch, latest, older)
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert result.reason_code == "version_evidence_missing" and not result.confirmations
    assert result.identity_snapshot_ref is None


def test_matching_cited_id_title_authors_bind_identity_even_when_year_cannot_be_confirmed(monkeypatch):
    current = _fetch(_record())
    _supply(monkeypatch, current, _fetch())
    result = resolver.resolve_arxiv(_parsed(year=2021), raw_text=TITLE)
    assert result.reason_code == "year_mismatch" and not result.confirmations
    assert result.identity_snapshot_ref == current.snapshot_ref


def test_captured_identity_survives_later_old_version_transport_failure(monkeypatch):
    current = _fetch(_record())
    _supply(monkeypatch, current, _fetch(status="timeout", code=None))
    result = resolver.resolve_arxiv(_parsed(year=2021), raw_text=TITLE)
    assert not result.confirmations and result.identity_snapshot_ref == current.snapshot_ref
    assert result.observations[-1].fetch_status == "timeout"


def test_pinned_version_cannot_be_replaced_with_matching_latest_metadata(monkeypatch):
    latest = _fetch(_record(identifier="2012.12345v3"))
    # Upstream ignores both requests and keeps supplying another version.
    calls = _supply(monkeypatch, latest, latest)
    result = resolver.resolve_arxiv(_parsed(arxiv_id="2012.12345v1"), raw_text=TITLE)
    assert not result.confirmations and result.identity_snapshot_ref is None
    assert result.reason_code == "version_evidence_missing"
    assert calls[0] == {"id_list": "2012.12345v1", "max_results": "1"}
    assert "2012.12345v1" in calls[1]["id_list"].split(",")


def test_missing_explicit_pinned_version_does_not_bind_other_matching_old_versions(monkeypatch):
    latest = _fetch(_record(identifier="2012.12345v4"))
    other = _fetch(_record(identifier="2012.12345v2"))
    _supply(monkeypatch, latest, other)
    result = resolver.resolve_arxiv(_parsed(arxiv_id="2012.12345v1"), raw_text=TITLE)
    assert not result.confirmations and result.identity_snapshot_ref is None
    assert result.reason_code == "version_evidence_missing"


def test_exact_pinned_version_is_retrieved_before_identity_confirmation(monkeypatch):
    latest = _fetch(_record(identifier="2012.12345v3", title="An updated title", authors=["Alice Roe"]))
    actual_pin = _fetch(_record(identifier="2012.12345v1", updated="2020-12-01T00:00:00Z"))
    calls = _supply(monkeypatch, latest, actual_pin)
    result = resolver.resolve_arxiv(_parsed(arxiv_id="2012.12345v1"), raw_text=TITLE)
    assert result.reason_code == "confirmed" and len(calls) == 2
    assert result.identity_snapshot_ref == actual_pin.snapshot_ref
    assert result.confirmations[0].metadata.arxiv_id == "2012.12345v1"


def test_exact_pin_identity_is_independent_of_its_unconfirmed_year(monkeypatch):
    latest = _fetch(_record(identifier="2012.12345v3"))
    actual_pin = _fetch(_record(identifier="2012.12345v1", updated="2020-12-01T00:00:00Z"))
    _supply(monkeypatch, latest, actual_pin)
    result = resolver.resolve_arxiv(_parsed(year=2021, arxiv_id="2012.12345v1"), raw_text=TITLE)
    assert not result.confirmations and result.reason_code == "year_mismatch"
    assert result.identity_snapshot_ref == actual_pin.snapshot_ref


def test_exact_pinned_doi_conflict_cannot_bind_identity(monkeypatch):
    latest = _fetch(_record(identifier="2012.12345v3"))
    actual_pin = _fetch(_record(identifier="2012.12345v1", doi="10.1234/different"))
    _supply(monkeypatch, latest, actual_pin)
    result = resolver.resolve_arxiv(_parsed(arxiv_id="2012.12345v1", doi="10.1234/cited"), raw_text=TITLE)
    assert not result.confirmations and result.identity_snapshot_ref is None
    assert result.reason_code == "identifier_mismatch"


def test_submission_history_selects_high_numbered_cited_year_versions(monkeypatch):
    current = _fetch(_record(identifier="2012.12345v9"))
    history = resolver._History({1: "2020-12-01T00:00:00Z", 7: "2021-01-02T00:00:00Z",
                                 9: "2022-03-01T00:00:00Z"}, current.snapshot_ref)
    version = _fetch(_record(identifier="2012.12345v7", updated="2021-01-02T00:00:00Z"))
    calls = _supply(monkeypatch, current, version, history=history)
    result = resolver.resolve_arxiv(_parsed(year=2021), raw_text=TITLE)
    assert result.reason_code == "confirmed"
    assert calls[1]["id_list"].split(",")[0] == "2012.12345v7"
    assert len(calls[1]["id_list"].split(",")) <= 4
    proof = read_snapshot(result.confirmations[0].snapshot_ref).normalized_json
    assert proof["submission_history_snapshot_ref"] == current.snapshot_ref


def test_old_version_scope_is_at_most_four_records_across_two_searches(monkeypatch):
    current = _fetch(_record(identifier="2012.12345v9"))
    calls = _supply(monkeypatch, current, _fetch(), current)
    result = resolver.resolve_arxiv(_parsed(year=2021, arxiv_id=None), raw_text=TITLE)
    assert result.reason_code == "year_mismatch" and not result.confirmations
    assert len(calls) == 3
    assert len([call for call in calls if "id_list" in call]) == 1
    assert len(calls[1]["id_list"].split(",")) == 4


@pytest.mark.parametrize("status,code", [("timeout", None), ("rate_limited", 429), ("error", 500)])
def test_failed_provider_keeps_observation_and_stops_search_retries(monkeypatch, status, code):
    failed = _fetch(status=status, code=code)
    calls = _supply(monkeypatch, failed)
    result = resolver.resolve_arxiv(_parsed(arxiv_id=None), raw_text=TITLE)
    assert result.reason_code == "source_fetch_failed" and not result.confirmations
    assert len(calls) == 1 and result.observations[0].fetch_status == status
    assert result.observations[0].snapshot_ref == failed.snapshot_ref


def test_in_memory_metadata_cannot_override_raw_archived_atom(monkeypatch):
    valid = _fetch(_record(title="A different archived title"))
    altered = replace(valid, entries=[_record()])
    _supply(monkeypatch, altered)
    result = resolver.resolve_arxiv(_parsed(), raw_text=TITLE)
    assert result.reason_code == "source_fetch_failed" and not result.confirmations


@pytest.mark.parametrize("metadata,code", [
    (_parsed(title=None), "insufficient_metadata"), (_parsed(authors=[]), "insufficient_metadata"),
    (_parsed(arxiv_id="http://evil.test/123"), "invalid_arxiv_id"),
])
def test_insufficient_or_invalid_identity_never_queries(monkeypatch, metadata, code):
    calls = _supply(monkeypatch)
    result = resolver.resolve_arxiv(metadata, raw_text=TITLE)
    assert result.reason_code == code and not result.confirmations and not calls


def _history_fetch(raw, *, status="ok", code=200):
    url, stamp = "https://arxiv.org/abs/2012.12345", "2026-10-19T00:00:00Z"
    ref = capture_snapshot(kind="official_publication_response", raw=raw, requested_url=url, final_url=url,
                           retrieved_at=stamp, fetch_status=status, http_status=code)
    return PublisherFetch(url, url, status, code, stamp, False, ref, raw, "text/html")


def test_history_reads_only_official_submission_history_container(monkeypatch):
    raw = b'''<html><div>[v99] Thu, 9 Nov 2099 00:00:00 UTC</div>
      <div class="submission-history"><h2>Submission history</h2>
       <div class="versions"><b><a href="/abs/2012.12345v1">[v1]</a></b>
       Tue, 1 Dec 2020 00:00:00 UTC (10 KB)<br>
       <b>[v7]</b> Sun, 2 Jan 2022 00:00:00 UTC (11 KB)</div></div>
       <div>[v80] Thu, 9 Nov 2080 00:00:00 UTC</div></html>'''
    fetched = _history_fetch(raw)
    urls = []
    monkeypatch.setattr(publisher_http, "fetch_official", lambda url: urls.append(url) or fetched)
    result = ReferenceFallback()
    history = resolver._history("2012.12345v9", result)
    assert urls == ["https://arxiv.org/abs/2012.12345"]
    assert history.dates == {1: "2020-12-01T00:00:00Z", 7: "2022-01-02T00:00:00Z"}
    assert result.observations[0].snapshot_ref == fetched.snapshot_ref


@pytest.mark.parametrize("raw", [
    b'<div>[v1] Tue, 1 Dec 2020 00:00:00 UTC</div>',
    b'<div class="submission-history">[v1] 31 Nov 2020 00:00:00 UTC</div>',
    b'<div class="submission-history"><a href="/abs/2101.99999v1">[v1]</a> '
    b'Tue, 1 Dec 2020 00:00:00 UTC</div>',
])
def test_unstructured_invalid_or_wrong_id_history_never_provides_dates(monkeypatch, raw):
    monkeypatch.setattr(publisher_http, "fetch_official", lambda _: _history_fetch(raw))
    result = ReferenceFallback()
    assert resolver._history("2012.12345", result) is None
    assert len(result.observations) == 1


def test_history_failure_is_recorded_without_claiming_an_empty_success(monkeypatch):
    failed = _history_fetch(b"upstream unavailable", status="error", code=503)
    monkeypatch.setattr(publisher_http, "fetch_official", lambda _: failed)
    result = ReferenceFallback()
    assert resolver._history("2012.12345", result) is None
    assert result.observations[0].fetch_status == "error"
