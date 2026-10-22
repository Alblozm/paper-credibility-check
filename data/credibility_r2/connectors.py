"""Read-only public API probes with bounded queries and minimal evidence cache."""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler

from .core import ValidationError, digest, load_json, now, require, save_json


@dataclass
class FetchResult:
    data: object = None
    error: str | None = None
    source_url: str = ""
    retrieved_at: str | None = None
    response_sha256: str | None = None
    from_cache: bool = False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect rejected", headers, fp)


def minimize(url, body):
    """Cache selected metadata and short excerpts, never full web pages/abstracts."""
    if urlparse(url).hostname == "api.crossref.org":
        message = body.get("message", {})
        def item(row):
            keys = ("DOI", "title", "author", "published", "published-print", "published-online", "issued", "URL", "type")
            return {key: row[key] for key in keys if key in row}
        return {"message": {"items": [item(r) for r in message["items"]]} if "items" in message else item(message)}
    def item(row):
        keys = ("id", "number", "html_url", "url", "title", "state", "state_reason", "comments", "created_at", "updated_at", "closed_at", "pull_request", "private", "full_name", "default_branch")
        output = {key: row[key] for key in keys if key in row}
        if "body" in row:
            output["body"] = (row.get("body") or "")[:2000]
            output["body_truncated"] = len(row.get("body") or "") > 2000
        if "user" in row:
            output["user"] = {"login": row["user"].get("login")}
        return output
    return [item(r) for r in body] if isinstance(body, list) else item(body)


class PublicJsonClient:
    def __init__(self, cache_dir, timeout=8, max_attempts=2, interval=0.5, ttl_seconds=86400, opener=None, clock=time.monotonic, sleep=time.sleep):
        require(0 < timeout <= 30 and 1 <= max_attempts <= 3 and interval >= 0, "invalid client bounds")
        self.cache_dir = Path(cache_dir)
        self.timeout, self.max_attempts, self.interval, self.ttl = timeout, max_attempts, interval, ttl_seconds
        if opener is None:
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                context = ssl.create_default_context()
            opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
        self.opener = opener
        self.clock, self.sleep = clock, sleep
        self.last_request = None

    def get(self, url):
        parsed = urlparse(url)
        require(parsed.scheme == "https" and parsed.hostname in ("api.crossref.org", "api.github.com") and not parsed.username and not parsed.password, "only approved public metadata API URLs may be queried")
        cache_path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < self.ttl:
            cached = load_json(cache_path)
            if cached.get("source_url") == url and cached.get("payload_sha256") == digest(cached.get("data")):
                return FetchResult(cached["data"], None, url, cached["retrieved_at"], cached["response_sha256"], True)
        headers = {"User-Agent": "PaperCredibilityCheck-Draft/0.1", "Accept": "application/json"}
        if parsed.hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            if os.environ.get("GITHUB_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        last_error = "query failed"
        for attempt in range(self.max_attempts):
            if self.last_request is not None:
                delay = self.interval - (self.clock() - self.last_request)
                if delay > 0:
                    self.sleep(delay)
            self.last_request = self.clock()
            try:
                with self.opener.open(Request(url, headers=headers), timeout=self.timeout) as response:
                    raw = response.read(2_000_001)
                require(len(raw) <= 2_000_000, "metadata response exceeds 2 MB")
                body = minimize(url, json.loads(raw))
                result = FetchResult(body, None, url, now(), hashlib.sha256(raw).hexdigest())
                save_json(cache_path, {"source_url": url, "retrieved_at": result.retrieved_at, "response_sha256": result.response_sha256, "payload_sha256": digest(body), "data": body})
                return result
            except HTTPError as error:
                last_error = f"HTTP {error.code}"
                if error.code == 404 and parsed.hostname == "api.crossref.org":
                    return FetchResult({"message": {"items": []}}, None, url, now())
                if error.code not in (403, 429, 500, 502, 503, 504):
                    break
                retry = error.headers.get("Retry-After") if error.headers else None
                if retry and retry.isdigit() and int(retry) > 5:
                    last_error += ": retry deferred by service"
                    break
                self.sleep(min(5, int(retry)) if retry and retry.isdigit() else 0.5 * (attempt + 1))
            except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                last_error = type(error).__name__ + ": metadata query failed"
        return FetchResult(None, last_error, url, now())


def normalize(text):
    text = re.sub(r"<[^>]*>", "", text or "")
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def normalized_doi(text):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/|^doi:\s*", "", (text or "").strip(), flags=re.I).rstrip(".,;").casefold()


def metadata_candidate(row, number):
    years = []
    for key in ("published", "published-print", "published-online", "issued"):
        parts = row.get(key, {}).get("date-parts", [])
        if parts and parts[0] and isinstance(parts[0][0], int):
            years.append(parts[0][0])
    title = row.get("title", [])
    return {"candidate_id": f"candidate-{number}", "title": title[0] if title else "", "authors": [a.get("family", "") for a in row.get("author", []) if a.get("family")], "year": years[0] if years else None, "years": list(dict.fromkeys(years)), "doi": row.get("DOI"), "source_url": row.get("URL") or "https://api.crossref.org/works/" + quote(row.get("DOI", ""), safe="")}


def verify_reference(ref, client, mailto=None):
    check = {"reference_check_id": "rc-" + ref["paper_reference_id"], "paper_reference_id": ref["paper_reference_id"], "status": "not_checked", "provider": "Crossref", "query": {}, "query_attempted": False, "source_url": None, "checked_at": None, "technical_error": None, "candidates": [], "conflicting_fields": [], "selected_candidate_id": None, "match_confidence": None, "retrieved_at": None, "response_sha256": None, "from_cache": False}
    if ref["parse_status"] == "failed":
        check["status"] = "parse_failed"
        return check
    doi = normalized_doi(ref["parsed_doi"])
    if doi:
        url = "https://api.crossref.org/works/" + quote(doi, safe="")
        check["query"] = {"doi": doi}
    else:
        query = ref["parsed_title"] or ref["raw_reference_text"]
        parameters = {"query.bibliographic": query, "rows": 5}
        if mailto:
            parameters["mailto"] = mailto
        url = "https://api.crossref.org/works?" + urlencode(parameters)
        check["query"] = parameters
    result = client.get(url)
    check.update(query_attempted=True, source_url=url, checked_at=now(), retrieved_at=result.retrieved_at, response_sha256=result.response_sha256, from_cache=result.from_cache)
    if result.error:
        check.update(status="query_failed", technical_error=result.error)
        return check
    message = result.data.get("message", {})
    items = message.get("items", [message] if message.get("DOI") else [])
    candidates = [metadata_candidate(row, i) for i, row in enumerate(items)]
    check["candidates"] = candidates
    title = normalize(ref["parsed_title"])
    authors = {normalize(a).split()[-1] for a in ref["parsed_authors"] if normalize(a)}
    year = ref["parsed_year"]
    qualified, conflicts = [], []
    for candidate in candidates:
        ratio = SequenceMatcher(None, title, normalize(candidate["title"])).ratio() if title else None
        candidate["title_similarity"] = ratio
        same_doi = bool(doi and normalized_doi(candidate["doi"]) == doi)
        same_title = ratio is not None and ratio >= 0.96
        candidate_authors = {normalize(a) for a in candidate["authors"]}
        same_authors = bool(authors and candidate_authors and authors & candidate_authors)
        same_year = year is not None and year in candidate["years"]
        mismatches = []
        if same_doi or same_title:
            if doi and not same_doi:
                mismatches.append("doi")
            if title and ratio is not None and ratio < 0.85:
                mismatches.append("title")
            if year and candidate["years"] and min(abs(year - y) for y in candidate["years"]) > 1:
                mismatches.append("year")
            if authors and candidate_authors and not same_authors:
                mismatches.append("authors")
            if mismatches:
                conflicts.extend(mismatches)
            # DOI match confirms identity only if supplied metadata does not conflict.
            # A one-year publication ambiguity stays unverified, rather than safe.
            elif (same_doi and (year is None or same_year) and (not title or same_title) and (not authors or same_authors)) or (same_title and same_year and same_authors):
                qualified.append(candidate)
    if len(qualified) == 1 and not conflicts:
        check.update(status="matched", selected_candidate_id=qualified[0]["candidate_id"], match_confidence=qualified[0]["title_similarity"] if title else 1.0)
    elif conflicts:
        check.update(status="metadata_conflict", conflicting_fields=list(dict.fromkeys(conflicts)))
    else:
        check["status"] = "unverified"
    return check


def discover_repositories(case):
    """Extract explicit links; return candidates for association review."""
    found = {}
    for chunk in case["document"]["chunks"]:
        normalized_text = re.sub(r"(https?://(?:www\.)?(?:github\.com|gitlab\.com)/)\s+", r"\1", chunk["text"])
        for match in re.finditer(r"https?://(?:www\.)?(github\.com|gitlab\.com)/([\w.-]+)/([\w.-]+)(?:/[^\s<>]*)?", normalized_text):
            platform, owner, name = match.group(1), match.group(2), match.group(3).rstrip(".,;")
            url = f"https://{platform}/{owner}/{name.removesuffix('.git')}"
            if url in found:
                continue
            found[url] = {"repository_id": "repo-" + hashlib.sha256(url.encode()).hexdigest()[:12], "paper_version_id": case["paper_version"]["paper_version_id"], "repository_url": url, "platform": "GitHub" if platform == "github.com" else "GitLab", "association_status": "candidate", "association_reviewed": False,
                          "association_evidence": [{"kind": "paper_link", "source_url": case["paper_version"]["source_url"], "excerpt": match.group(0), "observed_at": now()}], "access_status": "not_checked", "checked_at": None}
    return list(found.values())


def github_identity(url):
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    require(parsed.scheme == "https" and parsed.hostname == "github.com" and len(parts) == 2 and all(re.fullmatch(r"[\w.-]+", p) for p in parts), "canonical GitHub repository URL required")
    return parts


def collect_reproduction_signals(repo, client, max_pages=3, author_logins=()):
    require(repo["association_status"] == "confirmed" and repo["association_reviewed"], "only reviewed confirmed repositories may be queried")
    require(type(max_pages) is int and 1 <= max_pages <= 20, "max_pages must be 1..20")
    owner, name = github_identity(repo["repository_url"])
    base = f"https://api.github.com/repos/{owner}/{name}"
    scope = {"repository_id": repo["repository_id"], "completed": False, "truncated": False, "issues_checked": 0, "comments_checked": 0, "technical_error": None, "checked_at": now(), "source_urls": []}
    metadata = client.get(base)
    scope["source_urls"].append(base)
    if metadata.error or metadata.data.get("private") is not False:
        scope["technical_error"] = metadata.error or "repository is not confirmed public"
        return [], scope
    def pages(endpoint):
        rows = []
        for page in range(1, max_pages + 1):
            url = endpoint + ("&" if "?" in endpoint else "?") + urlencode({"per_page": 100, "page": page})
            scope["source_urls"].append(url)
            result = client.get(url)
            if result.error:
                scope["technical_error"] = result.error
                return rows
            require(isinstance(result.data, list), "unexpected GitHub API list response")
            rows.extend(result.data)
            if len(result.data) < 100:
                return rows
        scope["truncated"] = True
        return rows
    issues = pages(base + "/issues?state=all")
    signals = []
    authors = {a.casefold() for a in author_logins}
    keyword = re.compile(r"reproduc|reproducibility|replicat|reported results|paper results|复现", re.I)
    for issue in issues:
        if "pull_request" in issue:
            continue
        scope["issues_checked"] += 1
        comments = pages(base + f"/issues/{issue['number']}/comments") if issue.get("comments", 0) else []
        scope["comments_checked"] += len(comments)
        if issue.get("body_truncated") or any(c.get("body_truncated") for c in comments):
            scope["truncated"] = True
        issue_text = issue.get("title", "") + "\n" + (issue.get("body") or "")
        relevant_comments = [c for c in comments if keyword.search(c.get("body") or "")]
        if not keyword.search(issue_text) and not relevant_comments:
            continue
        excerpt = (relevant_comments[0].get("body") if relevant_comments and not keyword.search(issue_text) else issue_text) or ""
        login = (relevant_comments[0] if relevant_comments and not keyword.search(issue_text) else issue).get("user", {}).get("login", "")
        responses = [c for c in comments if c.get("user", {}).get("login", "").casefold() in authors]
        signals.append({"signal_id": f"signal-{repo['repository_id']}-{issue['number']}", "repository_id": repo["repository_id"], "signal_type": "discussion_inconclusive", "review_status": "candidate", "source_url": relevant_comments[0]["html_url"] if relevant_comments and not keyword.search(issue_text) else issue["html_url"], "source_title": issue.get("title", ""), "source_excerpt": excerpt[:400], "third_party_claim": "关键词检索候选，需人工确认是否讨论论文结果。", "third_party": login.casefold() not in authors if authors and login else None,
                        "author_response": "\n".join(c.get("body", "")[:400] for c in responses) or None, "resolution_status": "closed" if issue.get("state") == "closed" else "open", "resolution_url": None, "observed_at": now(), "confidence": None, "limitations": ["未运行代码；关键词命中不能证明复现成功或失败。", "作者身份仅依据显式提供的 author_logins。"], "comments": [{"source_url": c["html_url"], "excerpt": c.get("body", "")[:400], "login": c.get("user", {}).get("login")} for c in comments]})
    scope["completed"] = not scope["truncated"] and scope["technical_error"] is None
    return signals, scope
