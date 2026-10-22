"""Bounded DeepSeek Search/Fetch transport for untrusted code-discovery data.

Search snippets are candidates, never implementation or ownership evidence.
Fetch returns provider-extracted Markdown, not the origin's raw HTTP response;
the provider does not expose a verified redirect chain or origin HTTP status.
No model loop, scoring, automatic retry, or repository confirmation lives here.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

import certifi
import httpx

from app.config import get_settings
from app.credibility.code_lookup import now
from app.credibility.snapshots import SnapshotStore

PROVIDER_VERSION = "deepseek-code-discovery-1.2"
SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
_DNS_SLOTS = threading.BoundedSemaphore(4)
_SENSITIVE_KEYS = {
    "key", "apikey", "token", "accesstoken", "refreshtoken", "idtoken", "password", "passwd",
    "secret", "clientsecret", "privatekey", "authorization", "auth", "credential", "credentials",
    "signature", "sig", "session", "sessionid", "cookie", "xamzcredential", "xamzsignature",
}
_URL_IN_TEXT = re.compile(r"https?\\*:\\*/\\*/[^\s<>\"']+", re.I)
_CONTROL = re.compile(r"[\x00-\x20\x7f]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
ProviderStatus = Literal["ok", "not_found", "access_denied", "timeout", "rate_limited", "error"]


@dataclass(frozen=True)
class DiscoveryProviderResult:
    operation: Literal["search", "fetch"]
    status: ProviderStatus
    http_status: int | None
    retrieved_at: str
    reason_code: str
    snapshot_ref: str | None = None
    query: str | None = None
    url: str | None = None
    title: str = ""
    text: str = ""
    hits: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    raw_result_count: int = 0
    rejected_result_count: int = 0
    content_status: Literal["readable", "blocked", "empty"] | None = None
    content_reason_code: str | None = None

    def model_dump(self) -> dict:
        return asdict(self)


def page_content_problem(title: str, text: str) -> str | None:
    """Identify clear interstitials, independently of provider HTTP success.

    This is a conservative content check, not an origin status inference. A
    normal article mentioning CAPTCHA/login later in its body is still readable.
    """
    prose = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~])", r"\1", text[:8000])
    first = re.sub(r"^[\s#*_`>-]+", "", prose).casefold()
    label = " ".join(title.casefold().split())
    browser = r"(?:verifying|checking) your browser|verify (?:that )?you are human|just a moment"
    if re.match(browser, first) or re.fullmatch(browser + r"[.!… ]*", label):
        if len(text) < 1600 or re.search(r"complete the check|javascript|cloudflare|captcha|security check", first):
            return "content_browser_verification"
    if re.match(r"(?:captcha|security verification|human verification)\b", first):
        return "content_captcha"
    login = r"(?:sign in|log in|login)(?: to (?:continue|openreview|github|access))?\b"
    if re.match(login, first) and re.search(r"password|email|sign in to continue|log in to continue", first[:1600]):
        return "content_login_required"
    return None


def _sensitive_key(key: str) -> bool:
    name = re.sub(r"[^a-z0-9]", "", key.casefold())
    return name in _SENSITIVE_KEYS or name.endswith(("token", "password", "secret", "credential"))


def _credential_probe_url(value: str) -> str:
    # This is only a conservative secret detector, never display text or a
    # navigable URL. Markdown/JSON-literal escapes cannot conceal credentials.
    return re.sub(r"\\+([/?=&:._#%+@\-])", r"\1", value)


def _credential_url(url: str) -> bool:
    try:
        decoded = _credential_probe_url(url)
        # Reject encoded key names and nested redirect URLs as well. These
        # values must never reach either persisted audit data or the provider.
        for _ in range(5):
            parsed = urlsplit(decoded)
            if parsed.username is not None or parsed.password is not None:
                return True
            for part in (parsed.query, parsed.fragment):
                for key, value in parse_qsl(part, keep_blank_values=True):
                    if _sensitive_key(key) or "%" in key:
                        return True
                    for nested in _URL_IN_TEXT.finditer(value):
                        candidate = urlsplit(_credential_probe_url(nested[0]))
                        if candidate.username is not None or candidate.password is not None:
                            return True
                        if any(_sensitive_key(k) or "%" in k for p in (candidate.query, candidate.fragment)
                               for k, _ in parse_qsl(p, keep_blank_values=True)):
                            return True
            next_decoded = unquote(decoded)
            if next_decoded == decoded:
                break
            decoded = next_decoded
        return False
    except ValueError:
        return True


def _basic_url_reason(url: object) -> str | None:
    if not isinstance(url, str) or not 1 <= len(url) <= 8192 or _SURROGATE.search(url):
        return "target_url_invalid"
    if _credential_url(url):
        return "target_url_credentials_rejected"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.port not in (None, 443)
                or _CONTROL.search(url) or "\\" in url):
            return "target_url_unsafe"
        decoded = url
        for _ in range(3):
            decoded = unquote(decoded)
            if re.search(r"[\x00-\x1f\x7f\\]", decoded):
                return "target_url_unsafe"
        if ("%" in host or host.endswith(".") or host.casefold() == "localhost"
                or host.casefold().endswith((".localhost", ".local", ".internal", ".lan", ".home", ".arpa"))):
            return "target_url_unsafe"
        ascii_host = host.encode("idna").decode("ascii")
        try:
            address = ipaddress.ip_address(ascii_host)
        except ValueError:
            if ("." not in ascii_host or len(ascii_host) > 253 or any(
                    not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in ascii_host.split("."))):
                return "target_url_unsafe"
        else:
            if not _public_address(address):
                return "target_url_not_public"
    except (ValueError, UnicodeError):
        return "target_url_invalid"
    return None


def _public_address(address) -> bool:
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped and not _public_address(address.ipv4_mapped):
            return False
        if address.sixtofour and not _public_address(address.sixtofour):
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network("64:ff9b:1::/48"):
            return False
    return True


def _resolve(host: str) -> Iterable[str]:
    return [row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


def _dns_reason(host: str, resolver: Callable, timeout: float) -> str | None:
    # Daemon workers and a process-wide cap bound both latency and outstanding
    # stuck system resolver calls. DNS failure is not permission to fetch.
    if timeout <= 0 or not _DNS_SLOTS.acquire(timeout=timeout):
        return "target_dns_timeout"
    result: queue.Queue = queue.Queue(maxsize=1)

    def run():
        try:
            result.put((True, list(resolver(host))))
        except Exception:
            result.put((False, []))
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=run, daemon=True, name="code-discovery-dns").start()
    try:
        valid, addresses = result.get(timeout=timeout)
    except queue.Empty:
        return "target_dns_timeout"
    if not valid or not addresses:
        return "target_dns_unavailable"
    try:
        return None if all(_public_address(ipaddress.ip_address(a)) for a in addresses) else "target_url_not_public"
    except (ValueError, TypeError):
        return "target_dns_unavailable"


class DeepSeekCodeDiscoveryProvider:
    provider_name = "deepseek"
    provider_version = PROVIDER_VERSION
    search_endpoint = SEARCH_ENDPOINT
    fetch_endpoint = FETCH_ENDPOINT
    search_label = "deepseek_search"

    """One task's bounded provider requests; callers own the wider shared budget.

    Inject a client/resolver/store for offline tests. Credentials are loaded from
    the existing config only when not explicitly supplied. The provider host and
    paths are fixed regardless of configured chat base URLs. Redirects are never
    followed, and failed attempts consume request budget without automatic retry.
    """

    def __init__(self, *, api_key: str | None = None, client: httpx.Client | None = None,
                 store: SnapshotStore | None = None, resolver: Callable[[str], Iterable[str]] = _resolve,
                 max_requests: int = 7, timeout: float = 20, max_bytes: int = 1_000_000,
                 max_characters: int = 60_000, deadline_seconds: float = 120):
        if (not isinstance(max_requests, int) or isinstance(max_requests, bool) or not 0 <= max_requests <= 40
                or not 0 < timeout <= 60 or not 1024 <= max_bytes <= 5_000_000
                or not 1000 <= max_characters <= 150_000 or not 0 < deadline_seconds <= 600):
            raise ValueError("Invalid discovery provider budget")
        key = self._configured_key() if api_key is None else api_key
        self._api_key = key.strip() if isinstance(key, str) else ""
        self._client, self._store, self._resolver = client, store or SnapshotStore(), resolver
        self.max_requests, self.timeout, self.max_bytes = max_requests, timeout, max_bytes
        self.max_characters = max_characters
        self._deadline = time.monotonic() + deadline_seconds
        self._request_count = 0
        self._lock = threading.Lock()

    @staticmethod
    def _configured_key() -> str:
        return get_settings().deepseek_api_key

    def __repr__(self) -> str:
        return f"{type(self).__name__}(request_count={self.request_count}, max_requests={self.max_requests})"

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def remaining_requests(self) -> int:
        return max(0, self.max_requests - self.request_count)

    def _safe_text(self, value: str) -> str:
        value = _SURROGATE.sub("\ufffd", value)
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
        if self._api_key:
            value = value.replace(self._api_key, "[REDACTED]")

        def redact_url(match):
            return "[REDACTED_URL]" if _credential_url(match[0]) else match[0]

        return _URL_IN_TEXT.sub(redact_url, value)

    def _record(self, result: DiscoveryProviderResult) -> DiscoveryProviderResult:
        endpoint = self.search_endpoint if result.operation == "search" else self.fetch_endpoint
        # Persist only whitelisted safe fields. Error bodies, unused search
        # content, provider headers and arbitrary exception strings are omitted.
        normalized = {**result.model_dump(), "body": result.text}
        normalized.pop("snapshot_ref")
        ref = self._store.record(
            kind=f"code_discovery_{result.operation}", normalized_json=normalized,
            normalized_text=result.text or None, requested_url=result.url or endpoint,
            final_url=endpoint if result.operation == "search" else None,
            retrieved_at=result.retrieved_at, fetch_status=result.status, http_status=result.http_status,
            truncated=result.truncated,
            metadata={"provider": self.provider_name, "provider_version": self.provider_version,
                      "provider_endpoint": endpoint, "request_count": self.request_count,
                      "reason_code": result.reason_code, "origin_http_status": None,
                      "origin_redirect_chain_verified": False, "raw_provider_response_saved": False},
        )
        return replace(result, snapshot_ref=ref)

    def _result(self, operation, status, reason, **kwargs):
        return self._record(DiscoveryProviderResult(operation, status, kwargs.pop("http_status", None),
                                                    now(), reason, **kwargs))

    def _budget_reason(self) -> str | None:
        if not self._api_key:
            return "provider_key_missing"
        if self.remaining_requests <= 0:
            return "request_budget_exhausted"
        if time.monotonic() >= self._deadline:
            return "provider_deadline_exceeded"
        return None

    def _target_reason(self, url: str) -> str | None:
        problem = _basic_url_reason(url)
        if problem:
            return problem
        host = urlsplit(url).hostname.encode("idna").decode("ascii")
        try:
            return None if _public_address(ipaddress.ip_address(host)) else "target_url_not_public"
        except ValueError:
            return _dns_reason(host, self._resolver, min(2.0, max(0, self._deadline - time.monotonic())))

    def search(self, query: str, *, count: int = 5) -> DiscoveryProviderResult:
        if (not isinstance(query, str) or not query.strip() or len(query) > 128
                or _SURROGATE.search(query)
                or _CONTROL.search(query.replace(" ", ""))
                or (self._api_key and self._api_key in query)
                or not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 50):
            return self._result("search", "error", "search_request_invalid")
        if any(_credential_url(m[0]) for m in _URL_IN_TEXT.finditer(query)):
            return self._result("search", "access_denied", "search_credentials_rejected")
        return self._request("search", {"query": query, "count": count, "need_content": False}, query=query)

    def fetch(self, url: str) -> DiscoveryProviderResult:
        problem = _basic_url_reason(url)
        if problem or (isinstance(url, str) and self._api_key and self._api_key in url):
            return self._result("fetch", "access_denied", problem or "target_url_credentials_rejected")
        problem = self._budget_reason()
        if problem:
            return self._result("fetch", "timeout" if problem == "provider_deadline_exceeded" else "error", problem,
                                url=url)
        problem = self._target_reason(url)
        if problem:
            status = ("timeout" if problem == "target_dns_timeout"
                      else "error" if problem == "target_dns_unavailable" else "access_denied")
            return self._result("fetch", status, problem)
        return self._request("fetch", {"url": url}, url=url)

    def _request(self, operation, payload: dict, **identity) -> DiscoveryProviderResult:
        endpoint = SEARCH_ENDPOINT if operation == "search" else FETCH_ENDPOINT
        with self._lock:
            problem = self._budget_reason()
            if problem:
                return self._result(operation, "timeout" if problem == "provider_deadline_exceeded" else "error",
                                    problem, **identity)
            self._request_count += 1
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json",
                   "Accept": "application/json", "Accept-Encoding": "identity"}
        owned = self._client is None
        client = self._client or httpx.Client(
            verify=ssl.create_default_context(cafile=certifi.where()), trust_env=False,
        )
        http_status = None
        try:
            timeout = min(self.timeout, max(0.001, self._deadline - time.monotonic()))
            with client.stream("POST", endpoint, json=payload, headers=headers, timeout=timeout,
                               follow_redirects=False) as response:
                http_status = response.status_code
                if http_status != 200:
                    status = ("rate_limited" if http_status == 429 else "access_denied" if http_status in {401, 403}
                              or 300 <= http_status < 400 else "not_found" if http_status == 404
                              else "timeout" if http_status in {408, 504} else "error")
                    return self._result(operation, status, f"provider_http_{http_status}",
                                        http_status=http_status, **identity)
                if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    return self._result(operation, "error", "provider_encoding_unsupported",
                                        http_status=200, **identity)
                chunks, size = [], 0
                stream = (response.iter_bytes() if response.is_stream_consumed
                          else response.iter_raw(chunk_size=min(65_536, self.max_bytes + 1)))
                for chunk in stream:
                    size += len(chunk)
                    if size > self.max_bytes:
                        return self._result(operation, "error", "provider_response_too_large", http_status=200,
                                            truncated=True, **identity)
                    if time.monotonic() > self._deadline:
                        return self._result(operation, "timeout", "provider_deadline_exceeded",
                                            http_status=200, **identity)
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks))
            return self._parse(operation, data, result_limit=payload.get("count", 50), **identity)
        except httpx.TimeoutException:
            return self._result(operation, "timeout", "provider_timeout", http_status=http_status, **identity)
        except httpx.HTTPError:
            return self._result(operation, "error", "provider_transport_error", http_status=http_status, **identity)
        except (ValueError, UnicodeError, TypeError):
            return self._result(operation, "error", "provider_response_invalid", http_status=http_status, **identity)
        finally:
            if owned:
                client.close()

    def _parse(self, operation, data, *, result_limit=50, **identity) -> DiscoveryProviderResult:
        if not isinstance(data, dict) or "error" in data:
            return self._result(operation, "error", "provider_response_invalid", http_status=200, **identity)
        if operation == "fetch":
            if not isinstance(data.get("title"), str) or not isinstance(data.get("content"), str):
                return self._result(operation, "error", "provider_response_invalid", http_status=200, **identity)
            text, title = self._safe_text(data["content"]), self._safe_text(data["title"])
            content_problem = page_content_problem(title, text)
            truncated = len(text) > self.max_characters or len(title) > 1000
            reason = ("fetch_content_truncated" if truncated else "fetch_content"
                      if text.strip() else "fetch_empty_content")
            return self._result(operation, "ok" if text.strip() else "not_found",
                                reason, http_status=200, title=title[:1000],
                                text=text[:self.max_characters], truncated=truncated,
                                content_status=("blocked" if content_problem else "readable"
                                                if text.strip() else "empty"),
                                content_reason_code=content_problem,
                                **identity)
        rows = data.get("results")
        if not isinstance(rows, list):
            return self._result(operation, "error", "provider_response_invalid", http_status=200, **identity)
        hits, rejected, seen, hosts = [], 0, set(), {}
        count = min(result_limit, 50, len(rows))
        for row in rows[:count]:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("url", "title", "snippet")):
                rejected += 1
                continue
            url = row["url"]
            problem = _basic_url_reason(url)
            if not problem:
                host = urlsplit(url).hostname
                if host not in hosts:
                    hosts[host] = self._target_reason(url)
                problem = hosts[host]
            if problem or (self._api_key and self._api_key in url):
                rejected += 1
                continue
            if url not in seen:
                hits.append({"url": url, "title": self._safe_text(row["title"])[:1000],
                             "snippet": self._safe_text(row["snippet"])[:2000]})
                seen.add(url)
        reason = "search_results_filtered" if rejected else "search_results" if hits else "search_empty"
        return self._result(operation, "ok", reason,
                            http_status=200, hits=hits, raw_result_count=len(rows), rejected_result_count=rejected,
                            truncated=len(rows) > count, **identity)


OPENAI_PROVIDER_VERSION = "openai-code-discovery-1.0"


class OpenAICodeDiscoveryProvider(DeepSeekCodeDiscoveryProvider):
    """Same bounded interface without DeepSeek: OpenAI web search and a direct, SSRF-checked page read.

    Search hits are the cited URLs of an OpenAI Responses ``web_search`` call. Fetch reads
    the public page itself (redirects re-checked hop by hop) and extracts visible text.
    """

    provider_name = "openai"
    provider_version = OPENAI_PROVIDER_VERSION
    search_endpoint = "https://api.openai.com/v1/responses#web_search"
    fetch_endpoint = "direct-https-fetch"
    search_label = "openai_web_search"

    @staticmethod
    def _configured_key() -> str:
        return get_settings().openai_api_key

    def _request(self, operation, payload: dict, **identity) -> DiscoveryProviderResult:
        with self._lock:
            problem = self._budget_reason()
            if problem:
                return self._result(operation, "timeout" if problem == "provider_deadline_exceeded" else "error",
                                    problem, **identity)
            self._request_count += 1
        owned = self._client is None
        client = self._client or httpx.Client(
            verify=ssl.create_default_context(cafile=certifi.where()), trust_env=False,
        )
        try:
            timeout = min(self.timeout, max(0.001, self._deadline - time.monotonic()))
            if operation == "search":
                return self._search(client, payload, timeout, **identity)
            return self._fetch(client, payload["url"], timeout, **identity)
        except httpx.TimeoutException:
            return self._result(operation, "timeout", "provider_timeout", **identity)
        except httpx.HTTPError:
            return self._result(operation, "error", "provider_transport_error", **identity)
        except (ValueError, UnicodeError, TypeError, KeyError):
            return self._result(operation, "error", "provider_response_invalid", **identity)
        finally:
            if owned:
                client.close()

    def _search(self, client, payload, timeout, **identity):
        settings = get_settings()
        response = client.post(
            settings.openai_base_url.rstrip("/") + "/responses",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.openai_model, "tools": [{"type": "web_search"}], "store": False,
                "input": (f"Search the web for: {payload['query']}\nList the {payload['count']} most relevant "
                          "result pages with a one-sentence description of each, citing every page."),
            },
            timeout=max(timeout, 1.0),
        )
        if response.status_code != 200:
            status = ("rate_limited" if response.status_code == 429 else "access_denied"
                      if response.status_code in {401, 403} else "error")
            return self._result("search", status, f"provider_http_{response.status_code}",
                                http_status=response.status_code, **identity)
        rows, seen = [], set()
        for item in response.json().get("output", []):
            for content in item.get("content", []) if item.get("type") == "message" else []:
                text = content.get("text", "")
                for note in content.get("annotations", []):
                    url = note.get("url")
                    if note.get("type") != "url_citation" or not isinstance(url, str) or url in seen:
                        continue
                    seen.add(url)
                    start, end = note.get("start_index", 0), note.get("end_index", 0)
                    snippet = text[max(0, start - 300):end] if isinstance(start, int) and isinstance(end, int) else ""
                    rows.append({"url": _without_tracking(url), "title": str(note.get("title") or url),
                                 "snippet": snippet})
        return self._parse("search", {"results": rows}, result_limit=payload["count"], **identity)

    def _fetch(self, client, requested, timeout, **identity):
        from app.credibility.code_repository_scope import ProjectHTML

        target = requested
        for _ in range(4):
            with client.stream("GET", target, timeout=max(timeout, 1.0), follow_redirects=False,
                               headers={"User-Agent": "paper-credibility-check/0.1",
                                        "Accept": "text/html,text/plain"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    target = str(httpx.URL(target).join(response.headers.get("location", "")))
                    problem = self._target_reason(target)
                    if problem:
                        return self._result("fetch", "access_denied", problem, **identity)
                    continue
                if response.status_code != 200:
                    status = ("not_found" if response.status_code == 404 else "rate_limited"
                              if response.status_code == 429 else "access_denied"
                              if response.status_code in {401, 403} else "error")
                    return self._result("fetch", status, f"origin_http_{response.status_code}",
                                        http_status=response.status_code, **identity)
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            if "html" in response.headers.get("content-type", "html"):
                page = ProjectHTML(raw)
                title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
                # Like a Markdown page read: visible text, then every link with its absolute target.
                links = [f"[{' '.join(a['label'].split()) or 'link'}]({httpx.URL(target).join(a['href'])})"
                         for a in page.anchors if a.get("href") and not a["href"].startswith(("#", "javascript:"))]
                content = page.text + ("\n\nLinks:\n" + "\n".join(dict.fromkeys(links)) if links else "")
                return self._parse("fetch", {"title": " ".join((title[1] if title else "").split()),
                                             "content": content}, **identity)
            return self._parse("fetch", {"title": "", "content": raw}, **identity)
        return self._result("fetch", "error", "too_many_redirects", **identity)


def _without_tracking(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return parts._replace(query="&".join(f"{k}={v}" if v else k for k, v in query)).geturl()


def discovery_provider_for(client, **kwargs) -> DeepSeekCodeDiscoveryProvider | None:
    """The search/fetch transport matching the selected model's provider, or None when unavailable."""
    from app.llm.openai import OpenAIClient

    settings = get_settings()
    if isinstance(client, OpenAIClient):
        return OpenAICodeDiscoveryProvider(**kwargs) if settings.openai_api_key else None
    if settings.deepseek_api_key and settings.deepseek_enabled:
        return DeepSeekCodeDiscoveryProvider(**kwargs)
    return None
