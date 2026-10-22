"""Bounded, read-only external evidence tools; tool arguments never become code.

The registry is built from sources already discovered by the deterministic code
check. The model cannot supply a new repository, arbitrary URL, credentials,
shell command or executable action. Every observation is saved before use.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from html import unescape
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import Field, ValidationError

from app.credibility.code_lookup import LOOKUP, Fetch, now
from app.credibility.context import CheckContext, paper_from_context
from app.credibility.models import CodeCheckResult, CredibilityEvidence, CredibilityExternalSource
from app.credibility.snapshots import SnapshotError, capture_snapshot, derive_snapshot, read_snapshot
from app.schemas.common import StrictModel


class _RepositoryArgs(StrictModel):
    repository_id: str = Field(min_length=1, max_length=100)


class _FileArgs(_RepositoryArgs):
    path: str = Field(min_length=1, max_length=300)


class _IssueArgs(_RepositoryArgs):
    number: int = Field(strict=True, ge=1, le=2_147_483_647)


class _PageArgs(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)


_ARGUMENTS = {
    "read_repository_readme": (_RepositoryArgs, "Read the candidate repository README as text."),
    "resolve_repository_revision": (
        _RepositoryArgs,
        "Resolve the cited branch/tag to an immutable commit; retain an already inspected pinned commit.",
    ),
    "read_repository_file": (_FileArgs, "Read one necessary text/code file relative to the inspected subtree, "
                             "at its pinned commit; never run it."),
    "read_issue": (_IssueArgs, "Read one public issue title and body."),
    "read_issue_comments": (_IssueArgs, "Read the first 30 public issue comments; pagination remains an explicit gap."),
    "read_public_page": (_PageArgs, "Read a public project/supplement page already discovered in the paper."),
}
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema.model_json_schema()},
    }
    for name, (schema, description) in _ARGUMENTS.items()
]
_REPOSITORY = re.compile(r"/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/|$)")
_ISSUE = re.compile(r"/issues/(\d+)(?:#|/|$)")
_TEXT_EXTENSIONS = {
    ".md",
    ".rst",
    ".txt",
    ".py",
    ".r",
    ".jl",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".java",
    ".rs",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".cfg",
    ".ini",
}


def _scoped_message_rows(data: list, url: str) -> list[dict] | None:
    """Bind a source to one message or an explicitly scoped comments endpoint."""
    parsed = urlsplit(url)
    match = re.fullmatch(r"/repos/([^/]+)/([^/]+)/issues/(\d+)/comments", parsed.path)
    if parsed.hostname == "api.github.com" and match:
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 443)):
            return None
        owner, repo, number = match.groups()
        seen = set()
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("html_url"), str):
                return None
            message = urlsplit(row["html_url"])
            if (message.scheme != "https" or message.hostname != "github.com" or message.query
                    or message.username or message.password
                    or message.path.casefold() != f"/{owner}/{repo}/issues/{number}".casefold()
                    or not re.fullmatch(r"issuecomment-\d+", message.fragment)
                    or row["html_url"].casefold() in seen):
                return None
            seen.add(row["html_url"].casefold())
        return data
    matching = [row for row in data if isinstance(row, dict) and row.get("html_url") == url]
    return matching if len(matching) == 1 else None


def _text_from_data(data: Any, *, url: str = "") -> str:
    if isinstance(data, list):
        matching = _scoped_message_rows(data, url) if url else None
        if url and matching is None:
            # A saved issue/comment URL binds one original message, not every
            # other thread carried by its shared HTTP listing response.
            return ""
        if matching is not None and len(matching) == 1:
            data = matching[0]
        else:
            # Exclude profile URLs, avatars and irrelevant GitHub API envelopes.
            return "\n\n".join(_message_text(row) for row in (matching if url else data)
                               if isinstance(row, dict))
    if isinstance(data, dict):
        if data.get("encoding") == "base64" and isinstance(data.get("content"), str):
            try:
                return base64.b64decode(data["content"], validate=False).decode("utf-8", errors="strict")
            except (ValueError, UnicodeError, binascii.Error):
                return ""
        if "body" in data or "title" in data:
            return _message_text(data)
        return json.dumps(data, ensure_ascii=False)
    return ""


def _message(row: dict) -> dict:
    user = row.get("user")
    return {
        "url": row.get("html_url"),
        "title": row.get("title"),
        "body": row.get("body"),
        "user": user.get("login") if isinstance(user, dict) else None,
        "author_association": row.get("author_association"),
        "created_at": row.get("created_at"),
        "state": row.get("state"),
    }


def _message_text(row: dict) -> str:
    message = _message(row)
    metadata = {k: value for k, value in message.items() if k not in {"body", "title"}}
    return (
        json.dumps(metadata, ensure_ascii=False)
        + "\n"
        + str(message.get("title") or "")
        + "\n"
        + str(message.get("body") or "")
    )


def _html_text(text: str) -> str:
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    return unescape(re.sub(r"<[^>]+>", " ", text))


class ExternalToolSession:
    def __init__(
        self,
        ctx: CheckContext,
        result: CodeCheckResult,
        *,
        max_calls: int = 6,
        max_characters: int = 60_000,
        lookup=None,
    ):
        if not 0 <= max_calls <= 20 or not 1000 <= max_characters <= 150_000:
            raise ValueError("Invalid external tools budget")
        self.ctx, self.result = ctx, result
        self.max_calls, self.max_characters = max_calls, max_characters
        self.lookup = lookup or LOOKUP
        self.calls: list[dict[str, Any]] = []
        self.documents: dict[str, str] = {}
        self.original_documents: dict[str, str] = {}
        self.document_reviews: dict[str, dict[str, Any]] = {}
        self.document_errors: dict[str, str] = {}
        self.sources: list[CredibilityExternalSource] = []
        self.evidence: list[CredibilityEvidence] = []
        self.characters = 0
        self.request_count = 0
        self.request_budget = max(0, 40 - result.external_request_count)
        self.warnings: list[str] = []
        self.semantic_guards: list[dict[str, str]] = []
        self.guard_original_judgments: list[dict[str, Any]] = []
        self.prefix = uuid.uuid4().hex[:12]
        self._seen_calls: set[str] = set()
        self._seen_requests: dict[tuple[str, str], dict] = {}
        source_map = {s.source_id: s for s in result.sources}
        self.repositories: dict[str, tuple[str, str | None]] = {}
        self.repository_scopes: dict[str, dict] = {}
        self.invalid_initial_pins: set[str] = set()
        for repository in result.checks.repositories:
            source = source_map.get(repository.source_id)
            match = _REPOSITORY.search(urlsplit(source.url).path) if source else None
            if source and urlsplit(source.url).hostname == "api.github.com" and match:
                owner, repo = match.groups()
                if all(part not in {".", ".."} for part in (owner, repo)):
                    scope = dict(getattr(result, "followup_state", {}).get(repository.repository_id, {}))
                    requested = scope.get("requested_revision")
                    revision = repository.inspected_revision
                    if (isinstance(requested, str) and re.fullmatch(r"[a-f0-9]{40}", requested)
                            and revision != requested):
                        revision = None
                        self.invalid_initial_pins.add(repository.repository_id)
                        self.warnings.append("The inspected and cited commits differ; revalidation is required.")
                    self.repositories[repository.repository_id] = (f"{owner}/{repo}", revision)
                    self.repository_scopes[repository.repository_id] = scope
        self.pages = {s.source_id: s for s in result.sources if s.kind in {"project_page", "supplement"}}

    def existing_document(self, source: CredibilityExternalSource, *, focus_terms: tuple[str, ...] = ()) -> str:
        """Read the immutable response, including actual bodies, without a GET."""
        if source.source_id in self.documents:
            return self.documents[source.source_id]
        text = ""
        if source.snapshot_ref:
            try:
                snapshot = read_snapshot(source.snapshot_ref)
                data = snapshot.normalized_json
                if isinstance(data, list) and source.kind in {"issue", "comment"}:
                    if _scoped_message_rows(data, source.url) is None:
                        self.document_errors[source.source_id] = "original_message_not_unique"
                        self.warnings.append(
                            f"Original message unavailable or ambiguous for {source.source_id}; "
                            "other listing messages cannot substitute for it."
                        )
                        return self._keep_text(source.source_id, "")
                derived_page = snapshot.manifest.get("metadata", {}).get("normalization_version")
                if derived_page == "discovery-markdown-prose-1" and snapshot.normalized_text is not None:
                    text = snapshot.normalized_text
                else:
                    text = _text_from_data(snapshot.normalized_json, url=source.url)
                if not text and not derived_page and not (isinstance(data, list)
                                                         and source.kind in {"issue", "comment"}):
                    text = snapshot.normalized_text or (snapshot.raw or b"").decode("utf-8", errors="replace")
                    text = _html_text(text)
            except SnapshotError:
                self.document_errors[source.source_id] = "snapshot_unavailable"
                self.warnings.append(
                    f"Snapshot unavailable or invalid for {source.source_id}; evidence remains incomplete."
                )
                # A missing or tampered snapshot is not permission to substitute
                # an older copied excerpt for the observation being audited.
                return self._keep_text(source.source_id, "")
        if not text and not source.snapshot_ref:
            text = "\n".join(e.excerpt for e in self.result.evidence if e.source_id == source.source_id)
        return self._keep_text(source.source_id, text, focus_terms=focus_terms)

    def _keep_text(self, source_id: str, text: str, *, focus_terms: tuple[str, ...] = ()) -> str:
        available = max(0, min(15_000, self.max_characters - self.characters))
        spans = [(0, min(available, len(text)))]
        # Preserve the introduction plus identity-bearing local ranges rather
        # than silently losing a paper declaration late in a long README.
        # Ranges are still bounded, audited and never described as a full read.
        if len(text) > available and available >= 1000 and focus_terms:
            matches = []
            for term in focus_terms:
                if term:
                    pattern = r"\s+".join(re.escape(part) for part in term.split())
                    matches.extend(re.finditer(pattern, text, re.I))
            late = sorted((m.start(), m.end()) for m in matches if m.end() > available)
            if late:
                prefix = available * 2 // 3
                spans = [(0, prefix)]
                remaining = available - prefix
                for start, end in late:
                    if remaining < 200:
                        break
                    start = max(prefix, start - 250)
                    end = min(len(text), max(end + 250, start + 500), start + remaining)
                    if any(start < old_end and end > old_start for old_start, old_end in spans):
                        continue
                    spans.append((start, end))
                    remaining -= end - start
        retained = "\n[Archived source gap omitted]\n".join(text[start:end] for start, end in spans)
        # Delimiters are model input too: they consume the same text budget.
        if len(retained) > available:
            excess = len(retained) - available
            start, end = spans[-1]
            spans[-1] = (start, max(start, end - excess))
            retained = "\n[Archived source gap omitted]\n".join(text[start:end] for start, end in spans)
        self.original_documents[source_id] = text
        self.document_reviews[source_id] = {
            "source_character_count": len(text), "retained_character_count": sum(b - a for a, b in spans),
            "retained_ranges": [{"start": a, "end": b} for a, b in spans],
            "text_complete": spans == [(0, len(text))] and source_id not in self.document_errors,
            "pagination_complete": True,
        }
        if len(retained) < len(text):
            self.warnings.append(f"Text budget truncated {source_id}; this is not a complete source review.")
        self.documents[source_id] = retained
        self.characters += len(retained)
        return retained

    def execute(self, call_id: str, name: str, arguments: str | dict) -> dict:
        """Always return a structured result for the SAME provider call ID.

        Repeated IDs/arguments consume budget but never execute a second GET.
        Invalid/unknown calls are observable tool errors, not model instructions.
        """
        row: dict[str, Any] = {"call_id": call_id, "name": name, "started_at": now()}
        try:
            if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
                raise ValueError("Invalid tool call ID")
            if call_id in self._seen_calls:
                raise ValueError("Duplicate tool call ID")
            self._seen_calls.add(call_id)
            if len(self.calls) >= self.max_calls:
                raise ValueError("Tool call budget exhausted")
            if name not in _ARGUMENTS:
                raise ValueError("Unknown read-only tool")
            payload = json.loads(arguments) if isinstance(arguments, str) else arguments
            validated = _ARGUMENTS[name][0].model_validate(payload, strict=True)
            clean = validated.model_dump()
            row["arguments"] = clean
            key = (name, json.dumps(clean, sort_keys=True))
            if key in self._seen_requests:
                result = dict(self._seen_requests[key], repeated=True)
            else:
                result = self._read(name, clean)
                self._seen_requests[key] = result
        except (ValueError, TypeError, ValidationError) as exc:
            # Pydantic errors can contain untrusted input. Return only the type
            # and a short generic message, not arbitrary model-supplied strings.
            result = {
                "status": "error",
                "error": "invalid_or_disallowed_tool_request",
                "detail": str(exc).split("\n", 1)[0][:180],
            }
        row["result"] = result
        self.calls.append(row)
        return {"call_id": call_id, **result}

    def _read(self, name: str, args: dict) -> dict:
        kind = "repository"
        if self.characters >= self.max_characters:
            raise ValueError("External text budget exhausted")
        revision = None
        if name == "read_public_page":
            source = self.pages.get(args["source_id"])
            if source is None:
                raise ValueError("Page source is outside the discovered paper scope")
            if source.snapshot_ref and source.fetch_status == "ok":
                text = self.existing_document(source)
                return {
                    "status": "ok" if text else "error",
                    "source_id": source.source_id,
                    "url": source.url,
                    "text": text,
                    "snapshot_ref": source.snapshot_ref,
                    "from_snapshot": True,
                }
            url, kind = source.url, source.kind
        else:
            repository = self.repositories.get(args["repository_id"])
            if repository is None:
                raise ValueError("Repository is outside the discovered paper scope")
            repo, revision = repository
            scope = self.repository_scopes.get(args["repository_id"], {})
            requested = scope.get("requested_revision")
            subdir = scope.get("inspected_subdir") or ""
            if (not isinstance(subdir, str) or (subdir and (subdir.startswith("/") or "\\" in subdir
                    or "%" in subdir or any(part in {"", ".", ".."} for part in subdir.split("/"))
                    or any(ord(char) < 32 for char in subdir)))):
                raise ValueError("Inspected repository subtree is invalid")
            if name in {"read_repository_readme", "resolve_repository_revision", "read_repository_file"}:
                if scope.get("requested_ref_path") and not scope.get("ref_scope_resolved", False):
                    raise ValueError("The cited branch/subdirectory scope must be resolved before repository reads")
                if requested is not None and (not isinstance(requested, str) or not requested
                        or any(char in requested for char in "\\%?#")
                        or any(ord(char) < 32 for char in requested)
                        or any(part in {"", ".", ".."} for part in requested.split("/"))):
                    raise ValueError("Cited repository revision is invalid")
            api = f"https://api.github.com/repos/{repo}"
            if name == "read_repository_readme":
                ref = revision or requested
                url = api + "/readme" + ("/" + quote(subdir, safe="/") if subdir else "")
                url += "?ref=" + quote(ref, safe="") if ref else ""
            elif name == "resolve_repository_revision":
                if revision and re.fullmatch(r"[a-f0-9]{40}", revision):
                    return {"status": "ok", "repository_id": args["repository_id"],
                            "inspected_revision": revision, "from_inspected_revision": True,
                            "inspected_subdir": subdir}
                url = api + "/commits/" + quote(requested or "HEAD", safe="")
            elif name == "read_repository_file":
                path = args["path"]
                parsed = PurePosixPath(path)
                if (
                    parsed.is_absolute()
                    or any(x in {"", ".", ".."} for x in path.split("/"))
                    or "\\" in path
                    or "%" in path
                    or any(ord(c) < 32 for c in path)
                    or parsed.suffix.lower() not in _TEXT_EXTENSIONS
                    or any(
                        part.lower().startswith((".env", ".git", "id_rsa", "credentials", "secrets"))
                        for part in parsed.parts
                    )
                ):
                    raise ValueError("Only a safe necessary public text file may be read")
                if not revision or not re.fullmatch(r"[a-f0-9]{40}", revision):
                    raise ValueError("File reading requires an already inspected immutable commit")
                if subdir and not path.startswith(subdir + "/"):
                    path = subdir + "/" + path
                url = api + "/contents/" + quote(path, safe="/") + "?ref=" + revision
            else:
                url = api + f"/issues/{args['number']}"
                kind = "issue"
                if name == "read_issue_comments":
                    url += "/comments?per_page=30&page=1"
                    kind = "comment"
        if self.request_count >= self.request_budget:
            raise ValueError("Shared external request budget exhausted")
        self.request_count += 1
        fetched: Fetch = self.lookup.get(url)
        operation_status = fetched.status
        if name == "resolve_repository_revision" and fetched.status == "ok" and isinstance(fetched.data, dict):
            candidate = fetched.data.get("sha")
            requested = self.repository_scopes.get(args["repository_id"], {}).get("requested_revision")
            if (isinstance(candidate, str) and re.fullmatch(r"[a-f0-9]{40}", candidate)
                    and not (isinstance(requested, str) and re.fullmatch(r"[a-f0-9]{40}", requested)
                             and candidate != requested)):
                revision = candidate
                self.repositories[args["repository_id"]] = (repo, revision)
            else:
                operation_status = "error"
                self.warnings.append("Repository revision could not be resolved to an immutable commit.")
        sid = f"code_model_src_{self.prefix}_{len(self.sources) + 1}"
        parent_ref = getattr(fetched, "snapshot_ref", None)
        # Custom transports/test doubles also get an immutable observation.
        ref = (
            derive_snapshot(parent_ref, kind="model_tool_source", revision=revision,
                            metadata={"source_id": sid, "tool": name})
            if parent_ref
            else capture_snapshot(
                kind="model_tool_source",
                raw=fetched.text.encode("utf-8"),
                normalized_json=fetched.data,
                requested_url=url,
                retrieved_at=fetched.retrieved_at,
                fetch_status=fetched.status,
                http_status=fetched.http_status,
                is_cached=fetched.cached,
                revision=revision,
                metadata={"source_id": sid, "tool": name},
            )
        )
        self.sources.append(
            CredibilityExternalSource(
                source_id=sid,
                kind=kind,
                url=url,
                fetch_status=fetched.status,
                http_status=fetched.http_status,
                retrieved_at=fetched.retrieved_at,
                observed_at=now(),
                is_cached=fetched.cached,
                snapshot_ref=ref,
                association_reason=f"Read-only {name}; no code executed.",
            )
        )
        invalid_message = (isinstance(fetched.data, list) and kind in {"issue", "comment"}
                           and _scoped_message_rows(fetched.data, url) is None)
        if invalid_message:
            operation_status = "error"
            self.document_errors[sid] = "original_message_not_unique"
            self.warnings.append(f"Original message unavailable or ambiguous for {sid}; evidence remains incomplete.")
        original_text = ""
        if fetched.status == "ok" and not invalid_message:
            original_text = _text_from_data(fetched.data, url=url if kind in {"issue", "comment"} else "")
            if not original_text and not (isinstance(fetched.data, list) and kind in {"issue", "comment"}):
                original_text = _html_text(fetched.text)
        paper = paper_from_context(self.ctx)
        focus_terms = tuple(term for term in (paper.title if paper else None,
                                             paper.arxiv_id if paper else self.ctx.source.get("arxiv_id")) if term)
        text = (self._keep_text(sid, original_text,
                               focus_terms=focus_terms if name == "read_repository_readme" else ())
                if fetched.status == "ok" else "")
        if text:
            self.evidence.append(
                CredibilityEvidence(
                    evidence_id=f"code_model_ev_{self.prefix}_{len(self.evidence) + 1}",
                    origin="external",
                    mode="quote",
                    excerpt=text,
                    paper_locator=None,
                    source_id=sid,
                    location_confidence="high",
                )
            )
        if fetched.has_next:
            if sid in self.document_reviews:
                self.document_reviews[sid]["pagination_complete"] = False
            self.warnings.append(f"Pagination remains incomplete for {sid}.")
        return {
            "status": operation_status,
            "source_id": sid,
            "url": url,
            "text": text,
            "snapshot_ref": ref,
            "truncated": len(text) < len(original_text) or fetched.has_next,
            "inspected_revision": revision,
        }

    def prefetch(self, repository_ids: list[str], discussions: list) -> None:
        """A small deterministic plan, explicitly NOT a native tools transcript."""
        for rid in repository_ids:
            if len(self.calls) >= self.max_calls:
                return
            self.execute(f"prefetch_{len(self.calls) + 1}", "read_repository_readme", {"repository_id": rid})
        sources = {s.source_id: s for s in self.result.sources}
        for discussion in discussions:
            for sid in discussion.source_ids:
                source = sources.get(sid)
                if source is not None:
                    self.existing_document(source)
            # A fresh comments read is useful only when no saved comment exists;
            # do not refetch the complete saved thread merely to appear agentic.
            has_comment = any(sources[s].kind == "comment" for s in discussion.source_ids if s in sources)
            if has_comment or len(self.calls) >= self.max_calls:
                continue
            number = next(
                (
                    int(match[1])
                    for sid in discussion.source_ids
                    if sid in sources and (match := _ISSUE.search(sources[sid].url))
                ),
                None,
            )
            if number:
                self.execute(
                    f"prefetch_{len(self.calls) + 1}",
                    "read_issue_comments",
                    {"repository_id": discussion.repository_id, "number": number},
                )
