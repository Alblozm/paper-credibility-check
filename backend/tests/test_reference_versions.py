"""Offline publication-version resolution with real immutable local snapshots."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.credibility import reference_versions as versions
from app.credibility.models import CredibilityBibliographicMetadata, CredibilityReferenceCandidate
from app.credibility.reference_matching import classify_reference
from app.credibility.reference_version_transport import VersionFetch
from app.credibility.snapshots import artifact_root, capture_snapshot, read_snapshot

TITLE = "A detailed survey of language model expressivity"
OLD_TITLE = "Language model expressivity: A detailed survey"
AUTHORS = ["Jane Doe", "John Smith"]
DOI = "10.1234/publication"


def _metadata(*, title=TITLE, year=2020, authors=None, arxiv_id="2012.13255", doi=None):
    return CredibilityBibliographicMetadata(
        title=title, authors=AUTHORS if authors is None else authors, year=year, doi=doi, arxiv_id=arxiv_id,
    )


def _candidate(*, title=TITLE, year=2021, authors=None):
    return CredibilityReferenceCandidate(
        candidate_id="candidate_1", metadata=_metadata(title=title, year=year, authors=authors, doi=DOI,
                                                      arxiv_id=None),
        source_id="source_crossref", evidence_ids=["evidence_crossref"], match_basis="Crossref candidate.",
    )


def _crossref(*, publication_type="proceedings-article", items=None):
    rows = [{"DOI": DOI, "title": [TITLE], "type": publication_type}] if items is None else items
    payload = {"message": {"items": rows}}
    return capture_snapshot(
        kind="crossref_response", raw=json.dumps(payload).encode(), normalized_json=payload,
        requested_url="https://api.crossref.org/works?query.bibliographic=survey&rows=5",
        fetch_status="ok", http_status=200, retrieved_at="2026-10-19T01:00:00Z",
    )


def _record(*, title=TITLE, identifier="2012.13255v2", authors=None, doi=None,
            published="2020-12-24T00:00:00Z", updated="2021-06-01T00:00:00Z"):
    return {"id": identifier, "title": title, "authors": AUTHORS if authors is None else authors,
            "published": published, "updated": updated, "doi": doi}


def _fetch(*entries, status="ok", code=200):
    url = "https://export.arxiv.org/api/query?id_list=2012.13255&max_results=1"
    stamp = "2026-10-19T01:01:00Z"
    raw = b"<feed xmlns='http://www.w3.org/2005/Atom'><title>Offline fixture</title></feed>"
    ref = capture_snapshot(
        kind="reference_version_metadata", raw=raw, normalized_json={"entries": list(entries)},
        requested_url=url, final_url=url, retrieved_at=stamp, fetch_status=status,
        http_status=code, metadata={"provider": "arxiv"},
    )
    return VersionFetch(url, status, code, stamp, False, ref, list(entries))


def _supply(monkeypatch, *observations):
    calls = []
    responses = iter(observations)

    def fetch(params):
        calls.append(params)
        return next(responses)

    monkeypatch.setattr(versions, "fetch_arxiv", fetch)
    return calls


def test_dated_preprint_metadata_explains_a_formal_publication_year_difference(monkeypatch):
    current = _fetch(_record())
    calls = _supply(monkeypatch, current)
    parsed, candidate = _metadata(), _candidate()
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    link = result.links[candidate.candidate_id]
    assert link.relationship == "dated-preprint-metadata"
    assert link.publication_year_range == (2020, 2022)
    assert link.cited_year == 2020
    assert link.candidate_type == "proceedings-article"
    assert calls == [{"id_list": "2012.13255", "max_results": "1"}]
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "matched"
    proof = read_snapshot(link.evidence_ref)
    assert proof.manifest["parent_ref"] == current.snapshot_ref
    assert proof.raw == read_snapshot(current.snapshot_ref).raw
    assert proof.normalized_json["inference"] is True
    assert proof.normalized_json["latest_snapshot_ref"] == current.snapshot_ref
    assert result.observations[-1].snapshot_ref == link.evidence_ref


def test_captured_original_metadata_recovers_a_missing_arxiv_identifier(monkeypatch):
    calls = _supply(monkeypatch, _fetch(_record()))
    parsed, candidate = _metadata(arxiv_id=None), _candidate()
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    assert calls[0]["search_query"] == f'ti:"{TITLE}" AND au:"Doe"'
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "matched"


def test_explicit_doi_relationship_can_confirm_a_changed_title_in_an_older_version(monkeypatch):
    latest = _fetch(_record(identifier="2012.13255v3", doi=DOI))
    older = _fetch(_record(title=OLD_TITLE, identifier="2012.13255v1",
                           updated="2020-12-24T00:00:00Z"))
    calls = _supply(monkeypatch, latest, older)
    parsed, candidate = _metadata(title=OLD_TITLE), _candidate()
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    link = result.links[candidate.candidate_id]
    assert calls[1]["id_list"] == "2012.13255v1,2012.13255v2"
    assert link.relationship == "official-publication-link"
    assert link.cited_title == OLD_TITLE
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "matched"
    proof = read_snapshot(link.evidence_ref).normalized_json
    assert proof["inference"] is False
    assert proof["cited_snapshot_ref"] == older.snapshot_ref


def test_pinned_old_arxiv_version_first_resolves_latest_then_checks_original_title(monkeypatch):
    latest = _fetch(_record(identifier="2012.13255v3", doi=DOI))
    older = _fetch(_record(title=OLD_TITLE, identifier="2012.13255v1",
                           updated="2020-12-24T00:00:00Z"))
    calls = _supply(monkeypatch, latest, older)
    parsed, candidate = _metadata(title=OLD_TITLE, arxiv_id="2012.13255v1"), _candidate()
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    assert calls[0]["id_list"] == "2012.13255"
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "matched"


def test_warp_style_cited_year_before_first_preprint_remains_unverified(monkeypatch):
    _supply(monkeypatch, _fetch(_record(published="2021-01-01T00:00:00Z", updated="2021-05-01T00:00:00Z")))
    parsed, candidate = _metadata(year=2020), _candidate()
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    assert result.links == {}
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "unverified"


def test_formal_candidate_outside_the_source_backed_date_window_is_not_inferred(monkeypatch):
    _supply(monkeypatch, _fetch(_record()))
    result = versions.resolve_versions(_metadata(), [_candidate(year=2025)], _crossref())
    assert result.links == {}


@pytest.mark.parametrize("publication_type", ["posted-content", "book-chapter", "dataset", None])
def test_nonformal_crossref_type_is_rejected_without_an_arxiv_request(monkeypatch, publication_type):
    calls = _supply(monkeypatch)
    result = versions.resolve_versions(_metadata(), [_candidate()], _crossref(publication_type=publication_type))
    assert result.links == {}
    assert calls == []


def test_incomplete_candidate_authors_do_not_trigger_version_inference(monkeypatch):
    calls = _supply(monkeypatch)
    result = versions.resolve_versions(_metadata(), [_candidate(authors=["Jane Doe"])], _crossref())
    assert result.links == {} and calls == []


@pytest.mark.parametrize("authors", [["Jane Doe"], ["Jane Doe", "A Different Author"], []])
def test_missing_or_wrong_preprint_authors_do_not_create_a_link(monkeypatch, authors):
    _supply(monkeypatch, _fetch(_record(authors=authors)))
    assert versions.resolve_versions(_metadata(), [_candidate()], _crossref()).links == {}


def test_another_arxiv_identifier_does_not_confirm_the_cited_preprint(monkeypatch):
    _supply(monkeypatch, _fetch(_record(identifier="2005.14165v2")))
    assert versions.resolve_versions(_metadata(), [_candidate()], _crossref()).links == {}


def test_changed_title_without_an_explicit_publication_doi_is_not_inferred(monkeypatch):
    calls = _supply(monkeypatch, _fetch(_record()))
    result = versions.resolve_versions(_metadata(title=OLD_TITLE), [_candidate()], _crossref())
    assert result.links == {} and len(calls) == 1


def test_failing_provider_observation_retains_its_evidence_but_does_not_create_a_link(monkeypatch):
    failure = _fetch(status="timeout", code=None)
    _supply(monkeypatch, failure)
    result = versions.resolve_versions(_metadata(), [_candidate()], _crossref())
    assert result.links == {} and result.observations == [failure]


@pytest.mark.parametrize("ref", [None, "snapshot:v1:invalid", "snapshot:v1:" + "f" * 64])
def test_absent_or_unreadable_crossref_snapshot_skips_resolution_without_crashing(monkeypatch, ref):
    calls = _supply(monkeypatch)
    result = versions.resolve_versions(_metadata(), [_candidate()], ref)
    assert result.links == {} and calls == []


@pytest.mark.parametrize("items", [None, "not-a-record-list", 12])
def test_malformed_crossref_item_list_does_not_crash_the_reference_branch(monkeypatch, items):
    calls = _supply(monkeypatch)
    payload = {"message": {"items": items}}
    ref = capture_snapshot(kind="crossref_response", normalized_json=payload, fetch_status="ok", http_status=200)
    result = versions.resolve_versions(_metadata(), [_candidate()], ref)
    assert result.links == {} and calls == []


def test_corrupt_crossref_body_skips_version_resolution_without_crashing(monkeypatch):
    calls = _supply(monkeypatch)
    ref = _crossref()
    digest = read_snapshot(ref).manifest["blobs"]["raw"]["sha256"]
    (artifact_root() / "objects" / "sha256" / digest[:2] / digest).write_bytes(b"corrupt")
    assert versions.resolve_versions(_metadata(), [_candidate()], ref).links == {}
    assert calls == []


def test_missing_arxiv_snapshot_does_not_turn_a_valid_record_into_a_proof(monkeypatch):
    observation = replace(_fetch(_record()), snapshot_ref="snapshot:v1:" + "e" * 64)
    _supply(monkeypatch, observation)
    assert versions.resolve_versions(_metadata(), [_candidate()], _crossref()).links == {}


def test_arxiv_entries_must_match_the_captured_normalized_metadata(monkeypatch):
    archived = _fetch(_record(title="A completely different archived work"))
    observation = replace(archived, entries=[_record()])
    _supply(monkeypatch, observation)
    assert versions.resolve_versions(_metadata(), [_candidate()], _crossref()).links == {}


def test_failed_arxiv_snapshot_cannot_supply_successful_version_evidence(monkeypatch):
    observation = _fetch(_record(), status="timeout", code=None)
    _supply(monkeypatch, observation)
    assert versions.resolve_versions(_metadata(), [_candidate()], _crossref()).links == {}


def test_explicit_et_al_allows_only_the_cited_strict_subset_of_complete_external_authors(monkeypatch):
    complete_authors = ["Jane Doe", "John Smith", "Alice Roe"]
    observation = _fetch(_record(authors=complete_authors))
    calls = _supply(monkeypatch, observation)
    parsed, candidate = _metadata(authors=["Jane Doe", "Alice Roe"]), _candidate(authors=complete_authors)
    result = versions.resolve_versions(
        parsed, [candidate], _crossref(), raw_text=f"Jane Doe, Alice Roe, et al. {TITLE}. 2020.",
    )
    link = result.links[candidate.candidate_id]
    assert len(calls) == 1
    assert link.cited_authors == tuple(complete_authors)
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "matched"
    proof = read_snapshot(link.evidence_ref).normalized_json
    assert proof["cited_record"]["authors"] == complete_authors
    assert proof["latest_record"]["authors"] == complete_authors
    assert proof["date_inference"] is False


@pytest.mark.parametrize(("authors", "raw_text"), [
    (["Jane Doe"], f"Jane Doe. {TITLE}. 2020."),
    (["Jane Doe"], f"Jane Doe. {TITLE}: et alpha methods. 2020."),
    (["Jane Doe", "Wrong Person"], f"Jane Doe, Wrong Person, et al. {TITLE}. 2020."),
    (["Jane Doe", "Jane Doe"], f"Jane Doe, Jane Doe, et al. {TITLE}. 2020."),
    (["Jane Doe", "John Smith", "Wrong Person"], f"Jane Doe, John Smith, Wrong Person, et al. {TITLE}. 2020."),
], ids=["no-marker", "not-et-al", "listed-author-mismatch", "duplicate-listed-author", "not-strict-subset"])
def test_unmarked_or_incorrect_cited_author_lists_are_not_expanded(monkeypatch, authors, raw_text):
    calls = _supply(monkeypatch)
    result = versions.resolve_versions(
        _metadata(authors=authors), [_candidate(authors=["Jane Doe", "John Smith", "Alice Roe"])],
        _crossref(), raw_text=raw_text,
    )
    assert result.links == {} and calls == []


@pytest.mark.parametrize(("candidate_authors", "arxiv_authors"), [
    (["Jane Doe", "John Smith", "Alice Roe"], ["Jane Doe", "John Smith"]),
    (["Jane Doe", "John Smith"], ["Jane Doe", "John Smith", "Alice Roe"]),
    (["Jane Doe", "John Smith", "Alice Roe"], ["Jane Doe", "John Smith", "Wrong Person"]),
], ids=["incomplete-arxiv-list", "incomplete-crossref-list", "external-author-mismatch"])
def test_et_al_does_not_relax_full_crossref_against_full_arxiv_author_comparison(
    monkeypatch, candidate_authors, arxiv_authors,
):
    calls = _supply(monkeypatch, _fetch(_record(authors=arxiv_authors)))
    result = versions.resolve_versions(
        _metadata(authors=["Jane Doe"]), [_candidate(authors=candidate_authors)], _crossref(),
        raw_text=f"Jane Doe et al. {TITLE}. 2020.",
    )
    assert result.links == {} and len(calls) == 1


def _title_transition_observations(*, official_doi=DOI, adjacent=True, include_cited=True):
    latest = _fetch(_record(identifier="2012.13255v3", doi=official_doi,
                           published="2023-01-01T00:00:00Z", updated="2025-01-01T00:00:00Z"))
    old = _record(title=OLD_TITLE, identifier="2012.13255v1",
                  published="2023-01-01T00:00:00Z", updated="2023-11-01T00:00:00Z")
    successor = _record(identifier="2012.13255v2" if adjacent else "2012.13255v4",
                        published="2023-01-01T00:00:00Z", updated="2024-05-01T00:00:00Z")
    return latest, _fetch(*([old, successor] if include_cited else [successor]))


def test_official_adjacent_title_change_explains_the_transition_year_with_explicit_date_inference(monkeypatch):
    latest, older = _title_transition_observations()
    calls = _supply(monkeypatch, latest, older)
    parsed, candidate = _metadata(title=OLD_TITLE, year=2024), _candidate(year=2024)
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    link = result.links[candidate.candidate_id]
    assert len(calls) == 2
    assert link.relationship == "official-publication-link" and link.cited_year == 2024
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "matched"
    source = read_snapshot(link.evidence_ref)
    proof = source.normalized_json
    assert proof["cited_title_valid_year_range"] == [2023, 2024]
    assert proof["date_inference"] is True  # The cited v1 itself only contains 2023 dates.
    assert proof["inference"] is False  # DOI identity was explicitly stated by the official provider.
    assert proof["cited_record"]["updated"] == "2023-11-01T00:00:00Z"
    assert proof["cited_snapshot_ref"] == older.snapshot_ref
    assert proof["latest_snapshot_ref"] == latest.snapshot_ref
    assert source.manifest["parent_ref"] == older.snapshot_ref
    transition = {record["id"]: record for record in proof["title_transition_records"]}
    assert transition["2012.13255v1"]["title"] == OLD_TITLE
    assert transition["2012.13255v2"]["title"] == TITLE
    assert transition["2012.13255v2"]["updated"] == "2024-05-01T00:00:00Z"


@pytest.mark.parametrize(("year", "official_doi", "adjacent", "include_cited"), [
    (2025, DOI, True, True),
    (2024, None, True, True),
    (2024, DOI, False, True),
    (2024, DOI, True, False),
], ids=["after-title-transition", "no-official-doi", "nonadjacent-successor", "missing-cited-version"])
def test_title_transition_does_not_create_unbounded_or_unbacked_year_tolerance(
    monkeypatch, year, official_doi, adjacent, include_cited,
):
    observations = _title_transition_observations(
        official_doi=official_doi, adjacent=adjacent, include_cited=include_cited,
    )
    calls = _supply(monkeypatch, *observations)
    parsed, candidate = _metadata(title=OLD_TITLE, year=year), _candidate(year=2024)
    result = versions.resolve_versions(parsed, [candidate], _crossref())
    assert result.links == {}
    assert classify_reference(parsed, [candidate], version_links=result.links)[0] == "unverified"
    assert len(calls) == (1 if official_doi is None else 2)


def test_old_title_without_captured_successor_has_no_inferred_valid_year_range():
    old = _record(title=OLD_TITLE, identifier="2012.13255v1",
                  published="2023-01-01T00:00:00Z", updated="2023-11-01T00:00:00Z")
    observation = _fetch(old)
    assert versions._title_valid_years(old, [(old, observation)]) is None
