"""Offline acceptance for immutable evidence archives and request integration."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.credibility.code_checks import _Check
from app.credibility.code_lookup import Fetch, PublicCodeLookup
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.references import CrossrefLookup, _external_source
from app.credibility.snapshots import (
    SnapshotError,
    SnapshotStore,
    capture_snapshot,
    derive_snapshot,
    read_snapshot,
    verify_report_snapshots,
)


@pytest.fixture(autouse=True)
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))


def test_observations_are_distinct_but_exact_response_content_is_deduplicated(tmp_path):
    store = SnapshotStore(tmp_path)
    arguments = {"kind": "http", "raw": b"{\"body\":\"exact\"}",
                 "normalized_json": {"body": "exact"}, "normalized_text": "exact",
                 "requested_url": "https://example.org/source", "fetch_status": "ok", "http_status": 200}
    first, second = store.record(**arguments), store.record(**arguments)
    left, right = store.read(first), store.read(second)
    assert first != second and str(tmp_path) not in first
    assert left.manifest["observation_id"] != right.manifest["observation_id"]
    assert left.manifest["blobs"] == right.manifest["blobs"]
    assert left.raw == arguments["raw"] and left.normalized_json == {"body": "exact"}
    assert len(list((tmp_path / "objects").rglob("*"))) > 0


def test_model_input_is_saved_without_reformatting_prompt_text():
    payload = {"messages": [{"role": "user", "content": "line 1\n 引文  \n"}], "model": "selected"}
    ref = capture_snapshot(kind="model_input", normalized_json=payload)
    assert read_snapshot(ref).normalized_json == payload


def test_cache_observation_preserves_original_retrieval_and_verified_content():
    original = capture_snapshot(kind="http", raw=b"exact", normalized_text="exact",
                                retrieved_at="2026-10-18T15:00:00Z", fetch_status="ok", http_status=200,
                                metadata={"provider": "test"})
    cached = derive_snapshot(original, is_cached=True, metadata={"source_id": "src_second"})
    value = read_snapshot(cached)
    assert value.manifest["retrieved_at"] == "2026-10-18T15:00:00Z"
    assert value.manifest["is_cached"] is True and value.raw == b"exact"
    assert value.manifest["parent_ref"] == original
    assert value.manifest["metadata"] == {"provider": "test", "source_id": "src_second"}


@pytest.mark.parametrize("target", ["manifest", "raw", "normalized_json", "normalized_text"])
def test_any_modified_manifest_or_payload_is_detected(tmp_path, target):
    store = SnapshotStore(tmp_path)
    ref = store.record(kind="http", raw=b"source", normalized_json={"value": 1}, normalized_text="source text")
    value = store.read(ref)
    digest = ref.rsplit(":", 1)[-1] if target == "manifest" else value.manifest["blobs"][target]["sha256"]
    path = store._path(digest)
    path.write_bytes(b"modified")
    with pytest.raises(SnapshotError, match="integrity"):
        store.read(ref)
    with pytest.raises(SnapshotError):
        store.derive(ref, is_cached=True)


def test_missing_content_and_missing_provenance_are_detected(tmp_path):
    store = SnapshotStore(tmp_path)
    first = store.record(kind="http", raw=b"body")
    second = store.derive(first, is_cached=True)
    store._path(first.rsplit(":", 1)[-1]).unlink()
    with pytest.raises(SnapshotError, match="missing"):
        store.read(second)


@pytest.mark.parametrize("ref", ["/tmp/private", "snapshot:v1:../../secret", "snapshot:v2:" + "a" * 64])
def test_snapshot_references_cannot_be_filesystem_paths(ref):
    with pytest.raises(SnapshotError, match="reference"):
        read_snapshot(ref)


def test_timeout_records_only_actual_failure_metadata_without_fabricated_content():
    ref = capture_snapshot(kind="http", requested_url="https://example.org/failed", fetch_status="timeout")
    value = read_snapshot(ref)
    assert value.raw is None and value.normalized_text is None and value.normalized_json is None
    assert value.manifest["blobs"] == {} and value.manifest["fetch_status"] == "timeout"


def test_concurrent_publication_never_exposes_partial_or_overwritten_blobs(tmp_path):
    store = SnapshotStore(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as executor:
        refs = list(executor.map(lambda _: store.record(kind="input", raw=b"shared"), range(12)))
    assert len(set(refs)) == 12
    assert all(store.read(ref).raw == b"shared" for ref in refs)
    assert not list(tmp_path.rglob(".snapshot-*"))


def install_mock_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr("app.credibility.code_lookup.httpx.Client",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    lookup = PublicCodeLookup(interval=0)
    monkeypatch.setattr(lookup, "_public", lambda _url: True)
    return lookup


def test_code_response_archive_has_actual_body_final_url_revision_path_and_cache_event(monkeypatch):
    calls = []
    revision = "a" * 40
    body = {"sha": revision, "path": "src/model.py", "content": "value"}

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "/final"}) if request.url.path == "/redirect" else (
            httpx.Response(200, json=body)
        )

    lookup = install_mock_transport(monkeypatch, handler)
    first = lookup.get("https://example.org/redirect")
    cached = lookup.get("https://example.org/redirect")
    value = read_snapshot(first.snapshot_ref)
    assert value.raw is not None and json.loads(value.raw) == body
    assert value.normalized_json == body
    assert value.manifest["requested_url"] == "https://example.org/redirect"
    assert value.manifest["final_url"] == "https://example.org/final"
    assert value.manifest["revision"] == revision and value.manifest["path"] == "src/model.py"
    assert len(calls) == 2 and cached.cached and cached.snapshot_ref != first.snapshot_ref
    assert read_snapshot(cached.snapshot_ref).manifest["is_cached"] is True


def test_oversized_body_archive_is_explicitly_truncated_and_cannot_be_used_as_success(monkeypatch):
    lookup = install_mock_transport(monkeypatch, lambda _: httpx.Response(200, content=b"a" * 100))
    lookup.max_bytes = 10
    result = lookup.get("https://example.org/big")
    value = read_snapshot(result.snapshot_ref)
    assert result.status == "error" and result.text == "" and result.data is None
    assert value.manifest["truncated"] is True and value.raw == b"a" * 10
    assert value.normalized_text is None and value.normalized_json is None


def test_http_failure_and_cooldown_get_separate_metadata_only_archives(monkeypatch):
    lookup = install_mock_transport(monkeypatch, lambda _: httpx.Response(429, headers={"retry-after": "60"}))
    first = lookup.get("https://example.org/limited")
    cooldown = lookup.get("https://example.org/other")
    assert first.status == cooldown.status == "rate_limited"
    assert first.snapshot_ref != cooldown.snapshot_ref
    assert read_snapshot(first.snapshot_ref).raw is None
    assert read_snapshot(cooldown.snapshot_ref).manifest["metadata"]["cooldown"] is True


def test_snapshot_storage_failure_propagates_without_returning_success(monkeypatch):
    lookup = install_mock_transport(monkeypatch, lambda _: httpx.Response(200, json={"ok": True}))

    def unavailable(**_kwargs):
        raise OSError("artifact storage unavailable")

    monkeypatch.setattr("app.credibility.code_lookup.capture_snapshot", unavailable)
    with pytest.raises(OSError, match="unavailable"):
        lookup.get("https://example.org/source")
    assert not lookup._cache


def test_crossref_archives_original_response_and_cache_then_links_report_source(monkeypatch):
    body = {"message": {"title": ["An archived paper"], "DOI": "10.1000/paper"}}
    url = "https://api.crossref.org/works/10.1000%2Fpaper"
    response = httpx.Response(200, json=body, request=httpx.Request("GET", url))
    monkeypatch.setattr("app.credibility.references.httpx.get", lambda *_args, **_kwargs: response)
    lookup = CrossrefLookup(min_interval_seconds=0)
    metadata = CredibilityBibliographicMetadata(title=None, authors=[], year=None, doi="10.1000/paper", arxiv_id=None)
    first, cached = lookup.lookup(metadata), lookup.lookup(metadata)
    original, second = read_snapshot(first.snapshot_ref), read_snapshot(cached.snapshot_ref)
    assert original.raw == response.content and original.normalized_json == body
    assert second.manifest["is_cached"] is True
    source = _external_source("ref_source", cached, "ref_001")
    assert source.snapshot_ref not in {first.snapshot_ref, cached.snapshot_ref}
    assert source.retrieved_at == first.retrieved_at
    assert source.observed_at == read_snapshot(source.snapshot_ref).manifest["observed_at"]
    assert verify_report_snapshots({"sources": [source.model_dump()]})["verified_count"] == 1


def test_issue_and_listing_sources_reuse_actual_response_with_independent_provenance():
    fetch_ref = capture_snapshot(kind="public_code_response", normalized_json=[{"body": "I reproduced it."}],
                                 requested_url="https://api.github.com/repos/org/repo/issues", fetch_status="ok",
                                 http_status=200, retrieved_at="2026-10-19T00:00:00Z")
    result = Fetch("https://api.github.com/repos/org/repo/issues", "ok", 200, "2026-10-19T00:00:00Z",
                   snapshot_ref=fetch_ref)
    check = object.__new__(_Check)
    check.sources = []
    check.source(result, "discussion_listing", "Actual listing")
    check.source(result, "issue", "Issue in this actual listing", url="https://github.com/org/repo/issues/1")
    left, right = check.sources
    assert left.snapshot_ref != right.snapshot_ref
    assert read_snapshot(left.snapshot_ref).normalized_json == read_snapshot(right.snapshot_ref).normalized_json
    assert verify_report_snapshots({"sources": [left.model_dump(), right.model_dump()]})["verified_count"] == 2
    tampered = right.model_dump()
    tampered["url"] = "https://github.com/unrelated/repository/issues/1"
    with pytest.raises(SnapshotError, match="URL"):
        verify_report_snapshots({"sources": [tampered]})


def test_report_cannot_claim_a_missing_archive_or_change_the_recorded_failure_state():
    with pytest.raises(SnapshotError, match="no snapshot"):
        verify_report_snapshots({"sources": [{"source_id": "missing", "snapshot_ref": None}]})
    ref = capture_snapshot(kind="http", fetch_status="timeout", http_status=None)
    with pytest.raises(SnapshotError, match="fetch_status"):
        verify_report_snapshots({"sources": [{"source_id": "bad", "snapshot_ref": ref, "fetch_status": "ok"}]})


def test_atomic_content_hash_matches_raw_bytes_and_json_is_canonical(tmp_path):
    store = SnapshotStore(tmp_path)
    ref = store.record(kind="request", raw=b"exact bytes", normalized_json={"b": 2, "a": 1})
    value = store.read(ref)
    assert value.manifest["blobs"]["raw"]["sha256"] == hashlib.sha256(b"exact bytes").hexdigest()
    assert store._read_blob(value.manifest["blobs"]["normalized_json"]["sha256"]) == b'{"a":1,"b":2}'
    assert isinstance(store.root, Path)


def test_crossref_transient_failure_is_retryable_and_success_is_reused(monkeypatch):
    calls = []
    responses = [httpx.Response(503, request=httpx.Request("GET", "https://api.crossref.org/works")),
                 httpx.Response(200, json={"message": {"items": []}},
                                request=httpx.Request("GET", "https://api.crossref.org/works"))]

    def get(*_args, **_kwargs):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr("app.credibility.references.httpx.get", get)
    lookup = CrossrefLookup(min_interval_seconds=0)
    metadata = CredibilityBibliographicMetadata(title="Paper", authors=[], year=None, doi=None, arxiv_id=None)
    assert lookup.lookup(metadata).fetch_status == "error"
    assert lookup.lookup(metadata).fetch_status == "ok"
    assert lookup.lookup(metadata).is_cached is True
    assert len(calls) == 2


def test_crossref_long_retry_after_is_shared_and_explicit_retry_preserves_cooldown(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr("app.credibility.references.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.credibility.references.time.time", lambda: clock[0])

    def get(url, **_kwargs):
        calls.append(url)
        return httpx.Response(429, headers={"retry-after": "9999999"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.credibility.references.httpx.get", get)
    lookup = CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=0)
    first = CredibilityBibliographicMetadata(title="First", authors=[], year=None, doi=None, arxiv_id=None)
    second = first.model_copy(update={"title": "Second"})
    assert lookup.lookup(first).fetch_status == "rate_limited"
    assert lookup.lookup(second).http_status is None  # cooldown, no request was sent
    assert len(calls) == 1 and lookup._state().snapshot_state()["cooldown_until"] == 100.0 + 9999999
    lookup.clear_failed_cache()
    assert lookup.lookup(second).fetch_status == "rate_limited" and len(calls) == 1
    clock[0] = 100.0 + 9999999 + 1
    assert lookup.lookup(first).is_cached is False and len(calls) == 2
    lookup.clear_failed_cache()
    assert lookup._state().snapshot_state()["cooldown_until"] == clock[0] + 9999999


def test_crossref_serializes_network_calls_between_concurrent_tasks(monkeypatch):
    import threading
    import time

    guard = threading.Lock()
    in_flight = 0
    maximum = 0

    def get(url, **_kwargs):
        nonlocal in_flight, maximum
        with guard:
            in_flight += 1
            maximum = max(maximum, in_flight)
        time.sleep(0.01)
        with guard:
            in_flight -= 1
        return httpx.Response(200, json={"message": {"items": []}}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.credibility.references.httpx.get", get)
    lookup = CrossrefLookup(min_interval_seconds=0)
    metadata = CredibilityBibliographicMetadata(title="Base", authors=[], year=None, doi=None, arxiv_id=None)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda i: lookup.lookup(metadata.model_copy(update={"title": f"Paper {i}"})),
                                    range(6)))
    assert all(result.fetch_status == "ok" for result in results) and maximum == 1


def test_code_explicit_failure_refresh_retains_success_cache(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True}) if request.url.path == "/ok" else httpx.Response(503)

    lookup = install_mock_transport(monkeypatch, handler)
    assert lookup.get("https://example.org/ok").status == "ok"
    assert lookup.get("https://example.org/error").status == "error"
    lookup.clear_failed_cache()
    assert lookup.get("https://example.org/ok").cached is True
    assert lookup.get("https://example.org/error").cached is False
    assert len(calls) == 3
