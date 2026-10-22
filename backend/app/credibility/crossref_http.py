"""Bounded Crossref-only HTTP transport. No cache, snapshots or retries.

The optional contact address is sent ONLY in User-Agent. Untrusted transport
exceptions are reduced to fixed codes; they are never copied into artifacts.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import httpx


@dataclass(frozen=True)
class CrossrefResponse:
    url: str
    final_url: str | None
    retrieved_at: str
    fetch_status: str
    http_status: int | None
    payload: object = None
    raw: bytes | None = None
    headers: dict[str, str] | None = None
    error_code: str | None = None


def _header_values(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).casefold(): str(value) for key, value in headers.items()}


def retry_after_seconds(headers: Mapping[str, str], now: datetime | float) -> float | None:
    """Return an unshortened server delay; malformed/nonfinite values are unknown."""
    value = _header_values(headers).get("retry-after")
    if value is None:
        return None
    try:
        delay = float(value)
    except (ValueError, TypeError, OverflowError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if isinstance(now, datetime):
                stamp = (now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)).timestamp()
            else:
                stamp = float(now)
            if not math.isfinite(stamp):
                return None
            delay = deadline.timestamp() - stamp
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


def recommended_interval(headers: Mapping[str, str], fallback: float) -> float:
    """Use interval/limit with 10% headroom, never faster than the caller's floor."""
    if isinstance(fallback, bool) or not math.isfinite(fallback) or fallback < 0:
        raise ValueError("Crossref fallback interval must be finite and nonnegative.")
    values = _header_values(headers)
    try:
        limit = float(values.get("x-rate-limit-limit", ""))
        raw = values.get("x-rate-limit-interval", "")
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*", raw, re.I)
        if match is None:
            return fallback
        unit = (match[2] or "s").casefold()
        interval = float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
        if not math.isfinite(limit) or not math.isfinite(interval) or min(limit, interval) <= 0:
            return fallback
        proposed = interval / limit * 1.1
        return max(fallback, proposed) if math.isfinite(proposed) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


class CrossrefHTTP:
    def __init__(self, mailto: str = "", timeout_seconds: float = 15, max_bytes: int = 2_000_000, getter=None):
        # Parentheses/controls cannot escape the User-Agent contact component.
        if not isinstance(mailto, str) or (
            mailto
            and (
                len(mailto) > 254
                or re.fullmatch(
                    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
                    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",
                    mailto,
                )
                is None
                or any(part in mailto for part in ("..", "@.", ".@"))
            )
        ):
            raise ValueError("Crossref contact address is invalid.")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Crossref timeout must be finite and positive.")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("Crossref body limit must be a positive integer.")
        self._mailto = mailto
        self.timeout_seconds, self.max_bytes = timeout_seconds, max_bytes
        self.getter = getter

    def _valid_url(self, url: str) -> bool:
        try:
            if (
                not isinstance(url, str)
                or not url.isascii()
                or any(character.isspace() or ord(character) < 32 for character in url)
                or "\\" in url
                or re.search(r"%(?![0-9a-fA-F]{2})", url)
            ):
                return False
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "api.crossref.org"
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.port is not None
            ):
                return False
            decoded = url
            for _ in range(4):
                if self._mailto and self._mailto.casefold() in decoded.casefold():
                    return False
                expanded = unquote(decoded, errors="strict")
                if expanded == decoded:
                    break
                decoded = expanded
            if any(key.casefold() in {"mailto", "email"} for key, _ in parse_qsl(parsed.query)):
                return False
            if parsed.path == "/works":
                return True
            if not parsed.path.startswith("/works/") or parsed.query:
                return False
            doi = unquote(parsed.path[len("/works/") :], errors="strict")
            return bool(
                re.fullmatch(r"10\.\d{4,9}/\S+", doi)
                and "%" not in doi
                and "\\" not in doi
                and not any(ord(c) < 32 for c in doi)
                and all(part not in {".", "..", ""} for part in doi.split("/"))
            )
        except (TypeError, ValueError, UnicodeError):
            return False

    def _safe_headers(self, headers) -> dict[str, str]:
        if not isinstance(headers, Mapping):
            return {}
        retained = {}
        for key, value in headers.items():
            key, value = str(key).casefold(), str(value)
            if not (key.startswith("x-rate-limit") or key in {"x-concurrency-limit", "retry-after", "x-api-pool"}):
                continue
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                continue
            if self._mailto and self._mailto.casefold() in (key + unquote(value)).casefold():
                continue
            retained[key] = value
        return retained

    def fetch(self, canonical_url: str) -> CrossrefResponse:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if not self._valid_url(canonical_url):
            # Never echo a rejected URL: it might contain credentials/contact.
            return CrossrefResponse("", None, stamp, "error", None, headers={}, error_code="invalid_url")
        target = canonical_url
        deadline = time.monotonic() + self.timeout_seconds
        headers = {
            "User-Agent": "paper-credibility-check/0.2" + (f" (mailto:{self._mailto})" if self._mailto else ""),
            "Accept": "application/json",
        }
        try:
            if self.getter is not None:
                for _ in range(4):
                    response = self.getter(
                        target, headers=headers, timeout=self._remaining(deadline), follow_redirects=False
                    )
                    result = self._consume(canonical_url, target, stamp, response, deadline)
                    if isinstance(result, CrossrefResponse):
                        return result
                    target = result
            else:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False, trust_env=False) as client:
                    for _ in range(4):
                        with client.stream(
                            "GET", target, headers=headers, timeout=self._remaining(deadline)
                        ) as response:
                            result = self._consume(canonical_url, target, stamp, response, deadline)
                        if isinstance(result, CrossrefResponse):
                            return result
                        target = result
        except httpx.TimeoutException:
            return CrossrefResponse(canonical_url, target, stamp, "timeout", None, headers={}, error_code="timeout")
        except Exception:
            return CrossrefResponse(
                canonical_url, target, stamp, "error", None, headers={}, error_code="transport_error"
            )
        return CrossrefResponse(canonical_url, target, stamp, "error", None, headers={}, error_code="redirect_limit")

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise httpx.TimeoutException("Crossref total request deadline exhausted.")
        return remaining

    def _consume(self, original: str, target: str, stamp: str, response, deadline: float) -> CrossrefResponse | str:
        code = response.status_code
        try:
            actual = str(response.url)
        except (AttributeError, RuntimeError):
            actual = target  # Minimal injected responses can omit a Request.
        if not self._valid_url(actual):
            return CrossrefResponse(
                original, target, stamp, "error", code, headers={}, error_code="invalid_response_url"
            )
        safe_headers = self._safe_headers(getattr(response, "headers", {}))
        try:
            # A response/redirect arriving after the total budget cannot restart it.
            self._remaining(deadline)
            return self._consume_body(original, actual, stamp, response, deadline, safe_headers)
        except httpx.TimeoutException:
            if code != 429:
                raise
            # A received 429 remains authoritative even when its body is late.
            # Persisting these headers lets the caller honor the real cooldown.
            return CrossrefResponse(
                original, actual, stamp, "rate_limited", 429, headers=safe_headers, error_code="body_timeout"
            )

    def _consume_body(
        self, original: str, actual: str, stamp: str, response, deadline: float, safe_headers: dict[str, str]
    ) -> CrossrefResponse | str:
        code = response.status_code
        if code in {301, 302, 303, 307, 308}:
            location = getattr(response, "headers", {}).get("location", "")
            redirect = urljoin(actual, location)
            if not location or not self._valid_url(redirect):
                return CrossrefResponse(
                    original, actual, stamp, "error", code, headers=safe_headers, error_code="redirect_disallowed"
                )
            return redirect
        status = {200: "ok", 429: "rate_limited", 404: "not_found", 401: "access_denied", 403: "access_denied"}.get(
            code, "error"
        )
        raw, oversized = self._read_raw(response, deadline)
        if self._mailto and raw and self._mailto.casefold() in raw.decode("utf-8", errors="replace").casefold():
            return CrossrefResponse(
                original, actual, stamp, "error", code, headers=safe_headers, error_code="private_contact_in_response"
            )
        if oversized:
            return CrossrefResponse(
                original,
                actual,
                stamp,
                status if status != "ok" else "error",
                code,
                raw=raw,
                headers=safe_headers,
                error_code="response_too_large",
            )
        payload = None
        try:
            payload = json.loads(raw) if raw is not None else response.json()
        except (ValueError, TypeError, AttributeError, UnicodeError):
            if status == "ok":
                return CrossrefResponse(
                    original, actual, stamp, "error", code, raw=raw, headers=safe_headers, error_code="invalid_json"
                )
        self._remaining(deadline)
        if self._mailto and self._mailto.casefold() in json.dumps(payload, ensure_ascii=False).casefold():
            return CrossrefResponse(
                original, actual, stamp, "error", code, headers=safe_headers, error_code="private_contact_in_response"
            )
        if status == "ok" and not self._valid_message(payload, list_query=urlsplit(actual).path == "/works"):
            return CrossrefResponse(
                original,
                actual,
                stamp,
                "error",
                code,
                payload=payload,
                raw=raw,
                headers=safe_headers,
                error_code="invalid_message",
            )
        errors = {
            "rate_limited": "http_rate_limited",
            "not_found": "http_not_found",
            "access_denied": "http_access_denied",
            "error": "http_error",
        }
        return CrossrefResponse(
            original,
            actual,
            stamp,
            status,
            code,
            payload=payload,
            raw=raw,
            headers=safe_headers,
            error_code=errors.get(status),
        )

    def _read_raw(self, response, deadline: float) -> tuple[bytes | None, bool]:
        iterator = getattr(response, "iter_bytes", None)
        if callable(iterator):
            chunks, total = [], 0
            stream = iter(iterator())
            while True:
                self._remaining(deadline)
                try:
                    chunk = next(stream)
                except StopIteration:
                    self._remaining(deadline)
                    break
                self._remaining(deadline)
                if not isinstance(chunk, bytes):
                    raise TypeError("Invalid response byte stream")
                remaining = self.max_bytes - total
                chunks.append(chunk[:remaining])
                total += len(chunk)
                if total > self.max_bytes:
                    return b"".join(chunks), True
            return b"".join(chunks), False
        raw = getattr(response, "content", None)
        self._remaining(deadline)
        if isinstance(raw, bytes):
            return raw[: self.max_bytes], len(raw) > self.max_bytes
        # Legacy injected fixtures provide only json(); never fabricate raw bytes.
        return None, False

    @staticmethod
    def _valid_message(payload: object, *, list_query: bool) -> bool:
        if not isinstance(payload, dict) or payload.get("status", "ok") != "ok":
            return False
        message = payload.get("message")
        if not isinstance(message, dict):
            return False
        if list_query:
            return isinstance(message.get("items"), list) and all(isinstance(item, dict) for item in message["items"])
        return bool(isinstance(message.get("DOI"), str) and message["DOI"].strip() and "items" not in message)
