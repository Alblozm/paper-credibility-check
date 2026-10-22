"""Crossref transport privacy, bounds and status tests; all HTTP is injected."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from app.credibility import crossref_http
from app.credibility.crossref_http import CrossrefHTTP, recommended_interval, retry_after_seconds

DOI_URL = "https://api.crossref.org/works/10.1000%2Fexample"
LIST_URL = "https://api.crossref.org/works?query.bibliographic=A+Paper&rows=5"
MAIL = "researcher+project@example.edu"
WORK = {"status": "ok", "message": {"DOI": "10.1000/example", "title": ["A Paper"]}}
LIST = {"status": "ok", "message": {"items": [WORK["message"]]}}


def getter_for(response):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    return get, calls


def response(code=200, *, payload=None, content=None, headers=None, url=DOI_URL):
    kwargs = {"content": content} if content is not None else {"json": WORK if payload is None else payload}
    return httpx.Response(code, request=httpx.Request("GET", url), headers=headers, **kwargs)


def test_valid_response_preserves_real_bytes_and_sends_contact_only_in_header():
    reply = response(
        headers={
            "X-Rate-Limit-Limit": "50",
            "X-Rate-Limit-Interval": "1s",
            "X-Api-Pool": "polite",
            "User-Agent": MAIL,
            "Authorization": "secret",
            "Set-Cookie": "private",
        }
    )
    get, calls = getter_for(reply)
    result = CrossrefHTTP(mailto=MAIL, getter=get).fetch(DOI_URL)
    assert result.fetch_status == "ok" and result.error_code is None
    assert result.raw == reply.content and result.payload == WORK
    assert result.headers == {"x-rate-limit-limit": "50", "x-rate-limit-interval": "1s", "x-api-pool": "polite"}
    assert calls[0][0] == DOI_URL and calls[0][1]["follow_redirects"] is False
    assert calls[0][1]["headers"]["User-Agent"] == f"paper-credibility-check/0.2 (mailto:{MAIL})"
    assert calls[0][1]["timeout"] == pytest.approx(15, abs=0.01)
    assert MAIL not in repr(result)


def test_list_query_and_empty_search_are_valid_messages():
    for payload in (LIST, {"message": {"items": []}}):
        get, _ = getter_for(response(payload=payload, url=LIST_URL))
        assert CrossrefHTTP(getter=get).fetch(LIST_URL).fetch_status == "ok"


@pytest.mark.parametrize(
    "bad",
    [
        "bad\r\nInjected: value@example.org",
        "name @example.org",
        "name@example.org\t",
        "name@example.org\n",
        "name@example.org)",
        "mailto:name@example.org",
        None,
    ],
)
def test_invalid_mailto_does_not_echo_private_value(bad):
    with pytest.raises(ValueError) as captured:
        CrossrefHTTP(mailto=bad)
    assert "example.org" not in str(captured.value)
    assert "Crossref contact address is invalid" in str(captured.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.crossref.org/works",
        "https://crossref.org/works",
        "https://api.crossref.org.evil/works",
        "https://user:password@api.crossref.org/works",
        "https://api.crossref.org:443/works",
        "https://api.crossref.org/works#fragment",
        "https://api.crossref.org/works/",
        "https://api.crossref.org/members",
        "https://api.crossref.org/works/10.1000/a/../b",
        "https://api.crossref.org/works/10.1000%2Fa%2F..%2Fb",
        "https://api.crossref.org/works/10.1000%2Fa%2F%252e%252e%2Fb",
        "https://api.crossref.org/works/10.1000\\evil",
        "https://api.crossref.org/works?mailto=private@example.org",
        "https://api.crossref.org/works?%6dailto=hidden",
        "https://api.crossref.org/works?query=bad\r\nheader",
        "https://api.crossref.org/works/10.1000%2Fwith%20space",
    ],
)
def test_invalid_or_noncanonical_url_never_calls_getter_or_echoes_url(url):
    get, calls = getter_for(response())
    result = CrossrefHTTP(getter=get).fetch(url)
    assert result.error_code == "invalid_url" and result.fetch_status == "error" and not calls
    assert result.url == "" and result.final_url is None


@pytest.mark.parametrize("address", [MAIL, quote(MAIL, safe=""), quote(quote(MAIL, safe=""), safe="")])
def test_contact_cannot_appear_in_query_even_when_encoded(address):
    get, calls = getter_for(response())
    result = CrossrefHTTP(mailto=MAIL, getter=get).fetch("https://api.crossref.org/works?query=" + address)
    assert result.error_code == "invalid_url" and not calls and MAIL not in repr(result)


def test_redirect_outside_crossref_is_rejected_without_forwarding_contact():
    get, calls = getter_for(response(302, headers={"Location": "https://publisher.example.org/landing"}))
    result = CrossrefHTTP(mailto=MAIL, getter=get).fetch(DOI_URL)
    assert len(calls) == 1 and result.error_code == "redirect_disallowed"
    assert result.final_url == DOI_URL and "publisher.example.org" not in repr(result)


def test_safe_same_domain_redirect_and_limit():
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return response(302, headers={"Location": "/works/10.1000%2Fnext"})
        return response(url=url)

    result = CrossrefHTTP(getter=get).fetch(DOI_URL)
    assert result.fetch_status == "ok" and result.final_url.endswith("/10.1000%2Fnext")
    get, calls = getter_for(response(302, headers={"Location": DOI_URL}))
    assert CrossrefHTTP(getter=get).fetch(DOI_URL).error_code == "redirect_limit" and len(calls) == 4


def test_redirects_share_one_total_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(crossref_http, "time", SimpleNamespace(monotonic=lambda: clock.now))
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["timeout"])
        clock.now += 0.6
        return response(302, headers={"Location": "/works/10.1000/next"}, url=url)

    result = CrossrefHTTP(timeout_seconds=1, getter=get).fetch(DOI_URL)
    assert result.fetch_status == "timeout" and result.error_code == "timeout"
    assert calls == pytest.approx([1, 0.4])


def test_continuous_slow_chunks_do_not_extend_total_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(crossref_http, "time", SimpleNamespace(monotonic=lambda: clock.now))
    yielded = []

    class SlowResponse:
        status_code = 200
        headers = {}
        url = DOI_URL

        def iter_bytes(self):
            for chunk in (b'{"message":', b'{"DOI":', b'"10.1000/example"}}'):
                clock.now += 0.6
                yielded.append(chunk)
                yield chunk

    get, _ = getter_for(SlowResponse())
    result = CrossrefHTTP(timeout_seconds=1, getter=get).fetch(DOI_URL)
    assert result.fetch_status == "timeout" and len(yielded) == 2
    assert result.payload is None


@pytest.mark.parametrize("failure", ["late_headers", "slow_chunks", "read_timeout"])
def test_received_429_retains_cooldown_even_when_body_times_out(monkeypatch, failure):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(crossref_http, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class SlowRateLimit:
        status_code = 429
        headers = {"Retry-After": "120", "X-Api-Pool": "polite", "Set-Cookie": "private"}
        url = DOI_URL

        def iter_bytes(self):
            if failure == "read_timeout":
                raise httpx.ReadTimeout("private " + MAIL)
            clock.now += 1.1
            yield b"slow rate limit body"

    def get(*args, **kwargs):
        if failure == "late_headers":
            clock.now += 1.1
        return SlowRateLimit()

    result = CrossrefHTTP(mailto=MAIL, timeout_seconds=1, getter=get).fetch(DOI_URL)
    assert result.fetch_status == "rate_limited" and result.http_status == 429
    assert result.error_code == "body_timeout"
    assert result.headers == {"retry-after": "120", "x-api-pool": "polite"}
    assert result.raw is None and result.payload is None and MAIL not in repr(result)


def test_getter_that_ignores_no_redirect_cannot_archive_another_domain():
    get, _ = getter_for(response(url="https://evil.example.org/works"))
    result = CrossrefHTTP(getter=get).fetch(DOI_URL)
    assert result.error_code == "invalid_response_url" and result.raw is None
    assert "evil.example.org" not in repr(result)


@pytest.mark.parametrize(
    "status,expected,error",
    [
        (429, "rate_limited", "http_rate_limited"),
        (404, "not_found", "http_not_found"),
        (401, "access_denied", "http_access_denied"),
        (403, "access_denied", "http_access_denied"),
        (503, "error", "http_error"),
    ],
)
def test_statuses_are_preserved_without_becoming_empty_success(status, expected, error):
    reply = response(status, content=b"service failure", headers={"Retry-After": "240", "X-Concurrency-Limit": "1"})
    get, _ = getter_for(reply)
    result = CrossrefHTTP(getter=get).fetch(DOI_URL)
    assert result.fetch_status == expected and result.http_status == status and result.error_code == error
    assert result.raw == b"service failure" and result.headers == {"retry-after": "240", "x-concurrency-limit": "1"}


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"message": []},
        {"message": {}},
        {"status": "error", "message": {"DOI": "x"}},
        {"message": {"items": ["not a record"]}},
    ],
)
def test_http_200_requires_crossref_message(payload):
    get, _ = getter_for(response(payload=payload))
    result = CrossrefHTTP(getter=get).fetch(DOI_URL)
    assert result.fetch_status == "error" and result.error_code == "invalid_message"


def test_invalid_json_and_body_size_limit_preserve_actual_failure_bytes():
    reply = response(content=b"<html>not JSON</html>")
    get, _ = getter_for(reply)
    result = CrossrefHTTP(getter=get).fetch(DOI_URL)
    assert result.error_code == "invalid_json" and result.raw == reply.content
    get, _ = getter_for(response(content=b"1234567890"))
    result = CrossrefHTTP(max_bytes=4, getter=get).fetch(DOI_URL)
    assert result.error_code == "response_too_large" and result.raw == b"1234"


def test_minimal_injected_fixture_does_not_fabricate_raw_bytes():
    class Fixture:
        status_code = 200

        def json(self):
            return WORK

    get, _ = getter_for(Fixture())
    result = CrossrefHTTP(getter=get).fetch(DOI_URL)
    assert result.fetch_status == "ok" and result.payload == WORK and result.raw is None


@pytest.mark.parametrize(
    "exception,expected", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "error"), (RuntimeError, "error")]
)
def test_exception_messages_never_leak_contact(exception, expected):
    def get(*args, **kwargs):
        raise exception("private " + MAIL)

    result = CrossrefHTTP(mailto=MAIL, getter=get).fetch(DOI_URL)
    assert result.fetch_status == expected and MAIL not in repr(result)


def test_provider_contact_echo_is_not_archived_and_safe_header_values_are_filtered():
    get, _ = getter_for(
        response(
            payload={"message": {"DOI": "10.1000/example", "contact": MAIL}},
            headers={"X-Rate-Limit-Contact": MAIL, "X-Api-Pool": "polite"},
        )
    )
    result = CrossrefHTTP(mailto=MAIL, getter=get).fetch(DOI_URL)
    assert result.error_code == "private_contact_in_response" and result.raw is None and result.payload is None
    assert result.headers == {"x-api-pool": "polite"} and MAIL not in repr(result)


@pytest.mark.parametrize(
    "value,expected",
    [("120", 120), ("0", 0), ("-1", 0), ("nan", None), ("inf", None), ("-inf", None), ("nonsense", None)],
)
def test_retry_after_numeric_and_malformed_values(value, expected):
    assert retry_after_seconds({"Retry-After": value}, 0) == expected


def test_retry_after_http_date_is_not_clipped_to_a_short_budget():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert retry_after_seconds({"retry-after": "Mon, 19 Oct 2026 00:10:00 GMT"}, now) == 600
    assert retry_after_seconds({"retry-after": "Mon, 19 Oct 2026 00:10:00 GMT"}, now.replace(tzinfo=None)) == 600
    assert retry_after_seconds({"retry-after": "Mon, 19 Oct 2026 00:10:00 GMT"}, float("nan")) is None


@pytest.mark.parametrize(
    "limit,interval,floor,expected",
    [
        ("10", "1s", 0.05, 0.11),
        ("50", "1s", 0.2, 0.2),
        ("2", "500ms", 0, 0.275),
        ("2", "1m", 1, 33),
        ("nan", "1s", 0.2, 0.2),
        ("0", "1s", 0.2, 0.2),
        ("5", "infs", 0.2, 0.2),
    ],
)
def test_interval_is_conservative_and_never_faster_than_fallback(limit, interval, floor, expected):
    assert recommended_interval(
        {"X-Rate-Limit-Limit": limit, "X-Rate-Limit-Interval": interval}, floor
    ) == pytest.approx(expected)


def test_default_transport_streams_through_an_offline_httpx_client(monkeypatch):
    captured = []
    original = httpx.Client

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=WORK)

    monkeypatch.setattr(
        "app.credibility.crossref_http.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler))
    )
    result = CrossrefHTTP(mailto=MAIL).fetch(DOI_URL)
    assert result.fetch_status == "ok" and json.loads(result.raw) == WORK and len(captured) == 1
