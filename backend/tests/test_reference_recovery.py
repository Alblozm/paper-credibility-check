"""Successful reference queries survive recovery; rate-limit waits stay bounded."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.credibility.context import CheckContext
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.references import CrossrefLookup, _LookupResult, check_references, reference_retry_budget
from app.credibility.snapshots import SnapshotStore, read_snapshot
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper


@pytest.fixture(autouse=True)
def artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))


def context(text):
    block = TextBlock(chunk_id="refs", text=text, page_start=5, page_end=5,
                      section_id="references", section_title="References", section_role="references")
    doc = ParsedDocument(paper=Paper(paper_id="paper", parse_quality=ParseQuality.GOOD),
                         blocks=[block], parse_quality=ParseQuality.GOOD)
    return CheckContext(source={}, repo_root=Path("."), generated_at="2026-10-19T00:00:00Z",
                        ref=None, doc=doc, unresolved_reason=None)


def metadata(title="A Good Paper", doi="10.1000/one"):
    return CredibilityBibliographicMetadata(title=title, authors=["Jane Doe"], year=2024, doi=doi, arxiv_id=None)


def result(status="ok", candidate=None):
    return _LookupResult("https://api.crossref.org/works/test", status, 200 if status == "ok" else None,
                         "2026-10-19T00:00:00Z", (candidate,) if candidate else ())


class FakeLookup:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def lookup(self, item):
        self.calls.append(item)
        return self.results.pop(0)


def test_recovery_reuses_matched_and_unverified_items_but_requeries_not_checked(monkeypatch):
    ctx = context("[1] Jane Doe. A Good Paper. 2024. doi:10.1000/one\n"
                  "[2] Jane Doe. Another Paper. 2024. doi:10.1000/two\n"
                  "[3] Jane Doe. Last Paper. 2024. doi:10.1000/three")
    original = FakeLookup(result(candidate=metadata()), result(candidate=metadata("Unrelated", "10.1000/other")),
                          result("timeout"))
    monkeypatch.setattr("app.credibility.references._LOOKUP", original)
    first = check_references(ctx)
    assert [item.outcome for item in first.checks.items] == ["matched", "unverified", "not_checked"]
    ctx.previous_check = first.model_dump(mode="json")
    recovery = FakeLookup(result(candidate=metadata("Last Paper", "10.1000/three")))
    monkeypatch.setattr("app.credibility.references._LOOKUP", recovery)
    second = check_references(ctx)
    assert len(recovery.calls) == 1 and recovery.calls[0].doi == "10.1000/three"
    assert second.checks.items[:2] == first.checks.items[:2]
    assert second.sources[:2] == first.sources[:2]
    assert second.findings == first.findings
    assert second.checks.not_checked_count == 0
    assert len({entry.evidence_id for entry in second.evidence}) == len(second.evidence)


@pytest.mark.parametrize("changed", ["raw_text", "reference_id", "source_missing", "source_corrupt"])
def test_recovery_does_not_reuse_changed_identity_or_unverifiable_archive(monkeypatch, changed):
    ctx = context("[1] Jane Doe. A Good Paper. 2024. doi:10.1000/one")
    monkeypatch.setattr("app.credibility.references._LOOKUP", FakeLookup(result(candidate=metadata())))
    previous = check_references(ctx)
    payload = previous.model_dump(mode="json")
    if changed in {"raw_text", "reference_id"}:
        payload["checks"]["items"][0][changed] += "changed"
    else:
        archived = read_snapshot(previous.sources[0].snapshot_ref)
        digest = archived.manifest["blobs"]["normalized_json"]["sha256"]
        path = SnapshotStore()._path(digest)
        path.unlink() if changed == "source_missing" else path.write_bytes(b"tampered")
    ctx.previous_check = payload
    recovery = FakeLookup(result(candidate=metadata()))
    monkeypatch.setattr("app.credibility.references._LOOKUP", recovery)
    # Use a distinct fake response body after corrupting storage so repaired data
    # gets another digest rather than attempting to overwrite the corrupt blob.
    if changed == "source_corrupt":
        recovery.results = [result(candidate=metadata("A Good Paper", "10.1000/new"))]
    check_references(ctx)
    assert len(recovery.calls) == 1


def install_clock(monkeypatch):
    clock, waits = [100.0], []
    monkeypatch.setattr("app.credibility.references.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.credibility.references.time.time", lambda: clock[0])

    def sleep(seconds):
        assert seconds >= 0
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("app.credibility.references.time.sleep", sleep)
    return clock, waits


def test_429_waits_once_then_success_retains_both_actual_response_snapshots(monkeypatch):
    clock, waits = install_clock(monkeypatch)
    calls = []

    def get(url, **_kwargs):
        calls.append(clock[0])
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, content=b"rate limited",
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"message": {"title": ["A Good Paper"], "DOI": "10.1000/one"}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr("app.credibility.references.httpx.get", get)
    lookup = CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=10)
    answer = lookup.lookup(metadata())
    assert answer.fetch_status == "ok" and calls == [100.0, 102.0]
    assert 2 in waits
    success = read_snapshot(answer.snapshot_ref)
    failed = read_snapshot(success.manifest["parent_ref"])
    assert success.manifest["http_status"] == 200 and failed.manifest["http_status"] == 429
    assert failed.raw == b"rate limited"
    assert lookup.lookup(metadata()).is_cached and len(calls) == 2


def test_total_wait_budget_is_shared_between_reference_queries_not_reset_per_item(monkeypatch):
    _, waits = install_clock(monkeypatch)
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        return httpx.Response(429, headers={"retry-after": "2"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.credibility.references.httpx.get", get)
    lookup = CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=3)
    with reference_retry_budget(3):
        assert lookup.lookup(metadata()).fetch_status == "rate_limited"
        assert lookup.lookup(metadata("Second", "10.1000/two")).fetch_status == "rate_limited"
    assert len(calls) == 2 and sum(waits) == 2


def test_retry_after_larger_than_budget_never_causes_an_early_retry(monkeypatch):
    _, waits = install_clock(monkeypatch)
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        return httpx.Response(429, headers={"retry-after": "120"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.credibility.references.httpx.get", get)
    lookup = CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=60)
    assert lookup.lookup(metadata()).fetch_status == "rate_limited"
    assert len(calls) == 1 and sum(waits) == 0


def test_recovery_success_parent_links_to_previous_failed_attempt(monkeypatch):
    ctx = context("[1] Jane Doe. A Good Paper. 2024. doi:10.1000/one")
    first = CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=0)

    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("simulated")

    monkeypatch.setattr("app.credibility.references.httpx.get", timeout)
    monkeypatch.setattr("app.credibility.references._LOOKUP", first)
    old = check_references(ctx)
    ctx.previous_check = old.model_dump(mode="json")
    monkeypatch.setattr("app.credibility.references.httpx.get", lambda url, **_kwargs: httpx.Response(
        200, json={"message": {"title": ["A Good Paper"], "DOI": "10.1000/one"}},
        request=httpx.Request("GET", url)))
    resumed = check_references(ctx)
    source_snapshot = read_snapshot(resumed.sources[0].snapshot_ref)
    response_snapshot = read_snapshot(source_snapshot.manifest["parent_ref"])
    assert response_snapshot.manifest["parent_ref"] == old.sources[0].snapshot_ref
    assert resumed.checks.items[0].outcome == "matched"
