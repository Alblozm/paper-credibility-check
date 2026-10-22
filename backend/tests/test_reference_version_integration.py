"""Reference-version integration with archived evidence and offline transports."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.credibility import reference_version_transport as transport
from app.credibility import references
from app.credibility.context import CheckContext
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_matching import VersionLink
from app.credibility.reference_version_transport import VersionFetch
from app.credibility.reference_versions import VersionResolution
from app.credibility.snapshots import SnapshotError, capture_snapshot, read_snapshot, verify_report_snapshots
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "A detailed survey of language model expressivity"
AUTHORS = ["Jane Doe", "John Smith"]
DOI = "10.1234/publication"
STAMP = "2026-10-19T01:00:00Z"
ARXIV_URL = "https://export.arxiv.org/api/query?id_list=2012.13255&max_results=1"


def _context():
    return CheckContext(
        source={}, repo_root=Path("."), generated_at=STAMP, ref=None,
        doc=ParsedDocument(
            paper=Paper(paper_id="version-integration", parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(
                chunk_id="refs_1", page_start=10, page_end=10, section_title="References",
                section_role="references",
                text=f"Jane Doe and John Smith. {TITLE}. arXiv:2012.13255, 2020.",
            )],
        ), unresolved_reason=None,
    )


def _crossref_payload(*, year=2021):
    return {"status": "ok", "message": {"items": [{
        "DOI": DOI, "title": [TITLE], "type": "proceedings-article",
        "author": [{"given": "Jane", "family": "Doe"}, {"given": "John", "family": "Smith"}],
        "issued": {"date-parts": [[year]]},
    }]}}


def _lookup_result(*, year=2021):
    payload = _crossref_payload(year=year)
    url = "https://api.crossref.org/works?query.bibliographic=survey&rows=5"
    snapshot = capture_snapshot(
        kind="crossref_response", raw=json.dumps(payload).encode(), normalized_json=payload,
        requested_url=url, final_url=url, retrieved_at=STAMP, fetch_status="ok", http_status=200,
    )
    return references._LookupResult(
        url, "ok", 200, STAMP,
        (CredibilityBibliographicMetadata(title=TITLE, authors=AUTHORS, year=year, doi=DOI, arxiv_id=None),),
        snapshot_ref=snapshot,
    )


def _install_real_lookup(monkeypatch, result):
    lookup = references.CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=0)
    monkeypatch.setattr(lookup, "lookup", lambda metadata, **kwargs: result)
    monkeypatch.setattr(references, "_LOOKUP", lookup)


def _observation(*, status="ok", code=200):
    snapshot = capture_snapshot(
        kind="reference_version_metadata", raw=b"<feed>offline observation</feed>",
        normalized_json={"entries": []} if status == "ok" else None,
        requested_url=ARXIV_URL, final_url=ARXIV_URL, retrieved_at=STAMP,
        fetch_status=status, http_status=code, metadata={"provider": "arxiv"},
    )
    return VersionFetch(ARXIV_URL, status, code, STAMP, False, snapshot)


def test_matched_version_proof_binds_candidate_evidence_and_all_source_archives(monkeypatch):
    lookup = _lookup_result()
    _install_real_lookup(monkeypatch, lookup)
    observation = _observation()
    proof = capture_snapshot(
        kind="reference_version_link", raw=read_snapshot(observation.snapshot_ref).raw,
        normalized_json={"relationship": "official-publication-link", "candidate_doi": DOI},
        requested_url=ARXIV_URL, final_url=ARXIV_URL, retrieved_at=STAMP,
        fetch_status="ok", http_status=200, parent_ref=observation.snapshot_ref,
    )
    calls = []

    def resolve(parsed, candidates, snapshot_ref, *, raw_text):
        calls.append((parsed, candidates, snapshot_ref))
        assert snapshot_ref == lookup.snapshot_ref
        assert raw_text == _context().doc.blocks[0].text
        candidate_id = candidates[0].candidate_id
        return VersionResolution({candidate_id: VersionLink(
            cited_arxiv_id="2012.13255", candidate_doi=DOI, evidence_ref=proof,
            relationship="official-publication-link", cited_title=TITLE,
            cited_year=2020, cited_authors=tuple(AUTHORS),
        )}, [observation, VersionFetch(ARXIV_URL, "ok", 200, STAMP, False, proof)])

    monkeypatch.setattr(references, "resolve_versions", resolve)
    result = references.check_references(_context())
    item = result.checks.items[0]
    assert len(calls) == 1
    assert item.outcome == "matched" and result.dimension.check_status == "completed"
    assert item.selected_candidate_id == item.candidates[0].candidate_id
    assert item.source_ids == ["src_ref_001_metadata", "src_ref_001_version_1", "src_ref_001_version_2"]
    evidence = {row.evidence_id: row for row in result.evidence}
    proof_evidence = evidence["ev_ref_001_version_2"]
    assert proof_evidence.source_id == "src_ref_001_version_2"
    assert "ev_ref_001_version_2" in item.candidates[0].evidence_ids
    assert "official-publication-link" in proof_evidence.excerpt
    assert proof in item.reason
    archived = read_snapshot(result.sources[-1].snapshot_ref)
    assert archived.manifest["parent_ref"] == proof
    assert read_snapshot(proof).manifest["parent_ref"] == observation.snapshot_ref
    assert archived.manifest["metadata"]["source_id"] == proof_evidence.source_id
    assert verify_report_snapshots(result)["verified_count"] == 3


@pytest.mark.parametrize(("status", "code"), [("timeout", None), ("rate_limited", 429), ("error", 503)])
def test_version_provider_failure_remains_unverified_and_partial(monkeypatch, status, code):
    _install_real_lookup(monkeypatch, _lookup_result())
    observation = _observation(status=status, code=code)
    def resolve(parsed, candidates, snapshot_ref, *, raw_text):
        assert raw_text == _context().doc.blocks[0].text
        return VersionResolution({}, [observation])

    monkeypatch.setattr(references, "resolve_versions", resolve)
    result = references.check_references(_context())
    item = result.checks.items[0]
    assert item.outcome == "unverified" and item.selected_candidate_id is None
    assert result.dimension.check_status == "partial"
    assert result.checks.extraction_status == "complete"
    assert result.checks.unverified_count == 1 and result.checks.not_checked_count == 0
    assert "Supplementary preprint version lookup was incomplete" in item.reason
    assert item.source_ids == ["src_ref_001_metadata", "src_ref_001_version_1"]
    assert result.sources[-1].fetch_status == status
    assert result.sources[-1].http_status == code
    assert result.findings[0].source_ids == item.source_ids
    assert verify_report_snapshots(result)["verified_count"] == 2


def test_cached_crossref_and_atom_rebuild_complete_local_version_proof_in_another_root(monkeypatch, tmp_path):
    calls = []

    def crossref_get(url, **kwargs):
        calls.append("crossref")
        return httpx.Response(200, json=_crossref_payload(), request=httpx.Request("GET", url))

    atom = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/2012.13255v2</id><title>{TITLE}</title>
      <author><name>Jane Doe</name></author><author><name>John Smith</name></author>
      <published>2020-12-24T00:00:00Z</published><updated>2021-06-01T00:00:00Z</updated>
      <arxiv:doi>{DOI}</arxiv:doi></entry></feed>'''.encode()

    def arxiv_get(request):
        calls.append("arxiv")
        return httpx.Response(200, content=atom, request=request)

    monkeypatch.setattr(references.httpx, "get", crossref_get)
    monkeypatch.setattr(transport, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(arxiv_get)))
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "first-root"))
    monkeypatch.setattr(references, "_LOOKUP", references.CrossrefLookup(min_interval_seconds=0))
    first = references.check_references(_context())
    assert first.checks.items[0].outcome == "matched"
    assert first.dimension.check_status == "completed"
    assert calls == ["crossref", "arxiv"]
    assert verify_report_snapshots(first)["verified_count"] == 3
    original_crossref = read_snapshot(first.sources[0].snapshot_ref)
    original_proof = read_snapshot(first.sources[-1].snapshot_ref)

    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "second-root"))
    monkeypatch.setattr(references, "_LOOKUP", references.CrossrefLookup(min_interval_seconds=0))
    second = references.check_references(_context())
    item = second.checks.items[0]
    assert calls == ["crossref", "arxiv"]
    assert item.outcome == "matched" and second.dimension.check_status == "completed"
    assert all(source.is_cached for source in second.sources)
    assert verify_report_snapshots(second)["verified_count"] == 3
    rebuilt_crossref = read_snapshot(second.sources[0].snapshot_ref)
    rebuilt_proof = read_snapshot(second.sources[-1].snapshot_ref)
    assert rebuilt_crossref.raw == original_crossref.raw
    assert rebuilt_proof.raw == original_proof.raw == atom
    assert rebuilt_crossref.normalized_json == original_crossref.normalized_json
    assert first.sources[0].snapshot_ref != second.sources[0].snapshot_ref
    assert first.sources[-1].snapshot_ref != second.sources[-1].snapshot_ref
    proof = rebuilt_proof.normalized_json
    for field in ("crossref_snapshot_ref", "latest_snapshot_ref", "cited_snapshot_ref"):
        assert read_snapshot(proof[field]).manifest["fetch_status"] == "ok"
    with pytest.raises(SnapshotError):
        read_snapshot(first.sources[-1].snapshot_ref)
    external = {row.evidence_id: row for row in second.evidence if row.origin == "external"}
    assert external[item.candidates[0].evidence_ids[-1]].source_id == second.sources[-1].source_id


def test_non_crossref_lookup_does_not_invoke_the_version_provider(monkeypatch):
    response = _lookup_result()

    class OfflineLookup:
        def lookup(self, metadata):
            return response

    def forbidden(*args, **kwargs):
        raise AssertionError("A fake lookup must not trigger the live version resolver.")

    monkeypatch.setattr(references, "_LOOKUP", OfflineLookup())
    monkeypatch.setattr(references, "resolve_versions", forbidden)
    result = references.check_references(_context())
    assert result.checks.items[0].outcome == "unverified"
    assert len(result.sources) == 1


def test_a_normal_same_year_match_does_not_invoke_the_version_provider(monkeypatch):
    _install_real_lookup(monkeypatch, _lookup_result(year=2020))

    def forbidden(*args, **kwargs):
        raise AssertionError("A completed direct match must not trigger the version resolver.")

    monkeypatch.setattr(references, "resolve_versions", forbidden)
    result = references.check_references(_context())
    assert result.checks.items[0].outcome == "matched"
    assert result.dimension.check_status == "completed" and len(result.sources) == 1
