"""Bounded arXiv version transport, all HTTP mocked; no real network/model."""

from __future__ import annotations

import base64
import hashlib
import sqlite3

import httpx
import pytest

from app.config import get_settings
from app.credibility import reference_version_transport as transport
from app.credibility.snapshots import read_snapshot
from app.storage.crossref_state import CrossrefStateStore

XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry><id>http://arxiv.org/abs/2106.09685v2</id><title>Low-rank\n adaptation</title>
 <author><name>Edward Hu</name></author><author><name>Weizhu Chen</name></author>
 <published>2021-06-17T00:00:00Z</published><updated>2021-10-16T00:00:00Z</updated>
 <arxiv:doi>10.1000/example</arxiv:doi></entry>
</feed>'''


@pytest.fixture
def clock(monkeypatch):
    value = [1000.0]
    monkeypatch.setattr(transport.time, "time", lambda: value[0])
    monkeypatch.setattr(transport.time, "monotonic", lambda: value[0])
    monkeypatch.setattr(transport.time, "sleep", lambda seconds: value.__setitem__(0, value[0] + seconds))
    return value


def mock_client(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(transport, "_new_client", lambda: client(transport=httpx.MockTransport(handler)))


def test_atom_bytes_and_origin_time_survive_cross_artifact_root_cache(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "export.arxiv.org"
        assert request.url.path == "/api/query"
        assert "mailto" not in str(request.url) + str(request.headers)
        return httpx.Response(200, content=XML)

    mock_client(monkeypatch, handler)
    first = transport.fetch_arxiv({"id_list": "2106.09685v2", "max_results": "1"})
    assert first.fetch_status == "ok" and not first.is_cached
    assert first.entries[0] == {
        "id": "2106.09685v2", "title": "Low-rank adaptation", "authors": ["Edward Hu", "Weizhu Chen"],
        "published": "2021-06-17T00:00:00Z", "updated": "2021-10-16T00:00:00Z", "doi": "10.1000/example",
    }
    assert read_snapshot(first.snapshot_ref).raw == XML
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "another-artifact-root"))
    second = transport.fetch_arxiv({"max_results": "1", "id_list": "2106.09685v2"})
    assert second.is_cached and second.retrieved_at == first.retrieved_at and len(calls) == 1
    snap = read_snapshot(second.snapshot_ref)
    assert snap.raw == XML and snap.manifest["is_cached"] is True
    assert snap.normalized_json == {"entries": first.entries}
    store = CrossrefStateStore(str(get_settings().crossref_state_path) + ".arxiv-versions.sqlite3")
    with sqlite3.connect(store.state_path) as connection:
        row = connection.execute("SELECT raw, payload FROM crossref_cache").fetchone()
    assert row[0] is None  # DB envelope is explicitly not the original response JSON.


@pytest.mark.parametrize("params", [
    {}, {"id_list": "2106.09685", "search_query": "test"}, {"url": "https://evil.example"},
    {"id_list": "http://evil.example"}, {"id_list": "2106.09685", "max_results": "6"},
    {"search_query": "test\nheader"}, {"search_query": ""}, {"id_list": "2106.09685", "max_results": 1},
])
def test_invalid_query_never_sends_http(monkeypatch, params):
    mock_client(monkeypatch, lambda _: pytest.fail("Invalid query reached HTTP"))
    result = transport.fetch_arxiv(params)
    assert result.fetch_status == "error" and result.url == "" and not result.entries


@pytest.mark.parametrize("body", [
    b'<!DOCTYPE feed [<!ENTITY external SYSTEM "file:///etc/passwd">]>' + XML,
    XML.replace(b'xmlns="http://www.w3.org/2005/Atom"', b'xmlns="http://evil.example"'),
    XML.replace(b"http://arxiv.org/abs/2106.09685v2", b"http://evil.example/abs/2106.09685v2"),
    XML.replace(b"2106.09685v2", b"2106.09685v0"),
    XML.replace(b"2021-06-17T00:00:00Z", b"2021-06-17"),
    b"x" * (512 * 1024 + 1),
], ids=["doctype", "namespace", "foreign-entry-id", "invalid-version", "naive-date", "oversized"])
def test_invalid_or_oversized_atom_is_evidence_but_never_cached(monkeypatch, body):
    mock_client(monkeypatch, lambda _: httpx.Response(200, content=body))
    result = transport.fetch_arxiv({"id_list": "2106.09685v2"})
    assert result.fetch_status == "error" and not result.entries
    snapshot = read_snapshot(result.snapshot_ref)
    assert snapshot.raw == body[:512 * 1024]
    store = CrossrefStateStore(str(get_settings().crossref_state_path) + ".arxiv-versions.sqlite3")
    with sqlite3.connect(store.state_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM crossref_cache").fetchone()[0] == 0


def test_redirect_is_not_followed(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"}, content=b"moved")

    mock_client(monkeypatch, handler)
    result = transport.fetch_arxiv({"search_query": 'ti:"A paper" AND au:Hu'})
    assert result.fetch_status == "error" and result.http_status == 302 and len(calls) == 1
    assert read_snapshot(result.snapshot_ref).raw == b"moved"


def test_global_spacing_is_shared_by_distinct_calls(monkeypatch, clock):
    called = []

    def handler(request):
        called.append(clock[0])
        return httpx.Response(200, content=XML)

    mock_client(monkeypatch, handler)
    assert transport.fetch_arxiv({"id_list": "2106.09685v2"}).fetch_status == "ok"
    assert transport.fetch_arxiv({"search_query": "ti:adaptation"}).fetch_status == "ok"
    assert called == [1000, 1003]


def test_429_cooldown_is_persisted_without_failure_cache_or_early_retry(monkeypatch, clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"}, content=b"slow down")

    mock_client(monkeypatch, handler)
    first = transport.fetch_arxiv({"id_list": "2106.09685v2"})
    second = transport.fetch_arxiv({"id_list": "2106.09685v2"})
    assert first.fetch_status == second.fetch_status == "rate_limited" and len(calls) == 1
    assert clock[0] == 1000  # Do not truncate server cooldown to our shorter wait budget.
    assert read_snapshot(first.snapshot_ref).raw == b"slow down"


def test_atom_envelope_tampering_is_refetched(monkeypatch, clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=XML)

    mock_client(monkeypatch, handler)
    assert transport.fetch_arxiv({"id_list": "2106.09685v2"}).fetch_status == "ok"
    path = str(get_settings().crossref_state_path) + ".arxiv-versions.sqlite3"
    # A structurally valid, correctly hashed SQLite envelope can still contain
    # modified bytes: verify its inner raw XML hash too.
    import json

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM crossref_cache").fetchone()[0])
        payload["raw_b64"] = base64.b64encode(b"tampered").decode()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        connection.execute("UPDATE crossref_cache SET payload=?, payload_sha256=?",
                           (encoded, hashlib.sha256(encoded).hexdigest()))
    result = transport.fetch_arxiv({"id_list": "2106.09685v2"})
    assert result.fetch_status == "ok" and not result.is_cached and len(calls) == 2


def test_total_http_deadline_is_not_restarted_by_response_headers(monkeypatch, clock):
    def handler(request):
        clock[0] += 11
        return httpx.Response(200, content=XML)

    mock_client(monkeypatch, handler)
    result = transport.fetch_arxiv({"id_list": "2106.09685v2"})
    assert result.fetch_status == "timeout" and not result.entries


def test_transport_exception_text_is_not_copied_to_snapshot(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("secret@example.com private failure", request=request)

    mock_client(monkeypatch, handler)
    result = transport.fetch_arxiv({"id_list": "2106.09685v2"})
    assert result.fetch_status == "error"
    assert "secret@example.com" not in str(read_snapshot(result.snapshot_ref).manifest)
