from __future__ import annotations

import socket
import sqlite3

import httpx
import pytest

from app.config import get_settings
from app.credibility import reference_publisher_http as transport
from app.credibility.snapshots import read_snapshot

HTML = b'<html><head><title>A paper</title></head><body>A publication</body></html>'


@pytest.fixture(autouse=True)
def clock_and_dns(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(transport.time, "time", lambda: now[0])
    monkeypatch.setattr(transport.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(transport.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(transport.socket, "getaddrinfo",
                        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    return now


def mock_client(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(transport, "_new_client", lambda: client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("url", [
    "http://jmlr.org/papers/v22/test.html", "https://jmlr.org.evil.test/papers/test.html",
    "https://user:password@jmlr.org/papers/test.html", "https://jmlr.org:444/papers/test.html",
    "https://127.0.0.1/test.html", "https://localhost/test.html", "https://jmlr.org/../admin",
    "https://jmlr.org/%2e%2e/admin", "https://jmlr.org/\nheader", "https://jmlr.org/a\\b",
    "https://arxiv.org/search/?query=paper", "https://arxiv.org/abs/2101.00001?next=evil",
    "https://www.aclweb.org/admin", "https://arxiv.org/abs/2101.00001v0",
])
def test_untrusted_urls_never_reach_http(monkeypatch, url):
    mock_client(monkeypatch, lambda request: pytest.fail("Invalid URL reached HTTP"))
    result = transport.fetch_official(url)
    assert result.fetch_status == "error"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_official_host_with_private_dns_never_reaches_http(monkeypatch, address):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
    ])
    mock_client(monkeypatch, lambda request: pytest.fail("Private DNS reached HTTP"))
    assert transport.fetch_official("https://jmlr.org/papers/v22/test.html").fetch_status == "error"


def test_success_cache_preserves_raw_time_across_artifact_roots(monkeypatch, tmp_path):
    calls = []
    mock_client(monkeypatch, lambda request: calls.append(request) or httpx.Response(
        200, content=HTML, headers={"Content-Type": "text/html; charset=utf-8"}))
    first = transport.fetch_official("https://jmlr.org/papers/v22/test.html")
    assert first.fetch_status == "ok" and first.raw == HTML and not first.is_cached
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "new-artifacts"))
    second = transport.fetch_official("https://jmlr.org/papers/v22/test.html")
    assert second.is_cached and second.raw == HTML and second.retrieved_at == first.retrieved_at
    assert len(calls) == 1 and read_snapshot(second.snapshot_ref).raw == HTML


@pytest.mark.parametrize("destination", ["https://evil.test/paper", "http://jmlr.org/papers/test", "https://127.0.0.1/"])
def test_unsafe_redirect_never_followed(monkeypatch, destination):
    calls = []
    mock_client(monkeypatch, lambda request: calls.append(request) or httpx.Response(
        302, headers={"Location": destination}))
    assert transport.fetch_official("https://jmlr.org/papers/v22/test.html").fetch_status == "error"
    assert len(calls) == 1


def test_redirects_are_bounded_revalidated_and_paced(monkeypatch, clock_and_dns):
    requests = []

    def handler(request):
        requests.append((str(request.url), clock_and_dns[0]))
        return httpx.Response(302, headers={"Location": f"/papers/{len(requests)}"})

    mock_client(monkeypatch, handler)
    result = transport.fetch_official("https://jmlr.org/papers/start")
    assert result.fetch_status == "error" and len(requests) == 4
    assert [stamp for _, stamp in requests] == [1000, 1001, 1002, 1003]


def test_same_provider_requests_share_pacing(monkeypatch, clock_and_dns):
    starts = []
    mock_client(monkeypatch, lambda request: starts.append(clock_and_dns[0]) or httpx.Response(
        200, content=HTML, headers={"Content-Type": "text/html"}))
    assert transport.fetch_official("https://jmlr.org/papers/a").fetch_status == "ok"
    assert transport.fetch_official("https://proceedings.mlr.press/v9/test.html").fetch_status == "ok"
    assert starts == [1000, 1001]


@pytest.mark.parametrize("body", [
    b'<html><title>Just a moment...</title>cf-chl-challenge</html>', b'x' * (2097152 + 1),
])
def test_challenge_and_oversized_html_never_success_cached(monkeypatch, body):
    mock_client(monkeypatch, lambda request: httpx.Response(200, content=body, headers={"Content-Type": "text/html"}))
    result = transport.fetch_official("https://jmlr.org/papers/v22/test.html")
    assert result.fetch_status == "error"
    with sqlite3.connect(str(get_settings().crossref_state_path) + ".publisher.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM crossref_cache").fetchone()[0] == 0


def test_retry_after_cooldown_is_persistent_without_failure_cache(monkeypatch, clock_and_dns):
    calls = []
    mock_client(monkeypatch, lambda request: calls.append(request) or httpx.Response(
        429, content=b"Slow down", headers={"Retry-After": "120"}))
    first = transport.fetch_official("https://jmlr.org/papers/a")
    second = transport.fetch_official("https://jmlr.org/papers/b")
    assert first.fetch_status == second.fetch_status == "rate_limited" and len(calls) == 1
    assert clock_and_dns[0] == 1000
    assert read_snapshot(first.snapshot_ref).raw == b"Slow down"


def test_pdf_raw_bytes_are_kept(monkeypatch):
    body = b'%PDF-1.4\nnot a real paper, transport does not confirm identity'
    mock_client(monkeypatch, lambda request: httpx.Response(200, content=body,
                                                         headers={"Content-Type": "application/pdf"}))
    result = transport.fetch_official("https://cdn.openai.com/report.pdf")
    assert result.fetch_status == "ok" and result.raw == body
    assert read_snapshot(result.snapshot_ref).raw == body


def test_actual_transport_pins_dns_address_and_retains_tls_identity(monkeypatch):
    requests = []

    def underlying(transport_instance, request):
        requests.append(request)
        return httpx.Response(200, content=HTML, headers={"Content-Type": "text/html"})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", underlying)
    result = transport.fetch_official("https://jmlr.org/papers/test.html")
    assert result.fetch_status == "ok" and result.final_url == "https://jmlr.org/papers/test.html"
    assert len(requests) == 1 and requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "jmlr.org"
    assert requests[0].extensions["sni_hostname"] == "jmlr.org"


def test_last_redirect_start_is_persistently_paced_before_next_key(monkeypatch, clock_and_dns):
    starts = []

    def handler(request):
        starts.append((request.url.path, clock_and_dns[0]))
        if request.url.path == '/papers/start':
            return httpx.Response(302, headers={'Location': '/papers/final'})
        return httpx.Response(200, content=HTML, headers={'Content-Type': 'text/html'})

    mock_client(monkeypatch, handler)
    first = transport.fetch_official('https://jmlr.org/papers/start')
    assert first.fetch_status == 'ok'
    second = transport.fetch_official('https://jmlr.org/papers/another')
    assert second.fetch_status == 'ok'
    assert starts == [('/papers/start', 1000), ('/papers/final', 1001), ('/papers/another', 1002)]


def test_last_redirect_spacing_survives_snapshot_failure(monkeypatch, clock_and_dns):
    starts = []

    def handler(request):
        starts.append((request.url.path, clock_and_dns[0]))
        if request.url.path == '/papers/start':
            return httpx.Response(302, headers={'Location': '/papers/final'})
        return httpx.Response(200, content=HTML, headers={'Content-Type': 'text/html'})

    mock_client(monkeypatch, handler)
    original = transport.capture_snapshot
    monkeypatch.setattr(transport, 'capture_snapshot', lambda **kwargs: (_ for _ in ()).throw(OSError('disk error')))
    with pytest.raises(OSError, match='disk error'):
        transport.fetch_official('https://jmlr.org/papers/start')
    monkeypatch.setattr(transport, 'capture_snapshot', original)
    assert transport.fetch_official('https://jmlr.org/papers/another').fetch_status == 'ok'
    assert starts == [('/papers/start', 1000), ('/papers/final', 1001), ('/papers/another', 1002)]
