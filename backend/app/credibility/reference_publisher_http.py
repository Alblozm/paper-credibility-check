"""Bounded official publication fetching with persistent pacing and raw evidence.

Only named scholarly hosts and public HTTPS paths are accepted. Redirects pass
exactly the same URL and DNS checks as initial requests. HTML/index responses
are evidence of retrieval only; callers must independently verify paper identity.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import re
import socket
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from app.config import get_settings
from app.credibility.crossref_http import retry_after_seconds
from app.credibility.snapshots import artifact_root, capture_snapshot
from app.storage.crossref_state import CorruptCacheEntryError, CrossrefStateStore, StaleReservationError, Wait

PUBLISHER_HTTP_VERSION = "official-publication-http-1.1"
_MAX_HTML = 2 * 1024 * 1024
_MAX_PDF = 12 * 1024 * 1024
_SECONDS = 15.0
_WAIT = 30.0
_TTL = 7 * 24 * 60 * 60
_HOSTS = {
    "aclanthology.org", "www.aclweb.org", "aclweb.org", "proceedings.mlr.press", "jmlr.org", "www.jmlr.org",
    "papers.nips.cc", "papers.neurips.cc", "proceedings.neurips.cc", "neurips.cc",
    "arxiv.org", "export.arxiv.org", "www.arxiv.org", "openreview.net",
    "www.isca-archive.org", "isca-archive.org", "cdn.openai.com", "cdn.openai.com.cdn.cloudflare.net",
    "dl.acm.org", "ieeexplore.ieee.org", "link.springer.com", "www.nature.com", "nature.com",
    "www.sciencedirect.com", "onlinelibrary.wiley.com", "direct.mit.edu", "www.cambridge.org",
    "epubs.siam.org", "academic.oup.com", "proceedings.scipy.org",
}
_ARXIV_ID = re.compile(r"/(?:abs|html)/(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v[1-9]\d*)?/?", re.I)
_CHALLENGE = re.compile(
    r"<title[^>]*>\s*(?:just a moment|attention required|access denied|checking your browser|robot check)"
    r"|(?:cf-chl-|challenge-platform/|g-recaptcha|verify you are human)", re.I,
)


@dataclass(frozen=True)
class PublisherFetch:
    url: str
    final_url: str
    fetch_status: str
    http_status: int | None
    retrieved_at: str
    is_cached: bool
    snapshot_ref: str
    raw: bytes | None
    content_type: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_url(url: str) -> str:
    """Reject arbitrary hosts, credentials, non-HTTPS, controls and odd ports."""
    if not isinstance(url, str) or len(url) > 3000 or any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValueError("Invalid publication URL.")
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    if (parts.scheme != "https" or host not in _HOSTS or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) or "\\" in url or not parts.path.startswith("/")):
        raise ValueError("Publication URL is outside the official-source scope.")
    path = unquote(parts.path)
    if any(ord(char) < 32 for char in path) or any(part in {"..", "."} for part in path.split("/")):
        raise ValueError("Invalid publication path.")
    if host in {"arxiv.org", "export.arxiv.org", "www.arxiv.org"}:
        if not _ARXIV_ID.fullmatch(path) or parts.query:
            raise ValueError("Only fixed arXiv abstract/HTML pages are accepted.")
    if host in {"www.aclweb.org", "aclweb.org"} and not path.startswith("/anthology/"):
        raise ValueError("Only ACL Anthology paths are accepted on legacy hosts.")
    # Fragment identifies an in-page section, never a distinct server resource.
    return urlunsplit(("https", host, parts.path, parts.query, ""))


def _public_dns(host: str) -> list[str]:
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not records:
        raise ValueError("Publication host did not resolve.")
    addresses = []
    for record in records:
        address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Publication host resolved to a non-public address.")
        addresses.append(str(address))
    return sorted(set(addresses), key=lambda value: (":" in value, value))


class _PinnedOfficialTransport(httpx.HTTPTransport):
    """Connect to a validated public address, retaining original TLS SNI/Host.

    Validation followed by a second uncontrolled DNS lookup would permit DNS
    rebinding. The underlying connection therefore receives the actual checked
    IP literal, while HTTPS certificate verification still uses the named host.
    """
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        canonical_url(str(request.url))
        host = request.url.host
        address = _public_dns(host)[0]
        pinned = httpx.Request(request.method, request.url.copy_with(host=address),
                               headers={**dict(request.headers), "host": host}, stream=request.stream,
                               extensions={**request.extensions, "sni_hostname": host})
        return super().handle_request(pinned)


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=_SECONDS, follow_redirects=False, trust_env=False,
                        transport=_PinnedOfficialTransport())


def _observation(url, final_url, status, code, stamp, *, raw=None, content_type="", cached=False,
                 error=None, truncated=False):
    ref = capture_snapshot(
        kind="reference_official_publication", raw=raw, requested_url=url or None, final_url=final_url or None,
        retrieved_at=stamp, fetch_status=status, http_status=code, is_cached=cached, truncated=truncated,
        metadata={"provider": "official_publication", "content_type": content_type,
                  "error_code": error, "raw_available": raw is not None},
    )
    return PublisherFetch(url, final_url, status, code, stamp, cached, ref, raw, content_type)


def _body(raw: bytes, content_type: str) -> None:
    if raw.startswith(b"%PDF-"):
        if len(raw) > _MAX_PDF:
            raise ValueError("Oversized publication PDF.")
        return
    if len(raw) > _MAX_HTML or b"\x00" in raw:
        raise ValueError("Oversized or invalid HTML publication response.")
    if "html" not in content_type and not re.search(br"<(?:!doctype\s+html|html|head|title|meta)\b", raw[:8192], re.I):
        raise ValueError("Publication response is not HTML or PDF.")
    if _CHALLENGE.search(raw.decode("utf-8", "replace")):
        raise ValueError("Publication response is an access challenge.")


def _cache_body(payload: dict, url: str) -> tuple[bytes, str, str]:
    if payload.get("format") != "official-publication-v1" or not isinstance(payload.get("raw_b64"), str):
        raise ValueError("Invalid publication cache envelope.")
    if payload.get("url") != url or len(payload["raw_b64"]) > ((_MAX_PDF + 2) // 3) * 4:
        raise ValueError("Invalid publication cache origin or size.")
    raw = base64.b64decode(payload["raw_b64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != payload.get("raw_sha256"):
        raise ValueError("Publication cache integrity failure.")
    final = canonical_url(payload["final_url"])
    content_type = str(payload.get("content_type", ""))
    _body(raw, content_type)
    return raw, final, content_type


def _evict(state: CrossrefStateStore, key: str, digest: str) -> None:
    with sqlite3.connect(state.state_path, timeout=10) as connection:
        connection.execute("DELETE FROM crossref_cache WHERE query_key=? AND payload_sha256=?", (key, digest))


def fetch_official(url: str) -> PublisherFetch:
    """Read an official page/PDF; a successful fetch alone never verifies a citation."""
    try:
        url = canonical_url(url)
    except (ValueError, TypeError):
        return _observation("", "", "error", None, _now(), error="url_outside_official_scope")
    # One shared provider store intentionally uses a stricter single queue across
    # hosts, so it also enforces each host's minimum one-second start interval.
    state = CrossrefStateStore(str(get_settings().crossref_state_path) + ".publisher.sqlite3",
                              clock=lambda: time.time(), max_raw_bytes=None)
    key = "official-publication-v1:" + hashlib.sha256(url.encode()).hexdigest()
    end, waited = time.monotonic() + _WAIT + _SECONDS, 0.0
    while True:
        cached = None
        try:
            cached = state.lookup(key)
            if cached is not None:
                raw, final, content_type = _cache_body(cached.payload, url)
                if not cached.retrieved_at:
                    raise ValueError("Missing publication retrieval time.")
                return _observation(url, final, "ok", 200, cached.retrieved_at, raw=raw,
                                    content_type=content_type, cached=True)
        except (CorruptCacheEntryError, ValueError, KeyError, TypeError, binascii.Error):
            if cached is not None:
                _evict(state, key, cached.payload_sha256)
            else:
                state.invalidate(key)
        reservation = state.reserve(key, interval_seconds=1, lease_seconds=60)
        if isinstance(reservation, Wait):
            if reservation.reason == "cached":
                continue
            delay = reservation.seconds if reservation.reason == "cooldown" else min(reservation.seconds, 0.5)
            if delay > _WAIT - waited or time.monotonic() + delay >= end:
                state.abandon(key)
                return _observation(url, url, "rate_limited" if reservation.reason == "cooldown" else "timeout",
                                    None, _now(), error="shared_wait_budget")
            time.sleep(delay)
            waited += delay
            continue
        break
    stamp, final, status, code, raw, content_type = _now(), url, "error", None, None, ""
    error, cooldown, truncated = "transport_error", 0.0, False
    chunks = []
    last_started = None
    try:
        deadline = min(end, time.monotonic() + _SECONDS)
        with _new_client() as client:
            for redirects in range(4):
                final = canonical_url(final)
                if last_started is not None:
                    delay = max(0.0, 1.0 - (time.monotonic() - last_started))
                    if time.monotonic() + delay >= deadline:
                        raise httpx.TimeoutException("Publication redirect pacing deadline.")
                    time.sleep(delay)
                _public_dns(urlsplit(final).hostname)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.TimeoutException("Publication request deadline.")
                last_started = time.monotonic()
                with client.stream("GET", final, headers={"User-Agent": "paper-credibility-check-references/0.1",
                                                          "Accept": "text/html,application/pdf;q=0.9"},
                                   timeout=min(_SECONDS, remaining), follow_redirects=False) as response:
                    code = response.status_code
                    if canonical_url(str(response.url)) != final:
                        raise ValueError("Unexpected publication response URL.")
                    if code in {301, 302, 303, 307, 308}:
                        if redirects == 3 or not response.headers.get("location"):
                            raise ValueError("Publication redirect bound exceeded.")
                        final = canonical_url(urljoin(final, response.headers["location"]))
                        continue
                    status = {200: "ok", 404: "not_found", 401: "access_denied", 403: "access_denied",
                              429: "rate_limited"}.get(code, "error")
                    if code == 429:
                        cooldown = retry_after_seconds(response.headers, time.time())
                        cooldown = 30.0 if cooldown is None else cooldown
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
                    limit = _MAX_PDF if "pdf" in content_type or urlsplit(final).path.casefold().endswith(".pdf") \
                        else _MAX_HTML
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() >= deadline:
                            raise httpx.TimeoutException("Publication body deadline.")
                        chunks.append(chunk[:max(0, limit - size)])
                        size += len(chunk)
                        if size > limit:
                            truncated = True
                            break
                    raw = b"".join(chunks)
                    if time.monotonic() >= deadline:
                        raise httpx.TimeoutException("Publication body deadline.")
                    if truncated:
                        raise ValueError("Publication response too large.")
                    if status == "ok":
                        _body(raw, content_type)
                        error = None
                    else:
                        error = "http_error"
                    break
    except httpx.TimeoutException:
        status, error = ("rate_limited" if code == 429 else "timeout"), "timeout"
    except (ValueError, OSError):
        status, error = ("rate_limited" if code == 429 else "error"), "unsafe_or_invalid_publication_response"
    except Exception:
        status, error = "error", "transport_error"
    if raw is None and chunks:
        raw, truncated = b"".join(chunks), True
    # The queue reservation records the first network start, but an allowed
    # redirect may start later. Persist the last actual start's remaining
    # spacing before publishing evidence or releasing this lease; the next
    # worker must not send immediately after a quick redirect response.
    spacing = max(0.0, 1.0 - (time.monotonic() - last_started)) if last_started is not None else 0.0
    release_cooldown = max(cooldown, spacing)
    cooldown_until = time.monotonic() + release_cooldown
    try:
        state.update_limits(retry_after_seconds=release_cooldown)
        result = _observation(url, final, status, code, stamp, raw=raw, content_type=content_type,
                              error=error, truncated=truncated)
        envelope = {"format": "official-publication-v1", "url": url, "final_url": final,
                    "content_type": content_type, "raw_b64": base64.b64encode(raw).decode(),
                    "raw_sha256": hashlib.sha256(raw).hexdigest()} if status == "ok" else None
        state.finish(reservation.token, payload=envelope, raw=None, http_status=200 if status == "ok" else code,
                     url=url, final_url=final, retrieved_at=stamp, ttl_seconds=_TTL,
                     retry_after_seconds=max(0.0, cooldown_until - time.monotonic()),
                     snapshot_ref=result.snapshot_ref,
                     artifact_namespace=str(artifact_root().resolve()), interval_seconds=1)
        return result
    finally:
        with suppress(StaleReservationError):
            state.cancel(reservation.token)
