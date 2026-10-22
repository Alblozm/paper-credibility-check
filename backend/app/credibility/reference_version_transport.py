"""Bounded, auditable arXiv metadata access for reference-version evidence.

Successful Atom bytes are cached in a verified JSON envelope, not represented
as if the upstream had returned JSON. A separate provider-wide SQLite queue
enforces one request at a time and at least three seconds between starts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import httpx

from app.config import get_settings
from app.credibility.crossref_http import retry_after_seconds
from app.credibility.snapshots import artifact_root, capture_snapshot
from app.storage.crossref_state import CorruptCacheEntryError, CrossrefStateStore, StaleReservationError, Wait

_ENDPOINT = "https://export.arxiv.org/api/query"
_ATOM = "http://www.w3.org/2005/Atom"
_ARXIV = "http://arxiv.org/schemas/atom"
_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v[1-9]\d*)?", re.I | re.ASCII)
_MAX_BYTES = 512 * 1024
_MAX_HTTP_SECONDS = 10.0
_MAX_WAIT_SECONDS = 30.0
_INTERVAL_SECONDS = 3.0
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class VersionFetch:
    url: str
    fetch_status: str
    http_status: int | None
    retrieved_at: str
    is_cached: bool
    snapshot_ref: str
    entries: list[dict] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_url(params: dict[str, str]) -> str:
    if not isinstance(params, dict) or set(params) - {"id_list", "search_query", "max_results"}:
        raise ValueError("Invalid arXiv query parameters.")
    if ("id_list" in params) == ("search_query" in params):
        raise ValueError("Exactly one arXiv query form is required.")
    if any(not isinstance(value, str) for value in params.values()):
        raise ValueError("arXiv query parameters must be strings.")
    count = params.get("max_results", "5")
    if not re.fullmatch(r"[1-5]", count):
        raise ValueError("arXiv max_results must be between one and five.")
    query = {**params, "max_results": count}
    if "id_list" in query:
        ids = query["id_list"].split(",")
        if not 1 <= len(ids) <= 5 or any(not _ID.fullmatch(value) for value in ids):
            raise ValueError("Invalid arXiv identifiers.")
    else:
        search = query["search_query"]
        if not search.strip() or len(search) > 1000 or any(ord(char) < 32 or ord(char) == 127 for char in search):
            raise ValueError("Invalid arXiv search query.")
    return _ENDPOINT + "?" + urlencode(sorted(query.items()))


def _entry_id(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.netloc not in {"arxiv.org", "export.arxiv.org"}
            or parsed.query or parsed.fragment or not parsed.path.startswith("/abs/")):
        raise ValueError("Invalid Atom entry identifier URL.")
    identifier = parsed.path[5:]
    if not _ID.fullmatch(identifier):
        raise ValueError("Invalid Atom entry identifier.")
    return identifier


def _parse_atom(raw: bytes) -> list[dict]:
    if len(raw) > _MAX_BYTES or b"\x00" in raw or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ValueError("Unsafe or oversized Atom response.")
    text = raw.decode("utf-8-sig", errors="strict")
    declaration = re.match(r"\s*<\?xml\s+[^?]*encoding\s*=\s*['\"]([^'\"]+)", text, re.I)
    if declaration and declaration.group(1).casefold() not in {"utf-8", "utf8", "us-ascii", "ascii"}:
        raise ValueError("Unsupported Atom encoding.")
    root = ElementTree.fromstring(text)
    if root.tag != f"{{{_ATOM}}}feed":
        raise ValueError("Invalid Atom namespace or root.")
    nodes = root.findall(f"{{{_ATOM}}}entry")
    if len(nodes) > 5:
        raise ValueError("Atom response exceeds requested entry bound.")
    entries, ids = [], set()
    for node in nodes:
        def content(name: str, node=node) -> str:
            return " ".join((node.findtext(f"{{{_ATOM}}}{name}") or "").split())

        identifier, title = _entry_id(content("id")), content("title")
        authors = [" ".join((author.findtext(f"{{{_ATOM}}}name") or "").split())
                   for author in node.findall(f"{{{_ATOM}}}author")]
        published, updated = content("published"), content("updated")
        if identifier in ids or not title or not authors or not all(authors):
            raise ValueError("Incomplete or duplicate Atom entry.")
        for stamp in (published, updated):
            date = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("Atom date requires a timezone.")
        doi = " ".join((node.findtext(f"{{{_ARXIV}}}doi") or "").split()) or None
        if doi is not None and not re.fullmatch(r"10\.\d{4,9}/\S+", doi, re.I):
            raise ValueError("Invalid Atom DOI.")
        ids.add(identifier)
        entries.append({"id": identifier, "title": title, "authors": authors,
                        "published": published, "updated": updated, "doi": doi})
    return entries


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=_MAX_HTTP_SECONDS, follow_redirects=False, trust_env=False)


def _observe(url, status, code, stamp, *, raw=None, entries=None, cached=False, error=None, truncated=False):
    entries = [] if entries is None else entries
    snapshot_ref = capture_snapshot(
        kind="reference_version_metadata", raw=raw, normalized_json={"entries": entries} if status == "ok" else None,
        requested_url=url or None, final_url=url or None, retrieved_at=stamp, fetch_status=status,
        http_status=code, is_cached=cached, truncated=truncated,
        metadata={"provider": "arxiv", "format": "atom", "error_code": error, "raw_available": raw is not None},
    )
    return VersionFetch(url, status, code, stamp, cached, snapshot_ref, entries)


def _cache_xml(payload: dict) -> bytes:
    if payload.get("format") != "arxiv-atom-v1" or not isinstance(payload.get("raw_b64"), str):
        raise ValueError("Invalid arXiv cache envelope.")
    encoded = payload["raw_b64"]
    if len(encoded) > ((_MAX_BYTES + 2) // 3) * 4:
        raise ValueError("Oversized arXiv cache envelope.")
    raw = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(raw).hexdigest() != payload.get("raw_sha256"):
        raise ValueError("arXiv cache body integrity failure.")
    return raw


def _discard_invalid_envelope(state: CrossrefStateStore, key: str, digest: str) -> None:
    """CAS-evict an invalid inner envelope, preserving a concurrent replacement.

    The shared JSON store validates its outer integrity; only this transport
    knows the enclosed XML format. A correctly hashed but invalid envelope must
    not leave ``reserve`` returning ``cached`` forever.
    """
    with sqlite3.connect(state.state_path, timeout=10) as connection:
        connection.execute("DELETE FROM crossref_cache WHERE query_key=? AND payload_sha256=?", (key, digest))


def fetch_arxiv(params: dict[str, str]) -> VersionFetch:
    """Fetch up to five public Atom records; failures remain immutable evidence."""
    try:
        url = _canonical_url(params)
    except (ValueError, TypeError):
        return _observe("", "error", None, _now(), error="invalid_parameters")
    state = CrossrefStateStore(str(get_settings().crossref_state_path) + ".arxiv-versions.sqlite3",
                              clock=lambda: time.time())
    key = "arxiv-atom-v1:" + hashlib.sha256(url.encode()).hexdigest()
    deadline = time.monotonic() + _MAX_WAIT_SECONDS + _MAX_HTTP_SECONDS
    waited = 0.0
    while True:
        cached = None
        try:
            cached = state.lookup(key)
            if cached is not None:
                raw = _cache_xml(cached.payload)
                entries = _parse_atom(raw)
                if cached.url != url or not cached.retrieved_at or len(entries) > int(params.get("max_results", "5")):
                    raise ValueError("Invalid arXiv cache origin.")
                return _observe(url, "ok", 200, cached.retrieved_at, raw=raw, entries=entries, cached=True)
        except (CorruptCacheEntryError, ValueError, UnicodeError, ElementTree.ParseError, binascii.Error):
            if cached is not None:
                _discard_invalid_envelope(state, key, cached.payload_sha256)
            else:
                state.invalidate(key)
        reservation = state.reserve(key, interval_seconds=_INTERVAL_SECONDS, lease_seconds=30)
        remaining = deadline - time.monotonic()
        if isinstance(reservation, Wait):
            if reservation.reason == "cached":
                continue
            delay = reservation.seconds if reservation.reason == "cooldown" else min(reservation.seconds, 0.5)
            if delay > _MAX_WAIT_SECONDS - waited or delay >= remaining:
                state.abandon(key)
                status = "rate_limited" if reservation.reason == "cooldown" else "timeout"
                return _observe(url, status, None, _now(), error="shared_wait_budget")
            time.sleep(delay)
            waited += delay
            continue
        if remaining <= 0:
            state.cancel(reservation.token)
            return _observe(url, "timeout", None, _now(), error="total_deadline")
        break
    stamp, raw, code, cooldown = _now(), None, None, 0.0
    status, entries, error, truncated = "error", [], "transport_error", False
    chunks = []
    try:
        http_deadline = min(deadline, time.monotonic() + _MAX_HTTP_SECONDS)
        with _new_client() as client:
            with client.stream("GET", url, headers={"User-Agent": "paper-credibility-check-reference-versions/0.1",
                                                  "Accept": "application/atom+xml"},
                               timeout=min(_MAX_HTTP_SECONDS, remaining), follow_redirects=False) as response:
                code = response.status_code
                if str(response.url) != url:
                    raise ValueError("Unexpected arXiv response URL.")
                if code == 429:
                    cooldown = retry_after_seconds(response.headers, time.time())
                    cooldown = 30.0 if cooldown is None else cooldown
                status = {200: "ok", 404: "not_found", 401: "access_denied", 403: "access_denied",
                          429: "rate_limited"}.get(code, "error")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    if time.monotonic() >= http_deadline:
                        raise httpx.TimeoutException("Total arXiv body deadline.")
                    chunks.append(chunk[:max(0, _MAX_BYTES - size)])
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        truncated = True
                        break
                raw = b"".join(chunks)
                if time.monotonic() >= http_deadline:
                    raise httpx.TimeoutException("Total arXiv request deadline.")
                if truncated:
                    status = "error" if status == "ok" else status
                    error = "response_too_large"
                elif status == "ok":
                    entries = _parse_atom(raw)
                    if len(entries) > int(params.get("max_results", "5")):
                        raise ValueError("Atom response exceeds query bound.")
                    error = None
                else:
                    error = "redirect_disallowed" if 300 <= code < 400 else "http_error"
    except httpx.TimeoutException:
        status, error = ("rate_limited" if code == 429 else "timeout"), "timeout"
    except (ValueError, UnicodeError, ElementTree.ParseError):
        status, error = "error", "invalid_atom_or_response"
    except Exception:
        status, error = "error", "transport_error"
    if raw is None and chunks:
        raw = b"".join(chunks)
        truncated = True
    try:
        result = _observe(url, status, code, stamp, raw=raw, entries=entries if status == "ok" else [],
                          error=error, truncated=truncated)
        envelope = ({"format": "arxiv-atom-v1", "raw_b64": base64.b64encode(raw).decode("ascii"),
                     "raw_sha256": hashlib.sha256(raw).hexdigest()} if status == "ok" else None)
        state.finish(reservation.token, payload=envelope, raw=None, http_status=200 if status == "ok" else code,
                     url=url, final_url=url, retrieved_at=stamp, ttl_seconds=_CACHE_TTL_SECONDS,
                     retry_after_seconds=cooldown, snapshot_ref=result.snapshot_ref,
                     artifact_namespace=str(artifact_root().resolve()), interval_seconds=_INTERVAL_SECONDS)
        return result
    finally:
        with suppress(StaleReservationError):
            state.cancel(reservation.token)
