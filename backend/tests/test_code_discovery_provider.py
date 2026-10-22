"""Offline transport tests: no network, model, environment key or paid calls."""

import json
import threading
import time

import httpx
import pytest

from app.credibility import code_discovery_provider as module
from app.credibility.code_discovery_provider import (
    FETCH_ENDPOINT,
    SEARCH_ENDPOINT,
    DeepSeekCodeDiscoveryProvider,
)
from app.credibility.external_tools import _text_from_data
from app.credibility.snapshots import SnapshotStore

KEY = "offline-key-do-not-persist"
PUBLIC = "93.184.216.34"


@pytest.fixture
def factory(tmp_path):
    clients = []

    def create(handler=None, **kwargs):
        seen = []

        def transport(request):
            seen.append(request)
            if handler:
                return handler(request)
            return httpx.Response(200, json={"title": "Paper", "content": "# Official implementation"})

        client = httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=True)
        clients.append(client)
        store = SnapshotStore(tmp_path / str(len(clients)))
        resolver = kwargs.pop("resolver", lambda _: [PUBLIC])
        provider = DeepSeekCodeDiscoveryProvider(
            api_key=kwargs.pop("api_key", KEY), client=client, store=store, resolver=resolver, **kwargs,
        )
        return provider, store, seen, client

    yield create
    for client in clients:
        client.close()


def serialized(store, result):
    snap = store.read(result.snapshot_ref)
    return json.dumps({"manifest": snap.manifest, "data": snap.normalized_json,
                       "text": snap.normalized_text}, ensure_ascii=False)


def test_search_uses_fixed_endpoint_and_whitelists_candidates(factory):
    rows = [{"url": "https://example.org/paper", "title": "Paper", "snippet": "Candidate only",
             "summary": "must not promote", "content": "must not save", "secret_extra": KEY}]
    provider, store, seen, client = factory(lambda _: httpx.Response(200, json={"results": rows}))
    result = provider.search("论文 exact paper code", count=3)
    assert result.status == "ok" and result.reason_code == "search_results"
    assert result.hits == [{"url": rows[0]["url"], "title": "Paper", "snippet": "Candidate only"}]
    assert str(seen[0].url) == SEARCH_ENDPOINT
    assert json.loads(seen[0].content) == {"query": "论文 exact paper code", "count": 3, "need_content": False}
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert provider.request_count == 1 and provider.remaining_requests == 6
    assert not client.is_closed
    saved = serialized(store, result)
    assert KEY not in saved and "must not" not in saved and "secret_extra" not in saved
    assert KEY not in repr(provider)


def test_fetch_preserves_provider_provenance_and_snapshot_text(factory):
    provider, store, seen, _ = factory()
    result = provider.fetch("https://example.org/paper")
    assert result.status == "ok" and result.http_status == 200
    assert str(seen[0].url) == FETCH_ENDPOINT
    assert json.loads(seen[0].content) == {"url": result.url}
    snap = store.read(result.snapshot_ref)
    assert snap.raw is None
    assert snap.manifest["requested_url"] == result.url
    assert snap.manifest["final_url"] is None
    assert snap.manifest["metadata"]["origin_http_status"] is None
    assert snap.manifest["metadata"]["origin_redirect_chain_verified"] is False
    assert snap.manifest["metadata"]["raw_provider_response_saved"] is False
    assert "Official implementation" in _text_from_data(snap.normalized_json)
    assert result.model_dump()["snapshot_ref"] == result.snapshot_ref


@pytest.mark.parametrize("status,expected", [
    (301, "access_denied"), (302, "access_denied"), (400, "error"), (401, "access_denied"),
    (402, "error"), (403, "access_denied"), (404, "not_found"), (408, "timeout"),
    (413, "error"), (429, "rate_limited"), (500, "error"), (504, "timeout"),
])
def test_http_failures_are_distinct_counted_once_and_do_not_save_error_bodies(factory, status, expected):
    provider, store, seen, _ = factory(lambda _: httpx.Response(
        status, text=f"failure with {KEY}", headers={"Location": "https://private.org/?token=secret"},
    ))
    result = provider.search("paper")
    assert result.status == expected and result.reason_code == f"provider_http_{status}"
    assert result.http_status == status and provider.request_count == 1 and len(seen) == 1
    assert KEY not in serialized(store, result) and "private.org" not in serialized(store, result)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{}', b'{"error":"private"}',
                                  b'{"results":null}', b'{"results":{}}', b'"x"'])
def test_invalid_search_envelopes_are_not_empty_results(factory, body):
    provider, _, _, _ = factory(lambda _: httpx.Response(200, content=body))
    result = provider.search("paper")
    assert result.status == "error" and result.reason_code == "provider_response_invalid"


@pytest.mark.parametrize("body", [{}, {"title": None, "content": "x"}, {"title": "x", "content": []}])
def test_invalid_fetch_fields_are_not_empty_content(factory, body):
    provider, _, _, _ = factory(lambda _: httpx.Response(200, json=body))
    result = provider.fetch("https://example.org/paper")
    assert result.status == "error" and result.reason_code == "provider_response_invalid"


def test_successful_empty_results_are_distinct(factory):
    search, _, _, _ = factory(lambda _: httpx.Response(200, json={"results": []}))
    fetched, _, _, _ = factory(lambda _: httpx.Response(200, json={"title": "", "content": " \n"}))
    assert search.search("paper").reason_code == "search_empty"
    assert search.request_count == 1
    result = fetched.fetch("https://example.org/paper")
    assert result.status == "not_found" and result.reason_code == "fetch_empty_content"


@pytest.mark.parametrize("url", [
    "http://example.org/paper", "https://localhost/p", "https://x.internal/p", "https://x.local/p",
    "https://127.0.0.1/p", "https://10.0.0.1/p", "https://169.254.169.254/p",
    "https://[::1]/p", "https://[::ffff:127.0.0.1]/p", "https://[64:ff9b::7f00:1]/p",
    "https://[2002:7f00:1::]/p", "https://example.org:444/p", "https://example.org./p",
    "https://example.org/%250dprivate", "https://example.org/\\private", "https://a/p\nsecret",
    "https://user:secret@example.org/p", "https://@example.org/p",
    "https://example.org/p?api_key=never-persist", "https://example.org/#access_token=never-persist",
    "https://example.org/p?X-Amz-Signature=never-persist", "https://example.org/p?API%5fKEY=never-persist",
    "https://example.org/p?%2561pi_key=never-persist",
    "https://example.org/p?redirect=https%3A%2F%2Felse.org%2F%3Ftoken%3Dnever-persist",
    "https://example.org/\ud800",
])
def test_unsafe_urls_never_send_or_persist_original_input(factory, url):
    provider, store, seen, _ = factory()
    result = provider.fetch(url)
    assert result.status == "access_denied" and result.url is None
    assert provider.request_count == 0 and seen == []
    assert result.snapshot_ref
    saved = serialized(store, result)
    assert url not in saved and "never-persist" not in saved
    assert store.read(result.snapshot_ref).manifest["requested_url"] == FETCH_ENDPOINT


@pytest.mark.parametrize("addresses,reason", [
    ([PUBLIC, "127.0.0.1"], "target_url_not_public"),
    (["169.254.1.1"], "target_url_not_public"), (["::1"], "target_url_not_public"),
    (["not-an-ip"], "target_dns_unavailable"), ([], "target_dns_unavailable"),
])
def test_dns_requires_all_addresses_public(factory, addresses, reason):
    provider, _, seen, _ = factory(resolver=lambda _: addresses)
    result = provider.fetch("https://public-looking.example/paper")
    assert result.reason_code == reason and provider.request_count == 0 and not seen


def test_dns_exceptions_are_static_and_not_persisted(factory):
    def fail(_):
        raise OSError(f"sensitive diagnostic {KEY}")

    provider, store, seen, _ = factory(resolver=fail)
    result = provider.fetch("https://example.org/paper")
    assert result.status == "error" and result.reason_code == "target_dns_unavailable"
    assert not seen and provider.request_count == 0
    assert "sensitive diagnostic" not in serialized(store, result)


def test_dns_timeout_is_bounded_and_consumes_no_http_budget(factory):
    release = threading.Event()

    def slow(_):
        release.wait(2)
        return [PUBLIC]

    provider, _, seen, _ = factory(resolver=slow, deadline_seconds=0.02)
    start = time.monotonic()
    try:
        result = provider.fetch("https://example.org/paper")
    finally:
        release.set()
    assert time.monotonic() - start < 1
    assert result.reason_code == "target_dns_timeout" and result.status == "timeout"
    assert not seen and provider.request_count == 0


@pytest.mark.parametrize("query", ["", "  ", "a" * 129, "paper\nprivate", "\ud800", KEY,
                                    "paper https://example.org/?token=private"])
def test_invalid_or_secret_queries_are_not_persisted_or_requested(factory, query):
    provider, store, seen, _ = factory()
    result = provider.search(query)
    assert result.status in {"error", "access_denied"} and result.query is None
    assert provider.request_count == 0 and not seen
    assert "private" not in serialized(store, result) and KEY not in serialized(store, result)


@pytest.mark.parametrize("count", [0, 51, True, 1.5, "5"])
def test_invalid_search_counts_are_not_requests(factory, count):
    provider, _, seen, _ = factory()
    assert provider.search("paper", count=count).reason_code == "search_request_invalid"
    assert not seen and provider.request_count == 0


def test_shared_provider_budget_and_missing_key_have_safe_rejection_snapshots(factory):
    provider, store, seen, _ = factory(max_requests=1)
    assert provider.fetch("https://example.org/paper").status == "ok"
    rejected = provider.search("paper")
    assert rejected.status == "error" and rejected.reason_code == "request_budget_exhausted"
    assert provider.request_count == 1 and len(seen) == 1 and store.read(rejected.snapshot_ref)
    empty, _, seen2, _ = factory(api_key="", max_requests=0)
    assert empty.search("paper").reason_code == "provider_key_missing"
    assert not seen2 and empty.request_count == 0


def test_zero_budget_fetch_rejects_before_dns(factory):
    def should_not_resolve(_):
        raise AssertionError("DNS must not run after exhausted budget")

    provider, _, seen, _ = factory(max_requests=0, resolver=should_not_resolve)
    assert provider.fetch("https://example.org/paper").reason_code == "request_budget_exhausted"
    assert not seen


def test_deadline_rejects_without_http(factory):
    provider, _, seen, _ = factory()
    provider._deadline = time.monotonic() - 1
    result = provider.search("paper")
    assert result.status == "timeout" and result.reason_code == "provider_deadline_exceeded"
    assert provider.request_count == 0 and not seen


@pytest.mark.parametrize("error,reason,status", [
    (httpx.ReadTimeout("private"), "provider_timeout", "timeout"),
    (httpx.ConnectError("private"), "provider_transport_error", "error"),
])
def test_transport_failures_count_without_retry_or_exception_leaks(factory, error, reason, status):
    def fail(_):
        raise error

    provider, store, seen, _ = factory(fail)
    result = provider.search("paper")
    assert result.status == status and result.reason_code == reason
    assert provider.request_count == 1 and len(seen) == 1
    assert "private" not in serialized(store, result)


def test_response_byte_budget_stops_before_persisting_body(factory):
    provider, store, _, _ = factory(lambda _: httpx.Response(200, content=b"x" * 2048), max_bytes=1024)
    result = provider.search("paper")
    assert result.status == "error" and result.reason_code == "provider_response_too_large" and result.truncated
    assert "x" * 100 not in serialized(store, result)


def test_compressed_responses_rejected_without_decompression(factory):
    class CompressedStream(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("Compressed body must not be consumed")

    provider, _, _, _ = factory(lambda _: httpx.Response(
        200, stream=CompressedStream(), headers={"Content-Encoding": "gzip"},
    ))
    result = provider.search("paper")
    assert result.reason_code == "provider_encoding_unsupported"


def test_fetch_truncation_and_secret_redaction_keep_safe_usable_text(factory):
    text = (f"{KEY} https://example.org/?api_key=hidden "
            r"https:\/\/example.org\/?access_token=hidden2" + " \x00\ud800" + "x" * 2000)
    provider, store, _, _ = factory(lambda _: httpx.Response(
        200, content=json.dumps({"title": "T" * 1200, "content": text, "private": KEY}).encode(),
    ), max_characters=1000)
    result = provider.fetch("https://example.org/paper")
    assert result.status == "ok" and result.truncated and result.reason_code == "fetch_content_truncated"
    assert len(result.text) == 1000 and len(result.title) == 1000
    saved = serialized(store, result)
    assert KEY not in saved and "hidden" not in saved and "[REDACTED_URL]" in result.text
    assert "\x00" not in result.text and "\ud800" not in result.text


def test_search_filters_unsafe_hits_and_distinguishes_filtered_scope(factory):
    rows = [
        {"url": "https://127.0.0.1/", "title": "private", "snippet": "private"},
        {"url": "https://example.org/?token=hidden", "title": "private", "snippet": "private"},
        {"url": "https://example.org/a", "title": "A", "snippet": f"{KEY} https://x.org/?secret=hidden"},
        {"url": "https://example.org/a", "title": "duplicate", "snippet": "duplicate"},
        {"url": "https://example.org/b", "title": "B"},
    ]
    provider, store, _, _ = factory(lambda _: httpx.Response(200, json={"results": rows}))
    result = provider.search("paper", count=5)
    assert result.status == "ok" and result.reason_code == "search_results_filtered"
    assert len(result.hits) == 1 and result.rejected_result_count == 3 and result.raw_result_count == 5
    assert KEY not in serialized(store, result) and "hidden" not in serialized(store, result)
    empty, _, _, _ = factory(lambda _: httpx.Response(200, json={"results": rows[:2]}))
    result2 = empty.search("paper")
    assert result2.status == "ok" and result2.hits == [] and result2.reason_code == "search_results_filtered"


def test_provider_cannot_oversupply_requested_hit_count(factory):
    rows = [{"url": f"https://example.org/{i}", "title": "A", "snippet": "S"} for i in range(8)]
    provider, _, _, _ = factory(lambda _: httpx.Response(200, json={"results": rows}))
    result = provider.search("paper", count=2)
    assert len(result.hits) == 2 and result.raw_result_count == 8 and result.truncated


def test_streaming_limit_never_buffers_oversized_response(factory):
    consumed = []

    class LargeStream(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(10_000):
                consumed.append(i)
                yield b"x" * 1024

    provider, _, _, _ = factory(lambda _: httpx.Response(200, stream=LargeStream()), max_bytes=1024)
    result = provider.search("paper")
    assert result.reason_code == "provider_response_too_large" and result.truncated
    assert len(consumed) == 2 and provider.request_count == 1


def test_deadline_during_stream_prevents_successful_partial_response(factory):
    provider = None

    class LateStream(httpx.SyncByteStream):
        def __iter__(self):
            provider._deadline = time.monotonic() - 1
            yield b'{"results":[]}'

    provider, _, _, _ = factory(lambda _: httpx.Response(200, stream=LateStream()))
    result = provider.search("paper")
    assert result.status == "timeout" and result.reason_code == "provider_deadline_exceeded"
    assert provider.request_count == 1


def test_explicit_provider_key_in_url_path_is_not_persisted(factory):
    provider, store, seen, _ = factory()
    result = provider.fetch(f"https://example.org/{KEY}")
    assert result.reason_code == "target_url_credentials_rejected" and result.url is None
    assert not seen and KEY not in serialized(store, result)


def test_owned_client_is_closed_even_after_failure(tmp_path, monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429)))
    options = []

    def create(**kwargs):
        options.append(kwargs)
        return client

    monkeypatch.setattr(module.httpx, "Client", create)
    provider = DeepSeekCodeDiscoveryProvider(api_key=KEY, store=SnapshotStore(tmp_path))
    assert provider.search("paper").status == "rate_limited"
    assert client.is_closed and options[0]["trust_env"] is False


@pytest.mark.parametrize("url", [
    r"https\:\/\/example.org\/\?api\_key\=never-persist",
    r"https:\/\/example.org/?access_token=never-persist",
    r"https:\\/\\/example.org/?password=never-persist",
    r"https\\:\\/\\/example.org\\/\\?api\\_key\\=never-persist",
    r"https\:\/\/example.org\/\#client\_secret\=never-persist",
    r"https\:\/\/user\:never-persist\@example.org\/",
    r"https\:\/\/example.org\/\?redirect\=https\:\/\/other.org\/\?token\=never-persist",
])
def test_escaped_provider_credential_urls_are_redacted_before_persistence(factory, url):
    provider, store, _, _ = factory(lambda _: httpx.Response(200, json={
        "title": f"Title {url}", "content": f"Prose {url} end",
    }))
    result = provider.fetch("https://example.org/paper")
    assert result.status == "ok" and "[REDACTED_URL]" in result.text
    assert "never-persist" not in serialized(store, result)
    assert url not in result.text and "never-persist" not in result.title


def test_escaped_search_query_credentials_rejected_without_request_or_input_snapshot(factory):
    provider, store, seen, _ = factory()
    result = provider.search(r"paper https\:\/\/x.org\/\?api\_key\=never-persist")
    assert result.status == "access_denied" and result.reason_code == "search_credentials_rejected"
    assert result.query is None and not seen and provider.request_count == 0
    assert "never-persist" not in serialized(store, result)


def test_noncredential_escaped_markdown_is_not_generally_decoded_by_provider(factory):
    text = r"DataMIL\: \[Code\]\(https\:\/\/github.com\/UT\-Austin\-RobIn\/datamil\)"
    provider, store, _, _ = factory(lambda _: httpx.Response(200, json={"title": "Paper", "content": text}))
    result = provider.fetch("https://example.org/paper")
    assert result.text == text and store.read(result.snapshot_ref).normalized_json["body"] == text


def test_config_key_is_reused_without_customized_chat_endpoint(factory, monkeypatch):
    class Config:
        deepseek_api_key = KEY
        deepseek_base_url = "https://attacker.invalid/chat"

    monkeypatch.setattr(module, "get_settings", lambda: Config())
    provider, _, seen, _ = factory(api_key=None)
    provider.search("paper")
    assert str(seen[0].url) == SEARCH_ENDPOINT


@pytest.mark.parametrize("kwargs", [
    {"max_requests": -1}, {"max_requests": True}, {"max_requests": 41}, {"timeout": 0},
    {"timeout": 61}, {"max_bytes": 100}, {"max_characters": 500}, {"deadline_seconds": 0},
])
def test_constructor_rejects_invalid_limits(factory, kwargs):
    with pytest.raises(ValueError, match="budget"):
        factory(**kwargs)
