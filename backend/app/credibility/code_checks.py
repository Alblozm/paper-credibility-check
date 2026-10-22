"""Task three: conservative, traceable code and public reproduction checks.

The fixed check_code(ctx) seam reuses parsed documents and paper locators.
No LLM or repository code is executed; draft scores always remain empty.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import replace
from html import unescape
from urllib.parse import quote, urljoin, urlsplit

from app.credibility.code_lookup import LOOKUP, Fetch, now
from app.credibility.code_repository_scope import (
    ProjectHTML,
    implementation_paths,
    initializer_is_placeholder,
    readme_entrypoints,
    repository_scope,
    supported_paper_ids,
)
from app.credibility.context import CheckContext
from app.credibility.locate import PaperSpanCandidate, locate_paper_span
from app.credibility.models import (
    CodeCheckResult,
    CredibilityCodeChecks,
    CredibilityCodeSearch,
    CredibilityDimension,
    CredibilityDiscussion,
    CredibilityEvidence,
    CredibilityExternalSource,
    CredibilityFinding,
    CredibilityRepository,
)
from app.credibility.paper_link_classifier import OWN_ROLES
from app.credibility.snapshots import SnapshotError, capture_snapshot, derive_snapshot, read_snapshot

CODE_CHECKS_VERSION = "code-checks-2.6-model-paper-links"

_DATA_DIR = re.compile(
    r"(?i)\.?(?:data|datasets?|results?|outputs?|assets|figures?|images?|imgs?|logs?|cache|checkpoints?|"
    r"docs?|notebooks?|examples?|tests?|\.github)"
)
_SHALLOW_DIRS = 6
_SCOPES = ("paper_body", "appendix", "supplement", "project_page", "repository", "discussion_list")
_URL = re.compile(r"https?://[^\s<>\[\]\"']+", re.I)
_OWN_CODE = re.compile(
    r"(?:our|official|source)\s+(?:implementation|code|repository)|"
    r"(?:code|implementation)\s+(?:(?:is|are)\s+)?(?:available|released)|"
    r"(?:data(?:sets?)?|code)(?:\s*(?:,|and|&)\s*(?:data(?:sets?)?|code))+\s+(?:is|are)\s+available|"
    r"(?:we\s+(?:release|provide))\s+(?:the\s+)?code",
    re.I,
)
_THEORY = re.compile(
    r"\b(?:this (?:paper|work) is (?:purely|entirely) theoretical|"
    r"we (?:present|provide) a purely theoretical (?:analysis|study))\b",
    re.I,
)
_EMPIRICAL = re.compile(r"\b(?:we (?:conduct|evaluate|train|experiment)|our experiments|empirical results)\b", re.I)
_TOPIC = re.compile(r"reproduc|replicat|dataset|data set|benchmark|accuracy|training|checkpoint|results?", re.I)
_FAIL = re.compile(
    r"\b(?:I|we)\s+(?:could not|cannot|can't|failed to|was unable to|were unable to)\s+"
    r"(?:reproduce|replicate)|\b(?:dataset|data|checkpoint)\b.{0,60}"
    r"\b(?:missing|unavailable|broken|corrupt|404)\b",
    re.I | re.S,
)
_SUCCESS = re.compile(
    r"\b(?:I|we)\s+(?:(?:have|has)\s+)?(?:successfully\s+)?"
    r"(?:reproduced|replicated)\b",
    re.I,
)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _urls(text: str) -> list[str]:
    return [m.rstrip(".,;:)}，。") for m in _URL.findall(unescape(text))]


def _paper_urls(text: str):
    """Recover only explicit GitHub paths split immediately after the host slash.

    Yield the original span as well, so evidence quotes never contain a repaired URL.
    """
    wrapped = list(re.finditer(r"https://(?:www\.)?github\.com/[ \t\r\n]+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text))
    matches = wrapped + [m for m in _URL.finditer(text) if not any(w.start() <= m.start() < w.end() for w in wrapped)]
    for match in sorted(matches, key=lambda m: m.start()):
        raw = match[0].rstrip(".,;:)}，。")
        url = re.sub(r"(?<=github\.com/)\s+", "", raw)
        yield url, match.start(), match.start() + len(raw)


def _repo(url: str) -> str | None:
    try:
        p = urlsplit(url)
        parts = p.path.strip("/").split("/")
        if p.hostname not in ("github.com", "www.github.com") or p.username or p.password:
            return None
        if p.scheme != "https" or p.port not in (None, 443) or len(parts) < 2:
            return None
        owner, name = parts[:2]
        name = name.removesuffix(".git")
        if owner.lower() in {"features", "topics", "settings", "orgs", "users", "search", "marketplace"}:
            return None
        if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", x) and x not in {".", ".."} for x in (owner, name)):
            return None
        return f"{owner}/{name}"
    except ValueError:
        return None


def _scope(block) -> str:
    text = f"{block.section_role} {block.section_title}".lower()
    return "appendix" if "appendix" in text else "supplement" if "supplement" in text else "paper_body"


def _is_reference(block) -> bool:
    return block.section_role == "references" or bool(re.search(r"references|bibliography", block.section_title, re.I))


def classify_feedback(text: str) -> str:
    """Only explicit first-person reports; questions and negation are not success."""
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith((">", "```")))
    if _FAIL.search(text):
        return "failure_reported"
    if _SUCCESS.search(text) and not re.search(r"\b(?:not|never|if|wish|whether)\b|\?", text, re.I):
        return "success_reported"
    return "discussion_only"


class _Check:
    def __init__(self, ctx: CheckContext):
        self.ctx = ctx
        self.evidence: list[CredibilityEvidence] = []
        self.sources: list[CredibilityExternalSource] = []
        self.repositories: list[CredibilityRepository] = []
        self.discussions: list[CredibilityDiscussion] = []
        self.limitations = [
            "No code was executed. Public reports are not system reproduction results.",
            "Bounded check: at most 4 repositories, 4 official pages, 2 issue pages per repository, "
            "20 issues per page and 2 comment pages per issue; 40 external requests per check.",
            "Only GitHub repositories and public issues/comments are supported; GitHub Discussions is not checked.",
            "Rule-based English feedback classification requires human review; conditions are not assumed equivalent.",
            "Binary supplements are not parsed here; "
            "only supplied parsed supplement blocks and textual pages are checked.",
        ]
        self.searches = {
            s: CredibilityCodeSearch(
                scope_id=f"code_search_{s}",
                scope_type=s,
                outcome="not_checked",
                reason="This scope has not been inspected.",
                evidence_ids=[],
                source_ids=[],
            )
            for s in _SCOPES
        }
        self.model_calls: list = []
        self.requests = 0
        self.max_requests = 40
        self.plan_failed = False
        self.followup_state = {}
        self.discussion_complete = True
        self.discussion_ran = False

    def paper(self, block, excerpt: str) -> str | None:
        if not block.chunk_id or block.page_start < 1:
            self.limitations.append("A candidate has no trustworthy paper locator.")
            return None
        located, problems = locate_paper_span(
            self.ctx,
            PaperSpanCandidate(
                evidence_id=f"code_ev_{len(self.evidence) + 1}", mode="quote", excerpt=excerpt, chunk_id=block.chunk_id
            ),
        )
        if not located:
            self.limitations.extend(problems)
            return None
        self.evidence.append(located.evidence)
        self.searches[_scope(block)].evidence_ids.append(located.evidence.evidence_id)
        return located.evidence.evidence_id

    def source(self, result: Fetch, kind: str, reason: str, *, url: str | None = None) -> str:
        sid = f"code_src_{len(self.sources) + 1}"
        source_metadata = {"source_id": sid, "source_url": url or result.url, "association_reason": reason}
        snapshot_ref = (
            derive_snapshot(result.snapshot_ref, kind=kind, metadata=source_metadata)
            if result.snapshot_ref
            else capture_snapshot(
                kind=kind,
                normalized_json=result.data,
                normalized_text=result.text or None,
                requested_url=result.url,
                final_url=result.url,
                retrieved_at=result.retrieved_at,
                fetch_status=result.status,
                http_status=result.http_status,
                is_cached=result.cached,
                metadata={**source_metadata, "raw_available": False},
            )
        )
        self.sources.append(
            CredibilityExternalSource(
                source_id=sid,
                kind=kind,
                url=url or result.url,
                fetch_status=result.status,
                http_status=result.http_status,
                retrieved_at=result.retrieved_at,
                observed_at=now(),
                is_cached=result.cached,
                snapshot_ref=snapshot_ref,
                association_reason=reason,
            )
        )
        return sid

    def external(self, text: str, sid: str, *, mode: str = "quote") -> str:
        eid = f"code_ev_{len(self.evidence) + 1}"
        self.evidence.append(
            CredibilityEvidence(
                evidence_id=eid,
                origin="external",
                mode=mode,
                excerpt=text,
                paper_locator=None,
                source_id=sid,
                location_confidence="high",
            )
        )
        return eid

    def fetch(self, url: str, kind: str, scope: str, reason: str) -> tuple[Fetch, str]:
        if self.requests >= self.max_requests:
            result = Fetch(url, "error", None, now())
            self.limitations.append("External request budget exhausted; remaining queries were not performed.")
        else:
            self.requests += 1
            result = LOOKUP.get(url)
        sid = self.source(result, kind, reason)
        search = self.searches[scope]
        search.source_ids.append(sid)
        if result.status != "ok":
            self.plan_failed = True
            search.outcome = "failed"
            search.reason = f"At least one fetch failed: {result.status}."
            self.limitations.append(f"{url}: {result.status}.")
        elif search.outcome != "failed":
            search.outcome, search.reason = "checked", "Inspected the linked sources within the stated limits."
        return result, sid

    def link_roles(self, blocks) -> dict | None:
        """Model roles keyed by (chunk_id, offset); None keeps the sentence rules."""
        from app.config import get_settings

        if not get_settings().credibility_paper_link_classifier_enabled or self.ctx.model_id in {None, "not_called"}:
            return None
        from app.credibility.paper_link_classifier import MAX_LINKS, classify_paper_links

        rows, keys = [], {}
        for block in blocks:
            for url, start, end in _paper_urls(block.text):
                if len(rows) >= MAX_LINKS:
                    break
                link_id = f"L{len(rows) + 1}"
                keys[link_id] = (block.chunk_id, start)
                rows.append({
                    "link_id": link_id, "url": url, "section": block.section_title or block.section_role,
                    "page": block.page_start,
                    "text_before": block.text[max(0, start - 300):start],
                    "text_after": block.text[end:end + 150],
                })
        roles = classify_paper_links(self.ctx, rows, self.model_calls)
        return None if roles is None else {keys[link_id]: role for link_id, role in roles.items()}

    def matches_paper(self, text: str) -> bool:
        paper = self.ctx.doc.paper
        title = _norm(paper.title or "")
        title_match = len(title.split()) >= 4 and title in _norm(text)
        arxiv = (paper.arxiv_id or str(self.ctx.source.get("arxiv_id") or "")).removesuffix(".pdf")
        arxiv = re.search(r"\d{4}\.\d{4,5}", arxiv)
        id_match = bool(arxiv and re.search(rf"(?<!\d){re.escape(arxiv[0])}(?:v\d+)?(?!\d)", text))
        return title_match or id_match

    def run(self) -> CodeCheckResult:
        doc = self.ctx.doc
        if doc is None or not doc.blocks:
            self.limitations.append("No parsed text blocks are available; no check was run.")
            return self.finish("undetermined", "not_run", "unable_to_verify", "unable_to_verify")
        blocks = [b for b in doc.blocks if not _is_reference(b)]
        for scope in ("paper_body", "appendix", "supplement"):
            found = [b for b in blocks if _scope(b) == scope]
            self.searches[scope].outcome = "checked" if found else "not_checked"
            self.searches[scope].reason = (
                "Scanned available parsed blocks for code and official-page links."
                if found
                else "No reliably identified parsed blocks; absence from the PDF is not proven."
            )
        theory = next((b for b in blocks if _THEORY.search(b.text)), None)
        if theory and not any(
            _EMPIRICAL.search(b.text) or b.section_role in {"experiments", "results"} for b in blocks
        ):
            if self.paper(theory, theory.text):
                for search in self.searches.values():
                    search.outcome, search.reason = (
                        "not_applicable",
                        "Paper explicitly describes purely theoretical work.",
                    )
                return self.finish("not_applicable", "completed", "not_applicable", "not_applicable")

        candidates: dict[str, list[str]] = {}
        candidate_urls: dict[str, str] = {}
        pages: dict[str, tuple[str, list[str]]] = {}
        roles = self.link_roles(blocks)
        for block in blocks:
            for url, start, end in _paper_urls(block.text):
                repo = _repo(url)
                # Context must bind a URL to a code statement in its own sentence/window.
                prefix = re.split(r"(?<=[.!?])\s+", block.text[:start])[-1][-200:]
                context = prefix + block.text[start:end]
                role = roles.get((block.chunk_id, start)) if roles is not None else None
                if roles is not None:
                    # The selected model labels what the paper says about each printed URL;
                    # fetching and README/page matching below remain deterministic.
                    owned = role in OWN_ROLES and bool(repo)
                    official = role in OWN_ROLES and not repo
                else:
                    owned = (
                        bool(_OWN_CODE.search(prefix))
                        and not _urls(prefix)
                        and not re.search(r"\b(?:not|unavailable|baseline|previous|their)\b", prefix, re.I)
                    )
                    official = bool(
                        re.search(r"(?:our|official)\s+(?:project|supplement)|project page", prefix, re.I)
                    )
                if repo:
                    eid = self.paper(block, context)
                    if owned and eid and not candidates.get(repo):
                        candidate_urls[repo] = url
                    else:
                        candidate_urls.setdefault(repo, url)
                    candidates.setdefault(repo, []).extend([eid] if owned and eid else [])
                elif (owned or official) and urlsplit(url).hostname not in ("github.com", "www.github.com"):
                    eid = self.paper(block, context)
                    kind = "supplement" if _scope(block) == "supplement" else "project_page"
                    pages[url] = (kind, [eid] if eid else [])
                elif "github.com" in url:
                    self.limitations.append(f"Incomplete or unsupported repository URL was not confirmed: {url}")
        for link in doc.links:
            repo = _repo(link.url)
            if repo:
                candidate_urls.setdefault(repo, link.url)
                candidates.setdefault(repo, [])  # links.json alone never confirms association.
            elif link.link_type in {"code", "project", "project_page", "supplement", "supplementary"}:
                self.limitations.append(f"Unlocated/unsupported link remains a candidate only: {link.url}")

        for url, (kind, paper_ids) in list(pages.items())[:4]:
            fetched, sid = self.fetch(url, kind, kind, "Page printed next to a paper code statement.")
            if fetched.status != "ok":
                continue
            page = ProjectHTML(fetched.text)
            text = page.text
            associated = self.matches_paper(text[:6000]) and bool(paper_ids)
            for anchor in page.anchors:
                candidate_url = urljoin(url, unescape(anchor["href"]))
                repo = _repo(candidate_url)
                if not repo:
                    continue
                candidate_urls.setdefault(repo, candidate_url)
                ids = []
                label = _norm(anchor["label"])
                baseline = re.search(r"\bbaselines?\b|previous work|related work|references", anchor["section"], re.I)
                local_baseline = re.search(r"\bbaseline(?:s| implementation)?\s*[:(]?\s*$", anchor["context"], re.I)
                if (
                    associated
                    and not baseline
                    and not local_baseline
                    and label
                    in {
                        "code",
                        "source code",
                        "github",
                        "implementation",
                        "repository",
                        "official code",
                    }
                ):
                    ids = paper_ids + [self.external(text[:6000], sid), self.external(anchor["raw"], sid)]
                if ids and not candidates.get(repo):
                    candidate_urls[repo] = candidate_url
                candidates.setdefault(repo, []).extend(ids)
        if len(pages) > 4 or len(candidates) > 4:
            self.limitations.append("Candidate limit reached; remaining candidates were not checked.")
        for repo, paper_ids in list(candidates.items())[:4]:
            self.repository(repo, list(dict.fromkeys(paper_ids)), original_url=candidate_urls.get(repo))
        available = any(
            r.association == "confirmed" and r.availability == "accessible" and r.implementation_evidence_ids
            for r in self.repositories
        )
        availability = "available" if available else "unable_to_verify" if candidates or pages else "not_found_in_scope"
        signals = {d.signal for d in self.discussions if d.relevance == "relevant"}
        signal = next((s for s in ("failure_reported", "success_reported", "discussion_only") if s in signals), None)
        if signal is None:
            signal = (
                "no_feedback_found"
                if self.discussion_ran and self.discussion_complete and not self.discussions
                else "unable_to_verify"
            )
        if {"failure_reported", "success_reported"} <= signals:
            self.limitations.append(
                "Both success and failure reports exist; inspect individual conditions, not a net verdict."
            )
        return self.finish(
            "applicable" if available or any(_EMPIRICAL.search(b.text) for b in blocks) else "undetermined",
            "partial" if self.plan_failed else "completed",
            availability,
            signal,
        )

    def _scope_metadata(self, repo: str, state: dict) -> dict:
        return {
            "repository": repo,
            "requested_revision": state.get("requested_revision"),
            "inspected_subdir": state.get("inspected_subdir"),
            "original_url": state.get("original_url"),
            "ref_scope_resolved": state.get("ref_scope_resolved"),
        }

    def _revision(self, repo: str, state: dict, metadata: dict) -> str | None:
        api = f"https://api.github.com/repos/{repo}"
        if state.get("scope_invalid"):
            self.plan_failed = True
            self.limitations.append(f"{repo}: cited repository path is unsafe or unsupported.")
            return None
        tail = state.get("requested_ref_path") or []
        if tail and re.fullmatch(r"[a-f0-9]{7,40}", tail[0], re.I):
            state["requested_revision"] = tail[0]
            remainder = tail[1:-1] if state.get("target_file") else tail[1:]
            state["inspected_subdir"] = "/".join(remainder) or None
            state["ref_scope_resolved"] = True
        if tail and not state.get("ref_scope_resolved"):
            found = []
            for kind in ("heads", "tags"):
                fetched, _ = self.fetch(
                    api + f"/git/matching-refs/{kind}/" + quote(tail[0], safe=""),
                    "repository",
                    "repository",
                    "Resolve the cited ref/path without guessing HEAD.",
                )
                if fetched.status != "ok" or not isinstance(fetched.data, list):
                    continue
                for record in fetched.data:
                    ref = record.get("ref") if isinstance(record, dict) else None
                    if not isinstance(ref, str) or not ref.startswith(f"refs/{kind}/"):
                        continue
                    name = ref[len(f"refs/{kind}/") :]
                    prefix = name.split("/")
                    if tail[: len(prefix)] == prefix:
                        found.append((len(prefix), kind == "heads", name))
            if not found:
                self.plan_failed = True
                self.limitations.append(
                    f"{repo}: the cited ref/path could not be resolved; default HEAD was not substituted."
                )
                return None
            length, _, requested = max(found)
            state["requested_revision"] = requested
            remainder = tail[length:]
            if state.get("target_file"):
                remainder = remainder[:-1]
            state["inspected_subdir"] = "/".join(remainder) or None
            state["ref_scope_resolved"] = True
        requested = state.get("requested_revision") or metadata.get("default_branch")
        if not isinstance(requested, str) or not requested:
            self.plan_failed = True
            self.limitations.append(f"{repo}: no resolvable cited/default revision was supplied.")
            return None
        fetched, _ = self.fetch(
            api + "/commits/" + quote(requested, safe=""),
            "repository",
            "repository",
            "Resolve the cited revision to an immutable commit.",
        )
        sha = fetched.data.get("sha") if isinstance(fetched.data, dict) and fetched.status == "ok" else None
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
            self.plan_failed = True
            self.limitations.append(f"{repo}: revision did not resolve to an immutable commit.")
            return None
        if re.fullmatch(r"[a-f0-9]{7,40}", requested, re.I) and not sha.startswith(requested.casefold()):
            self.plan_failed = True
            self.limitations.append(f"{repo}: returned commit differs from the explicitly cited SHA.")
            return None
        state["inspected_revision"] = sha
        return sha

    def _readme(self, api: str, revision: str, state: dict) -> tuple[str, str | None]:
        subdir = state.get("inspected_subdir") or ""
        url = api + "/readme" + ("/" + quote(subdir, safe="/") if subdir else "") + "?ref=" + revision
        response, sid = self.fetch(
            url, "repository", "repository", "README at the inspected commit and cited subdirectory."
        )
        data = response.data if isinstance(response.data, dict) else {}
        try:
            if response.status == "ok" and data.get("encoding") == "base64":
                state["readme_readable"] = True
                return base64.b64decode(re.sub(r"[ \t\r\n]", "", data.get("content", "")), validate=True).decode(
                    "utf-8", errors="replace"
                ), sid
        except (ValueError, TypeError, binascii.Error):
            pass
        state["readme_readable"] = False
        self.plan_failed = True
        self.limitations.append(f"{api}: scoped README was unavailable or could not be decoded.")
        return "", sid

    def _shallow_tree(self, api: str, revision: str, failed: Fetch, failed_sid: str, repo: str):
        """Read the root and a few non-data top-level directories one level at a time."""
        root, root_sid = self.fetch(
            api + f"/git/trees/{revision}", "repository", "repository",
            "Recursive tree unavailable; read the root tree without recursion.",
        )
        if root.status != "ok" or not isinstance(root.data, dict) or not isinstance(root.data.get("tree"), list):
            return failed, failed_sid
        rows = [row for row in root.data["tree"] if isinstance(row, dict)]
        folders = [
            row for row in rows
            if row.get("type") == "tree" and isinstance(row.get("sha"), str) and not _DATA_DIR.fullmatch(
                str(row.get("path", "")))
        ][:_SHALLOW_DIRS]
        for folder in folders:
            child, _ = self.fetch(
                api + f"/git/trees/{folder['sha']}", "repository", "repository",
                "Read one top-level source directory without recursion.",
            )
            if child.status == "ok" and isinstance(child.data, dict) and isinstance(child.data.get("tree"), list):
                rows += [{**row, "path": f"{folder['path']}/{row.get('path')}"}
                         for row in child.data["tree"] if isinstance(row, dict)]
        self.limitations.append(
            f"{repo}: the full file tree exceeded the size limit; the root and up to {_SHALLOW_DIRS} "
            "top-level source directories were read one level deep."
        )
        return replace(root, data={**root.data, "tree": rows, "truncated": True}), root_sid

    def _inspect_implementation(
        self, repo: str, repository: CredibilityRepository, state: dict, readme: str, read_sid: str | None
    ) -> None:
        api = f"https://api.github.com/repos/{repo}"
        revision = repository.inspected_revision
        if not revision or not state.get("ref_scope_resolved", True):
            return
        if not state.get("implementation_checked"):
            tree, tree_sid = self.fetch(
                api + f"/git/trees/{revision}?recursive=1",
                "repository",
                "repository",
                "Inspect implementation paths at the cited immutable revision/subdirectory.",
            )
            if tree.status != "ok":
                # Data-heavy repositories can exceed the recursive tree size cap.
                tree, tree_sid = self._shallow_tree(api, revision, tree, tree_sid, repo)
            rows = tree.data.get("tree", []) if tree.status == "ok" and isinstance(tree.data, dict) else []
            tree_readable = (
                tree.status == "ok"
                and isinstance(tree.data, dict)
                and "tree" in tree.data
                and isinstance(rows, list)
                and all(
                    isinstance(row, dict)
                    and isinstance(row.get("path"), str)
                    and row.get("type") in {"blob", "tree", "commit"}
                    for row in rows
                )
            )
            if not tree_readable:
                self.plan_failed = True
            rows = rows if isinstance(rows, list) else []
            files = implementation_paths(rows, state)
            if isinstance(tree.data, dict) and tree.data.get("truncated"):
                self.limitations.append(f"{repo}: file tree was truncated by GitHub; coverage remains bounded.")
            subdir = state.get("inspected_subdir") or ""
            scoped = [
                str(row.get("path"))
                for row in rows
                if isinstance(row, dict)
                and row.get("type") == "blob"
                and (not subdir or str(row.get("path", "")).startswith(subdir + "/"))
            ]
            dependencies = [
                path
                for path in scoped
                if re.search(
                    r"(?:^|/)(?:requirements[^/]*\.txt|pyproject\.toml|environment\.ya?ml|Dockerfile|package\.json|setup\.py)$",
                    path,
                    re.I,
                )
            ]
            entries = [
                row["path"]
                for row in files
                if re.search(
                    r"(?:^|/)(?:train|main|run|evaluate|eval|inference|predict|demo)(?:[^/]*)\.", row["path"], re.I
                )
            ]
            entries = list(
                dict.fromkeys(
                    entries + readme_entrypoints(readme, [row["path"] for row in files], state.get("inspected_subdir"))
                )
            )
            observed_ids = [tree_sid] + ([read_sid] if read_sid else [])
            # Read at most one implementation file and one dependency manifest;
            # these are observations, never an execution or a runnable verdict.
            candidates = sorted(
                (row["path"] for row in files), key=lambda path: (path.rsplit("/", 1)[-1] == "__init__.py", path)
            )
            sample_paths = list(dict.fromkeys((entries or candidates)[:1] + dependencies[:1]))
            inspected_files, observed_text = [], {}
            for path in sample_paths:
                observed, observed_sid = self.fetch(
                    api + "/contents/" + quote(path, safe="/") + "?ref=" + revision,
                    "repository",
                    "repository",
                    "Read a bounded implementation/setup file; do not execute.",
                )
                observed_ids.append(observed_sid)
                inspected_file = {
                    "path": path,
                    "source_id": observed_sid,
                    "fetch_status": observed.status,
                    "content_read": False,
                }
                inspected_files.append(inspected_file)
                if observed.status == "ok" and isinstance(observed.data, dict):
                    content = observed.data.get("content")
                    if observed.data.get("encoding") == "base64" and isinstance(content, str):
                        try:
                            text = base64.b64decode(re.sub(r"[ \t\r\n]", "", content), validate=True).decode(
                                "utf-8", errors="replace"
                            )
                            inspected_file["content_read"] = True
                            observed_text[path] = text
                            if text:
                                self.external(text[:12_000], observed_sid)
                        except (ValueError, binascii.Error):
                            self.plan_failed = True
                if not inspected_file["content_read"]:
                    self.plan_failed = True
            excluded_implementation_paths = []
            if (
                len(files) == 1
                and files[0]["path"].rsplit("/", 1)[-1] == "__init__.py"
                and files[0]["path"] in observed_text
                and initializer_is_placeholder(observed_text[files[0]["path"]])
            ):
                excluded_implementation_paths = [files[0]["path"]]
                files, entries = [], []
                self.limitations.append(
                    f"{repo}: the sole observed source is an import/metadata-only package initializer; "
                    "it does not establish a research implementation."
                )
            if files:
                repository.implementation_evidence_ids.append(
                    self.external(json.dumps(files[:20], ensure_ascii=False), tree_sid, mode="paraphrase")
                )
            notes = [
                line.strip()
                for line in readme.splitlines()
                if re.search(
                    r"data(?:set)?|checkpoint|weights?|pretrained|download|install|requirements|train|inference",
                    line,
                    re.I,
                )
            ][:20]
            inspection_status = (
                "completed"
                if tree_readable
                and state.get("readme_readable", bool(readme))
                and all(row["content_read"] for row in inspected_files)
                else "partial"
            )
            facts = {
                **self._scope_metadata(repo, state),
                "inspected_revision": revision,
                "observation_type": "aggregate_inspection",
                "inspection_status": inspection_status,
                "readme_source_id": read_sid,
                "entrypoint_paths": entries[:20],
                "dependency_manifest_paths": dependencies[:20],
                "data_weights_setup_notes": notes,
                "source_ids": observed_ids,
                "inspected_files": inspected_files,
                "source_code_executed": False,
                "runnable_status": "not_executed",
                "implementation_paths": [row["path"] for row in files[:20]],
                "excluded_implementation_paths": excluded_implementation_paths,
            }
            proof = capture_snapshot(
                kind="code_implementation_inspection",
                normalized_json=facts,
                requested_url=api,
                final_url=api,
                retrieved_at=now(),
                fetch_status="ok",
                http_status=200,
                revision=revision,
                path=subdir or None,
                metadata={"repository_id": repository.repository_id, **self._scope_metadata(repo, state)},
            )
            summary = Fetch(api, "ok", 200, now(), data=facts, snapshot_ref=proof)
            sid = self.source(
                summary, "repository", "Source-bound implementation/setup observations; code was not executed."
            )
            self.external(json.dumps(facts, ensure_ascii=False), sid, mode="paraphrase")
            state["implementation_checked"] = True
        if not state.get("discussions_checked"):
            self.inspect_discussions(api, repository.repository_id, readme, read_sid)
            state["discussions_checked"] = True
        state["inspection_status"] = "completed" if not self.plan_failed else "partial"

    def repository(
        self,
        repo: str,
        paper_ids: list[str],
        *,
        original_url: str | None = None,
        allow_readme_confirmation: bool = True,
    ) -> None:
        api = f"https://api.github.com/repos/{repo}"
        fetched, sid = self.fetch(api, "repository", "repository", f"Candidate GitHub repository {repo}.")
        metadata = fetched.data if isinstance(fetched.data, dict) else {}
        valid = fetched.status == "ok" and isinstance(metadata.get("full_name"), str)
        rid = f"code_repo_{len(self.repositories) + 1}"
        state = repository_scope(original_url or f"https://github.com/{repo}")
        state.update({"repository": repo, "default_branch": metadata.get("default_branch"), "metadata_source_id": sid})
        self.followup_state[rid] = state
        readme, read_sid, revision = "", None, None
        association_ids = list(paper_ids)
        if valid:
            revision = self._revision(repo, state, metadata)
            if revision:
                readme, read_sid = self._readme(api, revision, state)
                state["readme_source_id"] = read_sid
                intro = re.split(r"(?im)^#+\s*(?:references|citation|bibtex)|^if you find", readme[:2500])[0]
                if allow_readme_confirmation and self.matches_paper(intro) and _OWN_CODE.search(intro):
                    association_ids.append(self.external(intro, read_sid))
        confirmed = bool(association_ids) and not state.get("scope_invalid")
        repository = CredibilityRepository(
            repository_id=rid,
            source_id=sid,
            association="confirmed" if confirmed else "uncertain",
            association_reason="Located paper code statement or explicit implementation README matches this paper."
            if confirmed
            else "Candidate URL alone does not establish ownership by this paper.",
            association_evidence_ids=association_ids,
            availability="accessible"
            if valid
            else "inaccessible"
            if fetched.status in {"not_found", "access_denied"}
            else "unknown",
            implementation_evidence_ids=[],
            inspected_revision=revision,
        )
        self.repositories.append(repository)
        if valid and confirmed:
            self._inspect_implementation(repo, repository, state, readme, read_sid)

    def listing(self, url: str, reason: str) -> list[tuple[dict, Fetch, str]]:
        rows = []
        for page in (1, 2):
            fetched, sid = self.fetch(url + f"&page={page}", "discussion_listing", "discussion_list", reason)
            if fetched.status != "ok" or not isinstance(fetched.data, list):
                self.discussion_complete = False
                return rows
            self.discussion_ran = True
            for row in fetched.data:
                if isinstance(row, dict):
                    rows.append((row, fetched, sid))
                else:
                    self.discussion_complete = False
            if not fetched.has_next:
                return rows
        self.discussion_complete = False
        self.limitations.append(f"Pagination limit reached for {url}; no exhaustive absence conclusion is possible.")
        return rows

    def inspect_discussions(self, api: str, rid: str, readme: str, read_sid: str | None) -> None:
        paper_ids = supported_paper_ids(readme)
        shared_repository = len(paper_ids) > 1
        self.followup_state.setdefault(rid, {})["shared_paper_ids"] = paper_ids
        self.followup_state[rid]["supported_paper_ids_basis"] = "official_intro_or_supported_papers_section"
        authors = {_norm(a) for a in self.ctx.doc.paper.authors if len(_norm(a).split()) >= 2}
        # Explicit full-name GitHub profile links are an auditable author identity clue.
        author_logins = {
            login.casefold()
            for name, login in re.findall(r"\[([^\]]+)\]\(https://github\.com/([A-Za-z0-9-]+)/?\)", readme)
            if _norm(name) in authors
        }
        for issue, fetched, _ in self.listing(
            api + "/issues?state=all&sort=updated&per_page=20", "Public issues, all states."
        ):
            if "pull_request" in issue:
                continue
            number = issue.get("number")
            if not isinstance(number, int):
                self.discussion_complete = False
                continue
            comments = []
            if issue.get("comments", 0):
                comments = self.listing(api + f"/issues/{number}/comments?per_page=100", "Public issue comments.")
            messages = [(issue, fetched, "issue")] + [(row, fetch, "comment") for row, fetch, _ in comments]
            topic = any(
                _TOPIC.search(str(row.get("title") or "") + " " + str(row.get("body") or "")) for row, _, _ in messages
            )
            if not topic:
                continue
            thread_text = "\n".join(
                str(row.get("title") or "") + "\n" + str(row.get("body") or "") for row, _, _ in messages
            )
            relevance = "undetermined" if shared_repository and not self.matches_paper(thread_text) else "relevant"
            if relevance == "undetermined":
                self.limitations.append("A shared-repository thread does not identify which paper it concerns.")
            source_ids, evidence_ids, responses, signals = [], [], [], []
            for message, fetch, kind in messages:
                url = message.get("html_url")
                if not isinstance(url, str) or not url.startswith("https://github.com/"):
                    self.discussion_complete = False
                    continue
                user = message.get("user") or {}
                login = user.get("login", "unknown") if isinstance(user, dict) else "unknown"
                body = str(message.get("body") or "")
                title = str(message.get("title") or "")
                text = (title + "\n" + body).strip()
                sid = self.source(
                    fetch,
                    kind,
                    f"Account {login}; repository role "
                    f"{message.get('author_association', 'unknown')}; posted "
                    f"{message.get('created_at', 'unknown')}. This is a public report, not verified reproduction.",
                    url=url,
                )
                source_ids.append(sid)
                if text:
                    evidence_ids.append(self.external(text, sid))
                if str(login).casefold() in author_logins and kind == "comment" and read_sid:
                    responses.append(sid)
                    source_ids.append(read_sid)
                    for identity in re.finditer(r"\[[^\]]+\]\(https://github\.com/[A-Za-z0-9-]+/?\)", readme):
                        if f"github.com/{login}".casefold() in identity[0].casefold():
                            evidence_ids.append(self.external(identity[0], read_sid))
                signals.append(classify_feedback(body))
            if not source_ids:
                continue
            signal = next(s for s in ("failure_reported", "success_reported", "discussion_only") if s in signals)
            self.discussions.append(
                CredibilityDiscussion(
                    discussion_id=f"code_disc_{len(self.discussions) + 1}",
                    repository_id=rid,
                    source_ids=source_ids,
                    evidence_ids=evidence_ids,
                    signal=signal,
                    relevance=relevance,
                    independence="unknown",
                    conditions="See quoted thread; datasets, metrics and settings were not normalized.",
                    resolution="unknown",
                    author_response_source_ids=responses,
                    scoring_eligible=False,
                    reason="Conservative English rule classification. Closed issue does not prove resolution. "
                    "Author replies require a full-name GitHub profile link in README matching paper author metadata. "
                    "Other maintainer replies are retained without assuming paper authorship."
                    + (
                        " Thread contains both success and failure reports."
                        if {"failure_reported", "success_reported"} <= set(signals)
                        else ""
                    ),
                )
            )

    def finish(self, applicability: str, status: str, availability: str, signal: str) -> CodeCheckResult:
        evidence_ids = [e.evidence_id for e in self.evidence]
        limitations = list(dict.fromkeys(self.limitations))
        findings = []
        if status != "not_run":
            findings.append(
                CredibilityFinding(
                    finding_id="code_finding_summary",
                    dimension_id="code_reproduction",
                    item_ids=[r.repository_id for r in self.repositories],
                    kind="context" if availability in {"available", "not_applicable"} else "verification_gap",
                    severity="info",
                    certainty="unresolved",
                    title="Code and public reproduction evidence",
                    detail=f"Code: {availability}; feedback: {signal}.",
                    evidence_ids=evidence_ids,
                    source_ids=[s.source_id for s in self.sources],
                    limitations=limitations,
                )
            )
        return CodeCheckResult(
            model_calls=self.model_calls,
            external_request_count=self.requests,
            followup_state=self.followup_state,
            dimension=CredibilityDimension(
                dimension_id="code_reproduction",
                applicability=applicability,
                applicability_reason="Explicit purely theoretical statement located in paper."
                if applicability == "not_applicable"
                else "Based on inspected parsed text and confirmed implementation evidence.",
                check_status=status,
                score_status="not_applicable" if applicability == "not_applicable" else "unable_to_assess",
                score=None,
                weight=1,
                score_rule_ids=[],
                summary=f"Code: {availability}; public feedback: {signal}.",
                unassessed_reason=None
                if applicability == "not_applicable"
                else "Rubric mapping is pending; no code was executed and search coverage is limited.",
                evidence_ids=evidence_ids,
                finding_ids=[f.finding_id for f in findings],
            ),
            checks=CredibilityCodeChecks(
                scope_complete=False,
                scope_limitations=limitations,
                searches=list(self.searches.values()),
                code_availability=availability,
                reproduction_signal=signal,
                repositories=self.repositories,
                discussions=self.discussions,
                base_score_rule_id=None,
                score_rule_id=None,
            ),
            evidence=self.evidence,
            sources=self.sources,
            findings=findings,
        )


def check_code(ctx: CheckContext) -> CodeCheckResult:
    return _Check(ctx).run()


def _merge_collection(output: CodeCheckResult, collected: CodeCheckResult) -> CodeCheckResult:
    """Replace collection facts, preserving review/discovery and future state."""
    present = {row.finding_id for row in collected.findings}
    collected.findings.extend(row for row in output.findings if row.finding_id not in present)
    collected.dimension.finding_ids = [row.finding_id for row in collected.findings]
    return output.model_copy(
        update={
            field: getattr(collected, field)
            for field in (
                "dimension",
                "checks",
                "evidence",
                "sources",
                "findings",
                "external_request_count",
                "followup_state",
            )
        }
    )


def _intake_scope(url: str) -> tuple[str, dict] | None:
    """Accept implementation entry URLs, never issue/search or arbitrary paths."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(char) < 32 for char in url):
        return None
    repo = _repo(url)
    if not repo:
        return None
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment:
        return None
    parts = parsed.path.strip("/").split("/")
    if any(not part for part in parts):
        return None
    if len(parts) > 2 and not (parts[2] in {"tree", "blob", "commit"} or parts[2:4] == ["releases", "tag"]):
        return None
    if parts[2:3] == ["commit"] and len(parts) != 4:
        return None
    scope = repository_scope(url)
    return None if scope.get("scope_invalid") else (repo, scope)


def _scope_key(scope: dict) -> tuple:
    # The original tokens survive longest-prefix branch/ref resolution. Do not
    # merge a default branch, a pinned commit or a sibling subtree by repo alone.
    tokens = list(scope.get("requested_ref_path") or [])
    if tokens and re.fullmatch(r"[a-f0-9]{7,40}", tokens[0], re.I):
        tokens[0] = tokens[0].casefold()
    return tuple(tokens), scope.get("target_file")


def intake_discovered_repositories(
    ctx: CheckContext, result: CodeCheckResult, candidates, *, max_requests: int = 40
) -> CodeCheckResult:
    """Register source-bound discovery candidates without granting ownership.

    Candidates are dicts with candidate_id/url/source_ids/evidence_ids, or those
    four values as a tuple. Discovery sources must already be archived in the
    result. Their quotes are leads, never automatic association evidence.
    """
    output = result.model_copy(deep=True)
    if ctx.doc is None or not ctx.doc.blocks:
        return output
    checker = _Check(ctx)
    checker.max_requests = min(40, max(0, max_requests))
    checker.requests = output.external_request_count
    checker.evidence, checker.sources = output.evidence, output.sources
    checker.repositories, checker.discussions = output.checks.repositories, output.checks.discussions
    checker.followup_state = output.followup_state
    checker.limitations = list(output.checks.scope_limitations)
    checker.searches.update({row.scope_type: row for row in output.checks.searches})
    checker.plan_failed = output.dimension.check_status in {"partial", "failed"} or any(
        source.fetch_status != "ok" for source in output.sources
    )
    source_map = {row.source_id: row for row in output.sources}
    evidence_map = {row.evidence_id: row for row in output.evidence}
    attempted = False
    for candidate in candidates:
        attempted = True
        if isinstance(candidate, (list, tuple)) and len(candidate) == 4:
            candidate = dict(zip(("candidate_id", "url", "source_ids", "evidence_ids"), candidate, strict=True))
        if not isinstance(candidate, dict):
            checker.plan_failed = True
            checker.limitations.append("A discovered repository candidate was malformed and was not inspected.")
            continue
        cid, url = candidate.get("candidate_id"), candidate.get("url")
        source_ids, evidence_ids = candidate.get("source_ids"), candidate.get("evidence_ids")
        valid_binding = (
            isinstance(cid, str)
            and 0 < len(cid) <= 200
            and isinstance(source_ids, list)
            and bool(source_ids)
            and all(
                isinstance(sid, str)
                and sid in source_map
                and source_map[sid].fetch_status == "ok"
                and source_map[sid].snapshot_ref
                for sid in source_ids
            )
            and isinstance(evidence_ids, list)
            and all(
                isinstance(eid, str) and eid in evidence_map and evidence_map[eid].source_id in source_ids
                for eid in evidence_ids
            )
        )
        scoped = _intake_scope(url) if isinstance(url, str) else None
        if not valid_binding or scoped is None:
            checker.plan_failed = True
            checker.limitations.append(
                "A discovered repository lacked valid source bindings or URL scope; not inspected."
            )
            continue
        # Verify actual archives before accepting a model-supplied reference.
        try:
            for sid in source_ids:
                read_snapshot(source_map[sid].snapshot_ref)
        except (SnapshotError, OSError):
            checker.plan_failed = True
            checker.limitations.append("A discovered repository source archive was invalid; candidate not inspected.")
            continue
        repo, scope = scoped
        existing = None
        for row in checker.repositories:
            state = checker.followup_state.get(row.repository_id, {})
            source = next((item for item in checker.sources if item.source_id == row.source_id), None)
            identity = state.get("repository")
            if not identity and source:
                identity = _repo(source.url.replace("api.github.com/repos/", "github.com/"))
            aliases = {identity.casefold()} if isinstance(identity, str) else set()
            if source and source.snapshot_ref:
                saved = read_snapshot(source.snapshot_ref).normalized_json
                full_name = saved.get("full_name") if isinstance(saved, dict) else None
                if isinstance(full_name, str):
                    aliases.add(full_name.casefold())
            known_scope = state or repository_scope(f"https://github.com/{identity}")
            if repo.casefold() in aliases and _scope_key(known_scope) == _scope_key(scope):
                existing = row
                break
        if existing is None:
            if len(checker.repositories) >= 4:
                checker.limitations.append(
                    "Repository candidate limit reached; additional discoveries were not inspected."
                )
                continue
            checker.repository(repo, [], original_url=url, allow_readme_confirmation=False)
            existing = checker.repositories[-1]
            checker.followup_state[existing.repository_id]["discovery_intake_only"] = True
        state = checker.followup_state.setdefault(existing.repository_id, {})
        leads = state.setdefault("discovery_candidates", [])
        if not any(lead.get("candidate_id") == cid for lead in leads):
            leads.append({"candidate_id": cid, "url": url, "source_ids": source_ids, "evidence_ids": evidence_ids})
    if not attempted:
        return output
    available = any(
        repo.association == "confirmed" and repo.availability == "accessible" and repo.implementation_evidence_ids
        for repo in checker.repositories
    )
    availability = "available" if available else "unable_to_verify"
    collected = checker.finish(
        output.dimension.applicability,
        "partial" if checker.plan_failed else "completed",
        availability,
        output.checks.reproduction_signal,
    )
    return _merge_collection(output, collected)


def complete_confirmed_repositories(
    ctx: CheckContext, result: CodeCheckResult, *, max_requests: int = 40
) -> CodeCheckResult:
    """Complete newly confirmed repository evidence, once, within the same budget.

    Existing observations and IDs survive. Metadata/README already archived by
    the initial check are read from their snapshots, never fetched for replay.
    This follows a semantic association judgment; it neither calls a model nor
    runs downloaded code and never changes scoring rules.
    """
    output = result.model_copy(deep=True)
    if ctx.doc is None or not ctx.doc.blocks:
        # A no-target semantic pass is not evidence that a missing-input
        # collection plan ran. Keep the producer's not_run/failed state.
        return output
    checker = _Check(ctx)
    checker.max_requests = min(40, max(0, max_requests))
    checker.requests = output.external_request_count
    checker.evidence, checker.sources = output.evidence, output.sources
    checker.repositories, checker.discussions = output.checks.repositories, output.checks.discussions
    checker.followup_state = output.followup_state
    checker.limitations = list(output.checks.scope_limitations)
    checker.searches.update({row.scope_type: row for row in output.checks.searches})
    prior_collection_gap = any(
        state.get("inspection_status") == "partial" or state.get("readme_readable") is False
        for state in output.followup_state.values()
    )
    checker.plan_failed = any(source.fetch_status != "ok" for source in output.sources) or prior_collection_gap
    if prior_collection_gap:
        checker.limitations.append("Previously observed repository payload/inspection gaps remain unresolved.")
    sources = {source.source_id: source for source in output.sources}
    for repository in checker.repositories:
        if repository.association != "confirmed" or repository.availability != "accessible":
            continue
        state = checker.followup_state.setdefault(repository.repository_id, {})
        if state.get("implementation_checked") and state.get("discussions_checked"):
            continue
        source = sources.get(repository.source_id)
        repo = state.get("repository") or (
            _repo(source.url.replace("api.github.com/repos/", "github.com/")) if source else None
        )
        if not repo:
            checker.plan_failed = True
            checker.limitations.append("A newly confirmed repository lacks a source-bound public repository identity.")
            continue
        metadata = {}
        if source and source.snapshot_ref:
            saved = read_snapshot(source.snapshot_ref)
            metadata = saved.normalized_json if isinstance(saved.normalized_json, dict) else {}
        if not state:
            state.update(repository_scope(f"https://github.com/{repo}"))
        state["repository"] = repo
        metadata.setdefault("default_branch", state.get("default_branch"))
        if not repository.inspected_revision or not state.get("ref_scope_resolved", True):
            repository.inspected_revision = checker._revision(repo, state, metadata)
        if not repository.inspected_revision:
            checker.plan_failed = True
            continue
        readme, read_sid = "", state.get("readme_source_id")
        read_source = sources.get(read_sid)
        if read_source and read_source.snapshot_ref:
            saved = read_snapshot(read_source.snapshot_ref)
            data = saved.normalized_json
            if isinstance(data, dict) and data.get("encoding") == "base64":
                try:
                    readme = base64.b64decode(re.sub(r"[ \t\r\n]", "", data.get("content", "")), validate=True).decode(
                        "utf-8", errors="replace"
                    )
                    state["readme_readable"] = True
                except (ValueError, TypeError, binascii.Error):
                    checker.plan_failed = True
        else:
            readme, read_sid = checker._readme(
                f"https://api.github.com/repos/{repo}", repository.inspected_revision, state
            )
            state["readme_source_id"] = read_sid
        checker._inspect_implementation(repo, repository, state, readme, read_sid)
    available = any(
        repo.association == "confirmed" and repo.availability == "accessible" and repo.implementation_evidence_ids
        for repo in checker.repositories
    )
    availability = "available" if available else output.checks.code_availability
    if not available and availability == "available":
        availability = "unable_to_verify"
    signals = {discussion.signal for discussion in checker.discussions if discussion.relevance == "relevant"}
    signal = next(
        (value for value in ("failure_reported", "success_reported", "discussion_only") if value in signals),
        output.checks.reproduction_signal,
    )
    if not signals and checker.discussion_ran and checker.discussion_complete and not checker.discussions:
        signal = "no_feedback_found"
    completed = checker.finish(
        output.dimension.applicability, "partial" if checker.plan_failed else "completed", availability, signal
    )
    return _merge_collection(output, completed)
