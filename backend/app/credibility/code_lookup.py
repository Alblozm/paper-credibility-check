"""Bounded public GETs for task three; no repository code is executed."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.config import get_settings
from app.credibility.snapshots import capture_snapshot, derive_snapshot, read_snapshot


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


TREE_MAX_BYTES = 8_000_000


@dataclass(frozen=True)
class Fetch:
    url: str
    status: str
    http_status: int | None
    retrieved_at: str
    text: str = ""
    data: object = None
    cached: bool = False
    has_next: bool = False
    snapshot_ref: str | None = None


class PublicCodeLookup:
    """Process-local TTL/LRU cache and serialized rate limiting, shared by jobs.

    Redirect targets are checked again. Private hosts, credentials and non-HTTPS
    URLs are rejected. Responses and pagination are bounded by the caller.
    """

    def __init__(self, *, interval: float = 0.25, timeout: float = 5, max_bytes: int = 1_000_000):
        self.interval, self.timeout, self.max_bytes = interval, timeout, max_bytes
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, str], tuple[float, Fetch]] = OrderedDict()
        self._last = 0.0
        self._cooldown: dict[str, float] = {}
        self._public_repositories: set[str] = set()

    @staticmethod
    def _public(url: str) -> bool:
        try:
            p = urlsplit(url)
            if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
                return False
            addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
            return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
        except (ValueError, OSError):
            return False

    def clear_failed_cache(self) -> None:
        """Allow explicit recovery to retry failures without discarding good reads."""
        with self._lock:
            for key in [key for key, (_, result) in self._cache.items() if result.status != "ok"]:
                self._cache.pop(key, None)
            self._cooldown.clear()

    def restore_public_repository_proof(self, snapshot_ref: str) -> bool:
        """Restore public-only authorization from a verified archived API response.

        Source wrappers may point to the actual response observation. No network
        is used, and an owner/repo string is never accepted as proof on its own.
        """
        saved = read_snapshot(snapshot_ref)
        while saved.manifest.get("kind") != "public_code_response":
            if saved.manifest.get("kind") != "repository" or not saved.manifest.get("parent_ref"):
                return False
            saved = read_snapshot(saved.manifest["parent_ref"])
        manifest = saved.manifest
        if (manifest.get("fetch_status") != "ok" or manifest.get("http_status") != 200
                or manifest.get("truncated")):
            return False
        requested, final = manifest.get("requested_url"), manifest.get("final_url")
        if not isinstance(requested, str) or not isinstance(final, str):
            return False
        if not self._github_api(requested) or not self._github_api(final):
            return False
        requested_parts, final_parts = urlsplit(requested), urlsplit(final)
        if requested_parts.query or requested_parts.fragment or final_parts.query or final_parts.fragment:
            return False
        original_path = self._repository_path(requested)
        if original_path is None or original_path[3] is not None:
            return False
        data = saved.normalized_json
        if not isinstance(data, dict) or data.get("private") is not False or saved.raw is None:
            return False
        try:
            if json.loads(saved.raw) != data:
                return False
        except (ValueError, UnicodeError):
            return False
        full_name = data.get("full_name")
        if (not isinstance(full_name, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name,
        ) or any(part in {".", ".."} for part in full_name.split("/"))):
            return False
        final_named = self._repository_path(final)
        final_numeric = re.fullmatch(r"/repositories/([0-9]+)", final_parts.path)
        if final_named and final_named[3] is None:
            if f"{final_named[1]}/{final_named[2]}".casefold() != full_name.casefold():
                return False
        elif final_numeric:
            identifier = data.get("id")
            if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier != int(final_numeric[1]):
                return False
        else:
            return False
        aliases = {f"{original_path[1]}/{original_path[2]}".casefold(), full_name.casefold()}
        if any(part in {".", ".."} for alias in aliases for part in alias.split("/")):
            return False
        with self._lock:
            self._public_repositories.update(aliases)
        return True

    @staticmethod
    def _github_api(url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https" and parsed.hostname == "api.github.com"
            and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
        )

    @staticmethod
    def _repository_path(url: str):
        path = urlsplit(url).path
        # httpx and upstream routers can normalize dot segments before sending.
        # Reject them before deciding whether this path may carry credentials.
        if any(part in {".", ".."} for part in unquote(path).split("/")):
            return None
        return re.fullmatch(r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(/.*)?", path)

    def _can_authenticate(self, url: str) -> bool:
        if not self._github_api(url):
            return False
        matched = self._repository_path(url)
        if not matched:
            return False
        repository = f"{matched[1]}/{matched[2]}".casefold()
        allowed = matched[3] is None or repository in self._public_repositories
        return allowed and bool(get_settings().github_token.get_secret_value().strip())

    def _quota_key(self, url: str, authenticated: bool) -> str:
        host = urlsplit(url).hostname or ""
        if self._github_api(url):
            return host + (":authenticated" if authenticated else ":anonymous")
        return host

    def get(self, url: str) -> Fetch:
        with self._lock:
            stamp = time.monotonic()
            quota_key = self._quota_key(url, self._can_authenticate(url))
            cache_key = (url, quota_key)
            entry = self._cache.get(cache_key)
            if entry and entry[0] > stamp:
                self._cache.move_to_end(cache_key)
                return replace(
                    entry[1], cached=True,
                    snapshot_ref=derive_snapshot(entry[1].snapshot_ref, is_cached=True)
                    if entry[1].snapshot_ref else None,
                )
            if self._cooldown.get(quota_key, 0) > stamp:
                result = Fetch(url, "rate_limited", None, now())
                return self._record(result, final_url=url, metadata={"cooldown": True})
            time.sleep(max(0, self.interval - (stamp - self._last)))
            result = self._fetch(url)
            self._last = time.monotonic()
            ttl = 600 if result.status == "ok" else 30
            self._cache[cache_key] = (self._last + ttl, result)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return result

    @staticmethod
    def _record(
        result: Fetch, *, final_url: str, raw: bytes | None = None,
        truncated: bool = False, metadata: dict | None = None,
    ) -> Fetch:
        revision = None
        path = None
        if isinstance(result.data, dict):
            sha = result.data.get("sha")
            revision = sha if isinstance(sha, str) else None
            entry_path = result.data.get("path")
            path = entry_path if isinstance(entry_path, str) else None
        ref = capture_snapshot(
            kind="public_code_response", raw=raw,
            normalized_json=result.data, normalized_text=result.text or None,
            requested_url=result.url, final_url=final_url, retrieved_at=result.retrieved_at,
            fetch_status=result.status, http_status=result.http_status, is_cached=result.cached,
            truncated=truncated or bool(isinstance(result.data, dict) and result.data.get("truncated")),
            revision=revision, path=path, metadata=metadata,
        )
        return replace(result, snapshot_ref=ref)

    def _fetch(self, url: str) -> Fetch:
        target = url
        retrieved = now()
        original_path = self._repository_path(url) if self._github_api(url) else None
        original_repository = f"{original_path[1]}/{original_path[2]}".casefold() if original_path else None
        original_metadata = original_path is not None and original_path[3] is None
        authorized_canonical_target = None
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
                for _ in range(4):
                    if not self._public(target):
                        return self._record(Fetch(url, "access_denied", None, retrieved), final_url=target)
                    # Rebuild headers after validating every redirect target.
                    # Credentials never enter the client defaults or an external origin.
                    parsed = urlsplit(target)
                    github_api = self._github_api(target)
                    repository_path = self._repository_path(target)
                    canonical_path = re.fullmatch(r"/repositories/([0-9]+)(/.*)?", parsed.path)
                    canonical_authorized = github_api and target == authorized_canonical_target
                    repository = (
                        f"{repository_path[1]}/{repository_path[2]}".casefold() if repository_path else None
                    )
                    repository_metadata = repository_path is not None and repository_path[3] is None
                    headers = {
                        "User-Agent": "paper-credibility-check/0.1",
                        "Accept": "application/vnd.github+json"
                        if github_api else "text/html,text/plain,application/json",
                    }
                    if self._can_authenticate(target) or canonical_authorized:
                        token = get_settings().github_token.get_secret_value().strip()
                        if token:
                            headers["Authorization"] = "Bearer " + token
                    authenticated = "Authorization" in headers
                    quota_key = self._quota_key(target, authenticated)
                    if self._cooldown.get(quota_key, 0) > time.monotonic():
                        return self._record(Fetch(url, "rate_limited", None, retrieved), final_url=target,
                                            metadata={"cooldown": True})
                    with client.stream("GET", target, headers=headers) as response:
                        code = response.status_code
                        if code in (301, 302, 303, 307, 308):
                            next_target = urljoin(target, response.headers.get("location", ""))
                            next_canonical = re.fullmatch(
                                r"/repositories/([0-9]+)(/.*)?", urlsplit(next_target).path,
                            )
                            same_scope = False
                            if repository_path and next_canonical:
                                same_scope = (repository_path[3] or "") == (next_canonical[2] or "")
                            elif canonical_authorized and canonical_path and next_canonical:
                                same_scope = (
                                    canonical_path[1] == next_canonical[1]
                                    and (canonical_path[2] or "") == (next_canonical[2] or "")
                                )
                            # Only this authenticated same-origin redirect can authorize
                            # a numeric canonical route, never a direct numeric-ID input.
                            authorized_canonical_target = (
                                next_target if authenticated and github_api and self._github_api(next_target)
                                and same_scope else None
                            )
                            target = next_target
                            continue
                        limited = code == 429 or (
                            code == 403
                            and (
                                response.headers.get("x-ratelimit-remaining") == "0"
                                or "retry-after" in response.headers
                            )
                        )
                        status = (
                            "rate_limited"
                            if limited
                            else "not_found"
                            if code == 404
                            else "access_denied"
                            if code in (401, 403)
                            else "ok"
                            if code == 200
                            else "error"
                        )
                        if limited:
                            try:
                                retry = float(response.headers.get("retry-after", "60"))
                                reset = float(response.headers.get("x-ratelimit-reset", "0")) - time.time()
                            except ValueError:
                                retry, reset = 60, 0
                            self._cooldown[quota_key] = time.monotonic() + max(60, retry, reset)
                        if status != "ok":
                            return self._record(Fetch(url, status, code, retrieved), final_url=target)
                        chunks, size = [], 0
                        # Recursive file trees of data-heavy repositories exceed 1 MB; GitHub
                        # itself truncates trees near 7 MB and flags that in the payload.
                        limit = TREE_MAX_BYTES if github_api and "/git/trees/" in parsed.path else self.max_bytes
                        for part in response.iter_bytes():
                            size += len(part)
                            if size > limit:
                                available = b"".join(chunks) + part[:max(0, limit - (size - len(part)))]
                                return self._record(Fetch(url, "error", code, retrieved), final_url=target,
                                                    raw=available, truncated=True)
                            chunks.append(part)
                        raw_bytes = b"".join(chunks)
                        raw = raw_bytes.decode("utf-8", errors="replace")
                        data = None
                        if "json" in response.headers.get("content-type", ""):
                            try:
                                data = json.loads(raw)
                            except ValueError:
                                return self._record(Fetch(url, "error", code, retrieved),
                                                    final_url=target, raw=raw_bytes)
                        if github_api and isinstance(data, dict) and data.get("private") is True:
                            for private_name in (repository, original_repository, data.get("full_name")):
                                if isinstance(private_name, str):
                                    self._public_repositories.discard(private_name.casefold())
                            return self._record(
                                Fetch(url, "access_denied", code, retrieved), final_url=target,
                                metadata={"reason": "private_repository_not_supported"},
                            )
                        metadata_response = repository_metadata or (
                            canonical_authorized and canonical_path is not None and canonical_path[2] is None
                        )
                        if github_api and metadata_response and isinstance(data, dict) and data.get("private") is False:
                            aliases = [repository, original_repository if original_metadata else None,
                                       data.get("full_name")]
                            for alias in aliases:
                                if (isinstance(alias, str) and re.fullmatch(
                                    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", alias,
                                ) and all(part not in {".", ".."} for part in alias.split("/"))):
                                    self._public_repositories.add(alias.casefold())
                        return self._record(Fetch(
                            url,
                            "ok",
                            code,
                            retrieved,
                            raw,
                            data,
                            has_next='rel="next"' in response.headers.get("link", ""),
                        ), final_url=target, raw=raw_bytes)
        except httpx.TimeoutException:
            return self._record(Fetch(url, "timeout", None, retrieved), final_url=target)
        except (httpx.HTTPError, ValueError):
            return self._record(Fetch(url, "error", None, retrieved), final_url=target)
        return self._record(Fetch(url, "error", None, retrieved), final_url=target)


LOOKUP = PublicCodeLookup()
