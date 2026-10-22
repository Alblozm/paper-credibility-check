#!/usr/bin/env python3
"""Read-only audit of fresh v0.5 code-discovery acceptance folders.

No model or source lookup is performed. Passing this audit establishes artifact,
quote, linkage, scoring and execution-provenance consistency; it does not certify
semantic accuracy, actual reproduction, or completion of an incomplete run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

sys.dont_write_bytecode = True

DEFAULT_REPO = Path(__file__).resolve().parents[1]
REF = re.compile(r"snapshot:v1:[a-f0-9]{64}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refs(value):
    if isinstance(value, str) and REF.fullmatch(value):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def same(actual, expected, label):
    require(actual == expected, f"Mismatch: {label}")


def implementation_helpers(root):
    loaded = {}
    for name in ("code_repository_scope", "external_tools", "code_model", "code_discovery",
                 "item_scoring", "locate", "execution"):
        path = root / "app/credibility" / (name + ".py")
        module_name = "_code_artifact_audit_" + name + "_" + sha(path)[:16]
        if module_name not in sys.modules:
            specification = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(specification)
            sys.modules[module_name] = module
            specification.loader.exec_module(module)
        loaded[name] = sys.modules[module_name]
    return loaded


def quote_documents(source, snapshot, helpers):
    ProjectHTML = helpers["code_repository_scope"].ProjectHTML
    _html_text = helpers["external_tools"]._html_text
    _text_from_data = helpers["external_tools"]._text_from_data

    data = snapshot.normalized_json
    if isinstance(data, list):
        parsed = urlsplit(source.url)
        comments = re.fullmatch(r"/repos/([^/]+)/([^/]+)/issues/(\d+)/comments", parsed.path)
        if source.kind == "comment" and parsed.hostname == "api.github.com" and comments:
            # Native tools may archive one server-owned comments page. Every
            # row must still belong to this exact repository and issue; this
            # endpoint is not permission to cite another thread's envelope.
            owner, repository, number = comments.groups()
            prefix = f"https://github.com/{owner}/{repository}/issues/{number}#issuecomment-".casefold()
            require(all(
                isinstance(row, dict) and isinstance(row.get("html_url"), str)
                and row["html_url"].casefold().startswith(prefix)
                and row["html_url"][len(prefix):].isdigit()
                for row in data
            ), "Comments page contains an unknown or cross-thread target")
            return [_text_from_data(data, url=source.url)]
        matching = [row for row in data if isinstance(row, dict) and row.get("html_url") == source.url]
        # An issue/comment source derived from a listing must stay bound to the
        # named row; another thread in the raw envelope cannot support its quote.
        if source.kind in {"issue", "comment"}:
            require(len(matching) == 1, "Issue/comment archive lacks a unique exact target row")
        if matching:
            return [_text_from_data(matching[0], url=source.url)]
    output = [snapshot.normalized_text, _text_from_data(data, url=source.url)]
    if snapshot.raw is not None:
        raw = snapshot.raw.decode("utf-8", errors="replace")
        output.extend([raw, _html_text(raw)])
        if "<" in raw and ">" in raw:
            output.append(ProjectHTML(raw).text)
    if data is not None:
        output.extend([json.dumps(data, ensure_ascii=False), json.dumps(data, ensure_ascii=False, sort_keys=True)])
    return [text for text in output if isinstance(text, str) and text]


def check_quote(source, snapshot, excerpt, label, helpers):
    _original_quote_span = helpers["code_model"]._original_quote_span

    require(source.fetch_status == "ok", f"Quoted failed source: {label}")
    require(any(_original_quote_span(text, excerpt) is not None for text in quote_documents(source, snapshot, helpers)),
            f"Quote absent from its archived source: {label}")


def assert_code_result_links(result):
    """Check the code-only result, including candidate proof, without I/O."""
    pools = {}
    for name, rows, key in (
        ("source", result.sources, "source_id"), ("evidence", result.evidence, "evidence_id"),
        ("finding", result.findings, "finding_id"), ("repository", result.checks.repositories, "repository_id"),
        ("discussion", result.checks.discussions, "discussion_id"),
        ("query", result.discovery.queries, "query_id"), ("candidate", result.discovery.candidates, "candidate_id"),
    ):
        pools[name] = {getattr(row, key): row for row in rows}
        require(len(pools[name]) == len(rows), f"Duplicate {name} IDs")

    def need(ids, name, label):
        require(set(ids) <= set(pools[name]), f"Unclosed {label} {name} IDs")

    need(result.dimension.evidence_ids, "evidence", "dimension")
    need(result.dimension.finding_ids, "finding", "dimension")
    for evidence in result.evidence:
        if evidence.origin == "external":
            need([evidence.source_id], "source", "external evidence")
            require(evidence.paper_locator is None, "External evidence has a paper locator")
        else:
            require(evidence.source_id is None and evidence.paper_locator is not None, "Invalid paper evidence")
    for finding in result.findings:
        need(finding.source_ids, "source", "finding")
        need(finding.evidence_ids, "evidence", "finding")
        items = set(pools["repository"]) | set(pools["discussion"]) | set(pools["candidate"]) | set(pools["query"])
        require(set(finding.item_ids) <= items, "Unclosed finding item IDs")
    for search in result.checks.searches:
        need(search.source_ids, "source", "search")
        need(search.evidence_ids, "evidence", "search")
    for repository in result.checks.repositories:
        need([repository.source_id], "source", "repository")
        need(repository.association_evidence_ids + repository.implementation_evidence_ids, "evidence", "repository")
    for discussion in result.checks.discussions:
        need([discussion.repository_id], "repository", "discussion")
        need(discussion.source_ids + discussion.author_response_source_ids, "source", "discussion")
        need(discussion.evidence_ids, "evidence", "discussion")
        require(discussion.scoring_eligible is False, "Unreviewed discussion became scoring eligible")
    for query in result.discovery.queries:
        need(query.source_ids, "source", "discovery query")
        require(all(pools["source"][sid].kind == "metadata" for sid in query.source_ids),
                "Search response must remain metadata")
    for candidate in result.discovery.candidates:
        need(candidate.query_ids, "query", "discovery candidate")
        need(candidate.source_ids, "source", "discovery candidate")
        need(candidate.evidence_ids, "evidence", "discovery candidate")
        if candidate.repository_id is not None:
            need([candidate.repository_id], "repository", "discovery candidate")
        for eid in candidate.evidence_ids:
            evidence = pools["evidence"][eid]
            if evidence.origin == "external":
                require(pools["source"][evidence.source_id].kind != "metadata",
                        "Search snippet became implementation/classification proof")
        if candidate.status == "verified":
            repository = pools["repository"].get(candidate.repository_id)
            require(repository is not None and repository.association == "confirmed"
                    and bool(repository.association_evidence_ids)
                    and set(repository.association_evidence_ids) <= set(candidate.evidence_ids),
                    "Verified discovery candidate lacks actual confirmed repository proof")
            require(bool(repository.inspected_revision and SHA.fullmatch(repository.inspected_revision)),
                    "Verified discovery candidate lacks an immutable repository revision")


def discovery_page_text(snapshot, store, helpers):
    """Verify a readable page derives only from its frozen normalization rule."""
    data = snapshot.normalized_json
    require(isinstance(data, dict) and data.get("operation") == "fetch", "Missing Fetch page observation")
    original = data.get("text", "")
    require(isinstance(original, str), "Invalid archived Fetch text")
    version = snapshot.manifest.get("metadata", {}).get("normalization_version")
    if not version:
        require(snapshot.normalized_text in (None, original), "Undeclared change to archived page text")
        return snapshot.normalized_text if snapshot.normalized_text is not None else original, False
    helper = helpers["code_discovery"]
    same(version, getattr(helper, "PAGE_TEXT_VERSION", None), "frozen page normalization version")
    parent_ref = snapshot.manifest.get("parent_ref")
    require(parent_ref is not None, "Normalized page has no immutable provider parent")
    parent = store.read(parent_ref)
    same(snapshot.normalized_json, parent.normalized_json, "original provider page JSON")
    same(parent.normalized_text or "", original, "original provider page text")
    rendered = helper.normalize_discovery_page_text(original)
    retained = snapshot.normalized_text
    require(isinstance(retained, str) and rendered.startswith(retained),
            "Derived page text is not a normalized provider-text prefix")
    return retained, True


def discovery_page_readable(source, snapshot, store, helpers):
    """Keep provider transport success separate from a blocked page's content."""
    same(source.fetch_status, snapshot.manifest.get("fetch_status"), "discovery source fetch state")
    same(source.http_status, snapshot.manifest.get("http_status"), "discovery source HTTP state")
    data = snapshot.normalized_json
    metadata = snapshot.manifest.get("metadata", {})
    classifier = getattr(helpers["code_discovery"], "page_content_problem", None)
    detected = classifier(data.get("title") or "", data.get("text") or "") if classifier else None
    problem = data.get("content_reason_code") or detected
    if metadata.get("content_status") == "blocked" or data.get("content_status") == "blocked" or problem:
        require(bool(problem), "Blocked discovery page has no content reason")
        same(metadata.get("content_status"), "blocked", "blocked page content metadata")
        same(metadata.get("content_reason_code"), problem, "blocked page content reason")
        same(source.fetch_status, "access_denied", "blocked page is not readable")
        parent_ref = snapshot.manifest.get("parent_ref")
        require(parent_ref is not None, "Blocked page has no preserved provider parent")
        parent = store.read(parent_ref)
        same(parent.manifest.get("kind"), "code_discovery_fetch", "blocked page provider parent kind")
        same(parent.normalized_json, data, "blocked page original provider observation")
        same(metadata.get("transport_fetch_status"), parent.manifest.get("fetch_status"),
             "blocked page preserved provider transport status")
        same(metadata.get("provider_http_status"), parent.manifest.get("http_status"),
             "blocked page preserved provider HTTP status")
        same(source.http_status, parent.manifest.get("http_status"), "blocked page actual provider HTTP status")
        if data.get("status") is not None:
            same(data["status"], parent.manifest.get("fetch_status"), "blocked provider result status")
        require(metadata.get("origin_http_status") is None
                and parent.manifest.get("metadata", {}).get("origin_http_status") is None,
                "Blocked content fabricated an origin HTTP status")
        return False, problem
    return source.fetch_status == "ok", None


def discovery_target_bindings(judgments, candidates, sources, context, helpers):
    """Audit URL ownership independently of the saved permission registry."""
    for judgment in judgments.candidates:
        require(judgment.candidate_id in candidates, "Discovery output has an unknown candidate")
        candidate = candidates[judgment.candidate_id]
        for citation in judgment.citations:
            require(citation.source_id in sources, "Discovery output has an unknown source")
            source = sources[citation.source_id]
            require(source.kind == "project_page" and source.fetch_status == "ok",
                    "Discovery output quotes a failed/non-page source")
            if source.url != candidate.url:
                require(helpers["code_discovery"].linked_implementation_quote(
                    context, candidate.url, citation.excerpt or ""),
                    "Parent discovery quote lacks the candidate's local Code link binding")


def resolved_discovery_output(audit, store, page_documents, sources, context, helpers, *, candidates):
    """Reconstruct server-resolved citations from raw output and owned spans.

    The accepted record cannot lend the model a new source, another page's
    span, or text outside its archived input. Legacy quote-only runs keep their
    original output format and remain independently auditable.
    """
    helper = helpers["code_discovery"]
    accepted_snapshot = store.read(audit["output_snapshot_ref"])
    judgments = helper._Judgments.model_validate_json(accepted_snapshot.normalized_text)
    raw_ref = audit.get("raw_output_snapshot_ref")
    if raw_ref is None:
        require(audit.get("model_citation_protocol") is None,
                "Span-only model protocol has no raw model-output provenance")
        require(not any(getattr(citation, "span_id", None) for row in judgments.candidates
                        for citation in row.citations), "Span citation has no raw model-output provenance")
        discovery_target_bindings(judgments, candidates, sources, context, helpers)
        return judgments, 0
    same(accepted_snapshot.manifest.get("kind"), "code_discovery_validated_output", "resolved output kind")
    same(accepted_snapshot.manifest.get("parent_ref"), raw_ref, "resolved output raw parent")
    require(raw_ref in audit.get("output_snapshot_refs", []), "Final raw output is outside recorded responses")
    raw_snapshot = store.read(raw_ref)
    same(raw_snapshot.manifest.get("kind"), "code_discovery_model_output", "raw output kind")
    if audit.get("model_citation_protocol") is not None:
        same(audit["model_citation_protocol"], "registered_spans_only", "model citation protocol")
        helper._ModelJudgments.model_validate_json(raw_snapshot.normalized_text)
    raw = helper._Judgments.model_validate_json(raw_snapshot.normalized_text)
    span_ref = audit.get("citation_spans_snapshot_ref")
    require(span_ref is not None, "Resolved output lacks the registered citation-span archive")
    span_snapshot = store.read(span_ref)
    same(span_snapshot.manifest.get("kind"), "code_discovery_citation_spans", "citation span registry kind")
    spans = span_snapshot.normalized_json.get("spans") if isinstance(span_snapshot.normalized_json, dict) else None
    require(isinstance(spans, dict), "Citation span registry is malformed")
    if spans:
        first = next(iter(spans))
        match = re.fullmatch(r"discovery_span_([a-f0-9]{10})_\d+", first)
        require(match is not None, "Citation span ID is not server generated")
        registry = object.__new__(helper.DiscoverySession)
        registry.ctx, registry.prefix, registry.citation_spans = context, match[1], {}
        for sid, text in page_documents.items():
            source = sources[sid]
            if source.fetch_status == "ok" and text:
                require(sid.startswith("code_discovery_src_" + registry.prefix + "_"),
                        "Citation span source is from another discovery session")
                registry.register_citation_spans(sid, text, source.snapshot_ref)
        same(spans, registry.citation_spans, "source-pinned reconstructed citation span registry")
    resolved_count = 0
    for judgment in raw.candidates:
        allowed = set(audit.get("candidate_origins", {}).get(judgment.candidate_id, []))
        for citation in judgment.citations:
            require(citation.source_id in allowed, "Raw citation is outside its fetched candidate scope")
            require(citation.source_id in page_documents, "Raw citation has no readable page")
            source = sources[citation.source_id]
            require(source.fetch_status == "ok", "Raw citation quotes an unreadable page")
            span_id = getattr(citation, "span_id", None)
            if span_id:
                span = spans.get(span_id)
                require(isinstance(span, dict) and span.get("source_id") == citation.source_id,
                        "Raw citation uses an unknown or cross-source span")
                exact = page_documents[citation.source_id][span["start"]:span["end"]]
                require(bool(exact) and (citation.excerpt is None or citation.excerpt == exact),
                        "Raw span quotation differs from the registered source interval")
                citation.excerpt = exact
                resolved_count += 1
            original = helpers["code_model"]._original_quote_span(
                page_documents[citation.source_id], citation.excerpt,
            )
            require(original is not None, "Raw citation is outside the retained own-page text")
            citation.excerpt = original
        if judgment.resource_kind == "implementation_candidate" and helper.explicit_third_party([
            page_documents[sid] for sid in allowed if sid in page_documents
        ]):
            judgment.resource_kind = "third_party_reproduction"
    same(raw.model_dump(mode="json"), judgments.model_dump(mode="json"),
         "raw model output with only server-owned citation resolution")
    discovery_target_bindings(judgments, candidates, sources, context, helpers)
    return judgments, resolved_count


def audit_discovery(result, summary, context, store, sources, helpers, http_audit):
    discovery = result.discovery
    same(summary["code_discovery"], discovery.model_dump(mode="json"), "typed discovery summary")
    same(summary["discovery_status"], discovery.status, "discovery status")
    same(summary["discovery_budget"], discovery.budget.model_dump(mode="json"), "discovery budget")
    same(summary["discovery_candidates"], [row.model_dump(mode="json") for row in discovery.candidates],
         "discovery candidate summary")
    same(summary["discovery_audit_snapshot_ref"], discovery.audit_ref, "discovery audit identity")
    require(summary["source_links_verified"] is True, "Producer did not verify source links")
    budget = discovery.budget
    for used, maximum in (("queries_used", "max_queries"), ("candidates_inspected", "max_candidates"),
                          ("source_reads_used", "max_source_reads"), ("model_rounds_used", "max_model_rounds"),
                          ("tool_calls_used", "max_tool_calls")):
        require(getattr(budget, used) <= getattr(budget, maximum), "Discovery stage budget exceeded")
    require(budget.max_source_reads <= 40 and budget.max_model_rounds <= 4 and budget.max_tool_calls <= 8,
            "Discovery hard budget was expanded")
    active = discovery.status in {"completed", "failed"} or any((budget.source_reads_used,
                                                                budget.model_rounds_used, budget.tool_calls_used))
    if not discovery.audit_ref:
        require(not active and summary["discovery_audit_snapshot_verified"] is False,
                "Executed discovery lacks an independent audit")
        return {"discovery_status": discovery.status, "discovery_attempts_verified": 0,
                "discovery_candidates_verified": len(discovery.candidates)}
    require(summary["discovery_audit_snapshot_verified"] is True, "Discovery audit was not verified")
    saved = store.read(discovery.audit_ref)
    same(saved.manifest["kind"], "code_discovery_audit", "discovery audit kind")
    audit = saved.normalized_json
    same(audit["status"], discovery.status, "discovery audit status")
    same(audit["budget"], budget.model_dump(mode="json"), "discovery audit budget")
    same(audit["attempt_count"], len(audit["attempts"]), "discovery attempt count")
    same(len(audit["attempts"]), budget.model_rounds_used, "discovery model usage")
    same(audit["tool_call_count"], len(audit["tool_calls"]), "discovery tool count")
    same(len(audit["tool_calls"]), budget.tool_calls_used, "discovery tool usage")
    same(audit["external_request_count"], budget.source_reads_used, "discovery provider operations")
    wire = [row for row in http_audit["http_calls"] if row["provider"] in {"deepseek_search", "deepseek_fetch"}]
    same(len(wire), budget.source_reads_used, "actual Search/Fetch POST operations")
    for row in wire:
        expected = "https://api.deepseek.com/v1/tools/" + row["provider"].removeprefix("deepseek_")
        require(row["method"] == "POST" and row["url"] == expected, "Unexpected Search/Fetch endpoint")
    safe_url = helpers["code_discovery"].safe_candidate_url
    page_documents, normalized_pages, blocked_pages = {}, 0, {}
    for sid, source in sources.items():
        snapshot = store.read(source.snapshot_ref)
        data = snapshot.normalized_json
        if source.kind == "project_page" and isinstance(data, dict) and data.get("operation") == "fetch":
            text, normalized = discovery_page_text(snapshot, store, helpers)
            readable, problem = discovery_page_readable(source, snapshot, store, helpers)
            if readable and text:
                page_documents[sid] = text
            if problem:
                blocked_pages[sid] = problem
            normalized_pages += normalized
    if blocked_pages:
        require(discovery.status in {"partial", "failed"}, "Blocked page was reported as completed discovery")
        allowed_sources = {sid for rows in audit.get("candidate_origins", {}).values() for sid in rows}
        require(not set(blocked_pages) & allowed_sources, "Blocked page received readable citation permissions")
        for sid, problem in blocked_pages.items():
            require(any(problem in limitation for limitation in discovery.limitations),
                    "Blocked page has no retained discovery limitation")
            calls = [row for row in audit["tool_calls"] if row.get("source_id") == sid]
            require(bool(calls) and all(row.get("status") == "access_denied" and row.get("reason") == problem
                                        for row in calls), "Blocked page tool state differs from its content state")
    query_ids = {row.query_id for row in discovery.queries}
    candidates = {row.candidate_id: row for row in discovery.candidates}
    same(budget.queries_used, sum(row.status != "not_run" for row in discovery.queries),
         "actual executed discovery queries")
    inspected = {row["source_id"] for row in audit["tool_calls"]
                 if row.get("name") == "fetch_discovery_page" and row.get("source_id")}
    same(budget.candidates_inspected, len(inspected), "actual unique candidate page reads")
    for query in discovery.queries:
        for sid in query.source_ids:
            data = store.read(sources[sid].snapshot_ref).normalized_json
            require(isinstance(data, dict) and data.get("operation") == "search", "Query lacks Search observation")
            same(data["query"], query.query, "archived actual query text")
    arxiv = (context.doc.paper.arxiv_id if context.doc else None) or context.source.get("arxiv_id")
    seed = "https://arxiv.org/abs/" + arxiv if arxiv else None
    for candidate in discovery.candidates:
        require(safe_url(candidate.url) == candidate.url, "Unsafe or noncanonical candidate URL")
        observed = candidate.url == seed
        for sid in candidate.source_ids:
            source = sources[sid]
            snapshot = store.read(source.snapshot_ref)
            data = snapshot.normalized_json
            if not isinstance(data, dict):
                continue
            if data.get("operation") == "search":
                observed |= any(safe_url(row.get("url", "")) == candidate.url for row in data.get("hits", []))
            elif data.get("operation") == "fetch" and source.url != candidate.url:
                text = page_documents.get(sid, data.get("text", ""))
                links = re.findall(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", text)
                links += re.findall(r'https://[^\s<>\[\]"\')]+', text)
                observed |= any(safe_url(link.rstrip(".,;"), source.url) == candidate.url for link in links)
        require(observed, "Candidate URL was not discovered in archived search/page content or the paper seed")
        require(set(candidate.query_ids) <= query_ids, "Candidate has an unknown query")
    output_ref = audit.get("output_snapshot_ref")
    accepted_quotes, resolved_spans = 0, 0
    if output_ref and not audit.get("error_type"):
        judgments, resolved_spans = resolved_discovery_output(
            audit, store, page_documents, sources, context, helpers, candidates=candidates,
        )
        for judgment in judgments.candidates:
            require(judgment.candidate_id in candidates, "Discovery output has an unknown candidate")
            allowed = set(audit.get("candidate_origins", {}).get(judgment.candidate_id, []))
            for citation in judgment.citations:
                require(citation.source_id in allowed and citation.source_id in sources,
                        "Discovery quote is outside its fetched candidate scope")
                source = sources[citation.source_id]
                require(source.kind == "project_page", "Discovery cited a Search snippet instead of a fetched page")
                require(helpers["code_model"]._original_quote_span(
                    page_documents.get(citation.source_id, ""), citation.excerpt) is not None,
                    "Discovery quote is outside the retained rendered page")
                check_quote(source, store.read(source.snapshot_ref), citation.excerpt, judgment.candidate_id, helpers)
                accepted_quotes += 1
            if judgment.resource_kind != "undetermined":
                require(bool(judgment.citations), "Classified candidate has no fetched quotations")
            if judgment.resource_kind in {"official_implementation", "implementation_candidate",
                                          "third_party_reproduction"}:
                require(helpers["code_discovery"].implementation_relation(
                    context, [row.excerpt for row in judgment.citations]), "Implementation identity is unbound")
    same(audit["model_call_count"], sum(row["status"] == "returned" for row in audit["attempts"]),
         "discovery returned model calls")
    return {"discovery_status": discovery.status, "discovery_attempts_verified": len(audit["attempts"]),
            "server_resolved_discovery_spans_verified": resolved_spans,
            "discovery_candidates_verified": len(candidates), "discovery_quotes_verified": accepted_quotes,
            "discovery_source_read_operations": budget.source_reads_used,
            "normalized_page_parent_chains_verified": normalized_pages,
            "provider_fetch_origin_redirect_chain_certified": False}


def audit_run(run_dir, selection, repo, implementation_root):
    from app.credibility.models import CodeCheckResult
    from app.credibility.rubric import load_credibility_rubric
    from app.credibility.runtime import decode_context
    from app.credibility.snapshots import SnapshotStore, verify_report_snapshots

    summary = json.loads((run_dir / "summary.json").read_text())
    payload = json.loads((run_dir / "code-result.json").read_text())
    encoded = json.loads((run_dir / "input-context.json").read_text())
    http_audit = json.loads((run_dir / "http-audit.json").read_text())
    result, context = CodeCheckResult.model_validate(payload), decode_context(encoded)
    assert_code_result_links(result)
    rows = [row for row in selection["samples"] if row["sample_id"] == summary["sample_id"]]
    require(len(rows) == 1, "Sample is absent or duplicated in the frozen selection")
    sample = rows[0]
    same(summary["selection_sha256"], selection["selection_sha256"], "frozen selection")
    require(summary["schema_version"] == "credibility-0.5.0-draft", "Unexpected code acceptance contract")
    for flag in ("previous_code_results_read", "previous_model_outputs_read", "claim_checks_run",
                 "reference_checks_run", "repository_code_executed"):
        require(summary.get(flag) is False, f"Fresh code-only execution flag missing/true: {flag}")
    require(context.doc is not None, "The fresh input has no parsed document")
    require(summary["parsing"]["mode"] == "fresh_pdf_parse", "Input was not freshly parsed")
    require(summary["parsing"]["reused_parsed_blocks"] is False, "Saved parsed blocks were reused")
    same(sha(sample["pdf_path"]), sample["pdf_sha256"], "frozen PDF bytes")
    same(summary["parsing"]["pdf_sha256"], sample["pdf_sha256"], "PDF parse provenance")
    same(context.doc.source_sha256, sample["pdf_sha256"], "fresh parsed PDF SHA")
    require(context.model_id == "model_standard_v1", "Reference/stub model was selected")
    for relative, digest in summary["implementation_sha256"].items():
        path = (implementation_root / relative).resolve()
        require(path.is_relative_to(implementation_root) and path.is_file(), "Invalid frozen implementation path")
        same(sha(path), digest, f"frozen implementation {relative}")
    manifest_path = implementation_root / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        same(manifest, summary["implementation_sha256"], "frozen source manifest")
    helpers = implementation_helpers(implementation_root)
    score_code_result = helpers["item_scoring"].score_code_result
    _result_state = helpers["execution"]._result_state
    PaperSpanCandidate = helpers["locate"].PaperSpanCandidate
    locate_paper_span = helpers["locate"].locate_paper_span
    verifier = implementation_root / "scripts/verify_code_usability.py"
    if not verifier.is_file():
        verifier = repo / "scripts/verify_code_usability.py"
    same(sha(verifier), summary["script_sha256"], "live verifier source")
    rubric = load_credibility_rubric(summary["rubric_version"])
    same(rubric.content_sha256, selection["rubric_sha256"], "selection rubric bytes")
    same(rubric.content_sha256, summary["rubric_sha256"], "run rubric bytes")
    scored = score_code_result(result, rubric=rubric)
    for field in ("score", "score_status", "score_rule_ids"):
        same(getattr(scored.dimension, field), getattr(result.dimension, field), f"deterministic score {field}")
    for field in ("base_score_rule_id", "score_rule_id"):
        same(getattr(scored.checks, field), getattr(result.checks, field), f"code scoring rule {field}")
    store = SnapshotStore(run_dir / "artifacts")
    same(store.read(summary["input_snapshot_ref"]).normalized_json, {"context": encoded}, "input snapshot content")
    same(store.read(summary["result_snapshot_ref"]).normalized_json, payload, "result snapshot content")
    same(store.read(summary["audit_snapshot_ref"]).normalized_json, http_audit, "HTTP audit snapshot content")
    sources = {item.source_id: item for item in result.sources}
    evidence = {item.evidence_id: item for item in result.evidence}
    findings = {item.finding_id: item for item in result.findings}
    repositories = {item.repository_id: item for item in result.checks.repositories}
    discussions = {item.discussion_id: item for item in result.checks.discussions}
    for name, mapping, values in (("source", sources, result.sources), ("evidence", evidence, result.evidence),
                                  ("finding", findings, result.findings),
                                  ("repository", repositories, result.checks.repositories),
                                  ("discussion", discussions, result.checks.discussions)):
        require(len(mapping) == len(values), f"Duplicate {name} IDs")

    def links(ids, pool, label):
        require(set(ids) <= set(pool), f"Unclosed IDs: {label}")

    links(result.dimension.evidence_ids, evidence, "dimension evidence")
    links(result.dimension.finding_ids, findings, "dimension findings")
    for finding in result.findings:
        links(finding.evidence_ids, evidence, f"finding {finding.finding_id} evidence")
        links(finding.source_ids, sources, f"finding {finding.finding_id} source")
        links(finding.item_ids, set(repositories) | set(discussions), f"finding {finding.finding_id} items")
    for search in result.checks.searches:
        links(search.evidence_ids, evidence, f"search {search.scope_id} evidence")
        links(search.source_ids, sources, f"search {search.scope_id} source")
    for repository in result.checks.repositories:
        links([repository.source_id], sources, f"repository {repository.repository_id} source")
        links(repository.association_evidence_ids + repository.implementation_evidence_ids, evidence,
              f"repository {repository.repository_id} evidence")
        require(repository.inspected_revision is None or SHA.fullmatch(repository.inspected_revision),
                f"Nonimmutable inspected revision: {repository.repository_id}")
    for discussion in result.checks.discussions:
        links([discussion.repository_id], repositories, f"discussion {discussion.discussion_id} repository")
        links(discussion.source_ids + discussion.author_response_source_ids, sources,
              f"discussion {discussion.discussion_id} source")
        links(discussion.evidence_ids, evidence, f"discussion {discussion.discussion_id} evidence")
        require(discussion.scoring_eligible is False, "Unreviewed public discussion became scoring eligible")
    quoted_external, paper_anchors = 0, 0
    for item in result.evidence:
        if item.origin == "external":
            links([item.source_id], sources, f"external evidence {item.evidence_id}")
            require(item.paper_locator is None, "External evidence has a paper locator")
            if item.mode == "quote":
                source = sources[item.source_id]
                check_quote(source, store.read(source.snapshot_ref), item.excerpt, item.evidence_id, helpers)
                quoted_external += 1
        else:
            require(item.source_id is None and item.paper_locator is not None, "Invalid paper evidence origin")
            locator = item.paper_locator
            located, problems = locate_paper_span(context, PaperSpanCandidate(
                item.evidence_id, item.mode, item.excerpt, chunk_id=locator.chunk_id,
            ))
            require(located is not None and not problems, f"Unlocated paper evidence: {item.evidence_id}")
            same(located.evidence.paper_locator.model_dump(), locator.model_dump(), f"fresh locator {item.evidence_id}")
            paper_anchors += 1
    verify_report_snapshots({"sources": payload["sources"], "code_discovery": payload["discovery"]})
    discovery_counts = audit_discovery(result, summary, context, store, sources, helpers, http_audit)
    pending = list(refs({key: summary[key] for key in (
        "input_snapshot_ref", "result_snapshot_ref", "audit_snapshot_ref",
    )}))
    if result.external_review and result.external_review.get("audit_ref"):
        pending.append(result.external_review["audit_ref"])
    visited, snapshots = set(), {}
    while pending:
        reference = pending.pop()
        if reference in visited:
            continue
        saved = store.read(reference)
        visited.add(reference)
        snapshots[reference] = saved
        pending.extend(refs(saved.manifest))
        pending.extend(refs(saved.normalized_json))
    implementation_facts = 0
    for saved in snapshots.values():
        facts = saved.normalized_json
        if saved.manifest.get("kind") != "code_implementation_inspection":
            continue
        implementation_facts += 1
        rid = saved.manifest["metadata"]["repository_id"]
        require(rid in repositories, "Implementation proof refers to an unknown repository")
        repository, state = repositories[rid], result.followup_state.get(rid, {})
        revision = facts["inspected_revision"]
        require(SHA.fullmatch(revision), "Implementation facts have a nonimmutable revision")
        same(revision, repository.inspected_revision, "implementation/repository revision")
        same(revision, saved.manifest["revision"], "implementation manifest revision")
        same(facts.get("requested_revision"), state.get("requested_revision"), "cited requested revision")
        same(facts.get("inspected_subdir"), state.get("inspected_subdir"), "cited subdirectory")
        if re.fullmatch(r"[a-fA-F0-9]{7,40}", str(state.get("requested_revision") or "")):
            require(revision.startswith(state["requested_revision"].lower()), "Explicit cited commit was substituted")
        require(facts.get("source_code_executed") is False and facts.get("runnable_status") == "not_executed",
                "Implementation observations claim executed reproduction")
        links([sid for sid in facts["source_ids"] if sid], sources, "implementation fact source")
        subdir = facts.get("inspected_subdir") or ""
        for field in ("implementation_paths", "entrypoint_paths", "dependency_manifest_paths"):
            for path in facts.get(field, []):
                require(not subdir or path.startswith(subdir + "/"), "Implementation path escaped cited subdirectory")
        for file in facts.get("inspected_files", []):
            source = sources[file["source_id"]]
            url = urlsplit(source.url)
            same(parse_qs(url.query).get("ref"), [revision], "file source immutable ref")
            require(unquote(url.path).endswith("/contents/" + file["path"]), "File source path differs from fact")
            if file.get("content_read"):
                require(source.fetch_status == "ok", "Failed file source marked read")
    semantic = store.read(result.external_review["audit_ref"]).normalized_json if result.external_review else {}
    passes = semantic.get("passes", [semantic] if semantic else [])
    attempts = semantic.get("attempts", [row for review in passes for row in review.get("attempts", [])])
    attempt_ids = [row["attempt_snapshot_ref"] for row in attempts]
    require(len(attempt_ids) == len(set(attempt_ids)), "A model attempt was counted more than once")
    review_attempts = sum(len(review.get("attempts", [])) for review in passes)
    review_tools = sum(review.get("tool_call_count", 0) for review in passes)
    require(review_attempts <= summary["versions"]["tool_rounds"] <= 3, "Review model budget was expanded")
    require(review_tools <= summary["versions"]["tool_calls"] <= 6, "Review tool budget was expanded")
    require(len(attempts) <= 7 and semantic.get("tool_call_count", 0) <= 14,
            "Combined discovery/review phase budgets were expanded")
    returned = [row for row in attempts if row["status"] == "returned"]
    for attempt in attempts:
        started = store.read(attempt["attempt_snapshot_ref"])
        terminal = store.read(attempt["result_snapshot_ref"])
        require(started.manifest["kind"] in {"external_model_attempt", "code_discovery_model_attempt"},
                "Unexpected model attempt snapshot kind")
        same(terminal.manifest["parent_ref"], attempt["attempt_snapshot_ref"], "model terminal parent")
        same(terminal.normalized_json["status"], attempt["status"], "model attempt terminal status")
        same(attempt.get("assessment_id", started.manifest.get("metadata", {}).get("assessment_id")),
             summary["run_id"], "model attempt run identity")
        store.read(attempt["input_snapshot_ref"])
    same(semantic.get("attempt_count", 0), len(attempts), "model attempt count")
    same(len(result.model_calls), len(returned), "actual returned model calls")
    for review in passes:
        if not review.get("output_snapshot_ref"):
            continue
        judgments = store.read(review["output_snapshot_ref"]).normalized_json
        for group, pool in (("repositories", repositories), ("discussions", discussions)):
            id_key = "repository_id" if group == "repositories" else "discussion_id"
            for judgment in judgments.get(group, []):
                require(judgment[id_key] in pool, "Model output refers to unknown review item")
                for citation in judgment.get("citations", []):
                    source = sources[citation["source_id"]]
                    check_quote(source, store.read(source.snapshot_ref), citation["excerpt"], judgment[id_key], helpers)
    same(summary["model_calls"], [call.model_dump(mode="json") for call in result.model_calls], "model identities")
    same(summary["model_call_count"], len(result.model_calls), "summary model count")
    counts = dict(Counter(row["provider"] for row in http_audit["http_calls"]))
    same(summary["http_counts"], counts, "HTTP provider counts")
    require(summary["blocked_http_count"] == http_audit["blocked_count"] == 0, "Blocked HTTP operation")
    deepseek_returned = sum(row["provider"] == "deepseek" and row.get("http_status") == 200
                            for row in http_audit["http_calls"])
    require(deepseek_returned >= len(returned), "Recorded model calls exceed actual successful provider HTTP")
    require(result.external_request_count <= 40, "Shared source-read operation budget exceeded")
    for field, actual in {
        "execution_status": _result_state(result), "check_status": result.dimension.check_status,
        "scope_complete": result.checks.scope_complete, "code_availability": result.checks.code_availability,
        "reproduction_signal": result.checks.reproduction_signal, "code_score": result.dimension.score,
        "score_rule_ids": result.dimension.score_rule_ids, "repository_count": len(repositories),
        "discussion_count": len(discussions), "source_count": len(sources),
        "repository_associations": dict(Counter(item.association for item in result.checks.repositories)),
        "discussion_signals": dict(Counter(item.signal for item in result.checks.discussions)),
        "discussion_relevance": dict(Counter(item.relevance for item in result.checks.discussions)),
        "scoring_eligible_discussions": 0, "external_review": result.external_review,
        "source_statuses": dict(Counter(source.fetch_status for source in result.sources)),
        "external_request_count": result.external_request_count,
    }.items():
        same(summary[field], actual, f"typed summary {field}")
    return {
        "status": "passed", "run_dir": str(run_dir), "sample_id": summary["sample_id"],
        "verified_implementation_root": str(implementation_root),
        "frozen_helper_sources_used": sorted(helpers),
        "reported_execution_status": summary["execution_status"], "reported_acceptance_status": summary["status"],
        "execution_complete": summary["execution_status"] == "completed",
        "code_score": result.dimension.score, "sources_verified": len(sources),
        "evidence_links_verified": len(evidence), "external_quotes_verified": quoted_external,
        "fresh_paper_anchors_verified": paper_anchors, "transitive_snapshots_verified": len(visited),
        "implementation_fact_snapshots_verified": implementation_facts,
        "implementation_files_verified": len(summary["implementation_sha256"]),
        "model_attempts_verified": len(attempts), "returned_model_calls_verified": len(returned),
        "successful_deepseek_http": deepseek_returned, "wire_http_counts": counts,
        "source_read_operations": result.external_request_count,
        "scoring_eligible_discussions": 0, "audit_network_calls": 0, "audit_model_calls": 0,
        "semantic_accuracy_assessed": False, **discovery_counts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO,
                        help="Complete backend runtime/import root")
    parser.add_argument("--implementation-root", type=Path,
                        help="Frozen implementation bytes to verify and use for audit helpers; defaults to backend")
    parser.add_argument("--output", type=Path, required=True, help="New audit JSON; never overwrites an earlier audit")
    args = parser.parse_args(argv)
    require(not args.output.exists(), "Output audit already exists")
    repo = args.repo_root.resolve()
    implementation_root = (args.implementation_root or repo).resolve()
    sys.path.insert(0, str(repo))
    selection = json.loads(args.samples.read_text())
    content = {key: value for key, value in selection.items() if key != "selection_sha256"}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    same(selection["selection_sha256"], digest, "sample selection digest")
    require(selection["is_human_gold"] is False, "Selection provenance was misrepresented")
    import httpx

    def forbidden(*_args, **_kwargs):
        raise AssertionError("The local audit cannot call HTTP or models")

    report = {"schema_version": "code-discovery-artifact-audit-1", "status": "passed", "runs": [],
              "network_calls": 0, "model_calls": 0, "semantic_accuracy_assessed": False,
              "audit_script_sha256": sha(__file__)}
    original_artifact_dir = os.environ.get("CREDIBILITY_ARTIFACT_DIR")
    try:
        with patch.object(httpx.Client, "send", forbidden), patch.object(httpx.AsyncClient, "send", forbidden):
            for run in args.run_dirs:
                run = run.resolve()
                os.environ["CREDIBILITY_ARTIFACT_DIR"] = str(run / "artifacts")
                try:
                    report["runs"].append(audit_run(run, selection, repo, implementation_root))
                except Exception as exc:
                    report["status"] = "failed"
                    report["runs"].append({"status": "failed", "run_dir": str(run),
                                           "error_type": type(exc).__name__, "error": str(exc)})
    finally:
        if original_artifact_dir is None:
            os.environ.pop("CREDIBILITY_ARTIFACT_DIR", None)
        else:
            os.environ["CREDIBILITY_ARTIFACT_DIR"] = original_artifact_dir
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
