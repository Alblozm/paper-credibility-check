"""Verify raw model choices cannot fabricate their server-resolved quotations."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.credibility import code_discovery, code_model
from app.credibility.code_checks import check_code
from app.credibility.context import CheckContext
from app.credibility.models import CredibilityExternalSource
from app.credibility.snapshots import SnapshotStore, capture_snapshot
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.paper import Paper
from scripts.audit_code_discovery import (
    audit_discovery,
    discovery_page_readable,
    resolved_discovery_output,
)


@pytest.fixture
def span_output(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    title = "A Precise Learning Method for Models"
    context = CheckContext(source={"type": "arxiv", "arxiv_id": "2401.12345v1"}, repo_root=Path("."),
        generated_at="2026-10-19T00:00:00Z", ref=None, unresolved_reason=None,
        doc=ParsedDocument(paper=Paper(
            paper_id="span_audit", title=title, arxiv_id="2401.12345v1", parse_quality="good"),
                           blocks=[TextBlock(chunk_id="body", page_start=1, text="We evaluate models.")]))
    session = code_discovery.DiscoverySession(context, check_code(context), None)
    sid = "code_discovery_src_" + session.prefix + "_1"
    url = "https://github.com/example/method"
    text = "Official code for " + title + r" (2401.12345), with \(x\_1\)."
    ref = capture_snapshot(kind="project_page", normalized_json={"operation": "fetch", "text": text},
                           normalized_text=text, fetch_status="ok", http_status=200)
    source = CredibilityExternalSource(source_id=sid, kind="project_page", url=url, fetch_status="ok",
        http_status=200, retrieved_at=context.generated_at, observed_at=context.generated_at,
        is_cached=False, snapshot_ref=ref, association_reason="Exact fixture page.")
    session.result.sources.append(source)
    session.documents[sid] = text
    candidate = session.register(url, source_ids=[sid], parent_source=sid)
    # register() discovers a URL; only an actual own-page fetch grants citation
    # permissions. Reproduce fetch()'s explicit grant in this local fixture.
    session.candidate_origins.setdefault(candidate.candidate_id, set()).add(sid)
    span_id = session.register_citation_spans(sid, text, ref)[0]["span_id"]
    raw = {"candidates": [{"candidate_id": candidate.candidate_id, "resource_kind": "official_implementation",
            "reason": "Exact paper declaration.", "citations": [{"source_id": sid, "span_id": span_id}]}],
           "reason": "Bounded evidence review."}
    raw_ref = capture_snapshot(kind="code_discovery_model_output", normalized_text=json.dumps(raw))
    accepted = code_discovery._Judgments.model_validate(raw)
    session.apply(accepted)
    output_ref = capture_snapshot(kind="code_discovery_validated_output", normalized_text=accepted.model_dump_json(),
                                  parent_ref=raw_ref)
    spans_ref = capture_snapshot(kind="code_discovery_citation_spans",
                                 normalized_json={"spans": session.citation_spans})
    audit = {"raw_output_snapshot_ref": raw_ref, "output_snapshot_refs": [raw_ref], "output_snapshot_ref": output_ref,
             "citation_spans_snapshot_ref": spans_ref, "candidate_origins": {candidate.candidate_id: [sid]}}
    return SimpleNamespace(audit=audit, store=SnapshotStore(tmp_path / "artifacts"), documents={sid: text},
        sources={sid: source}, context=context, helpers={"code_discovery": code_discovery, "code_model": code_model},
        raw=raw, spans=session.citation_spans, sid=sid, candidates={candidate.candidate_id: candidate})


def resolve(state):
    return resolved_discovery_output(state.audit, state.store, state.documents,
                                     state.sources, state.context, state.helpers, candidates=state.candidates)


def test_span_audit_reconstructs_exact_backslashes_from_raw_model_selection(span_output):
    judgments, count = resolve(span_output)
    assert count == 1
    assert judgments.candidates[0].citations[0].excerpt == span_output.documents[span_output.sid]


def test_new_span_protocol_is_rechecked_against_raw_model_output(span_output):
    span_output.audit["model_citation_protocol"] = "registered_spans_only"
    assert resolve(span_output)[1] == 1


def test_new_span_protocol_cannot_be_bypassed_with_a_legacy_exact_quote(span_output):
    raw = copy.deepcopy(span_output.raw)
    raw["candidates"][0]["citations"] = [{
        "source_id": span_output.sid, "excerpt": span_output.documents[span_output.sid]}]
    raw_ref = capture_snapshot(kind="code_discovery_model_output", normalized_text=json.dumps(raw))
    span_output.audit.update(model_citation_protocol="registered_spans_only",
                             raw_output_snapshot_ref=raw_ref, output_snapshot_refs=[raw_ref])
    span_output.audit["output_snapshot_ref"] = capture_snapshot(
        kind="code_discovery_validated_output",
        normalized_text=code_discovery._Judgments.model_validate(raw).model_dump_json(), parent_ref=raw_ref)
    with pytest.raises(ValueError, match="span_id"):
        resolve(span_output)


def test_span_audit_rejects_changed_source_range(span_output):
    spans = copy.deepcopy(span_output.spans)
    next(iter(spans.values()))["start"] += 1
    span_output.audit["citation_spans_snapshot_ref"] = capture_snapshot(
        kind="code_discovery_citation_spans", normalized_json={"spans": spans})
    with pytest.raises(ValueError, match="reconstructed citation span registry"):
        resolve(span_output)


def test_span_audit_rejects_unrecorded_raw_output(span_output):
    span_output.audit["output_snapshot_refs"] = []
    with pytest.raises(ValueError, match="outside recorded responses"):
        resolve(span_output)


def test_span_audit_rejects_server_changing_the_models_reason(span_output):
    accepted = code_discovery._Judgments.model_validate_json(
        span_output.store.read(span_output.audit["output_snapshot_ref"]).normalized_text)
    accepted.candidates[0].reason = "A different unsupported reason."
    span_output.audit["output_snapshot_ref"] = capture_snapshot(
        kind="code_discovery_validated_output", normalized_text=accepted.model_dump_json(),
        parent_ref=span_output.audit["raw_output_snapshot_ref"])
    with pytest.raises(ValueError, match="only server-owned citation resolution"):
        resolve(span_output)


@pytest.mark.parametrize("legacy", [False, True])
def test_forged_candidate_origins_cannot_lend_a_parent_official_span(span_output, legacy):
    candidate = next(iter(span_output.candidates.values()))
    candidate.url = "https://github.com/example/dependency"
    if legacy:
        accepted = code_discovery._Judgments.model_validate_json(
            span_output.store.read(span_output.audit["output_snapshot_ref"]).normalized_text)
        accepted.candidates[0].citations[0].span_id = None
        span_output.audit.pop("raw_output_snapshot_ref")
        span_output.audit["output_snapshot_ref"] = capture_snapshot(
            kind="code_discovery_model_output", normalized_text=accepted.model_dump_json())
    with pytest.raises(ValueError, match="local Code link binding"):
        resolve(span_output)


def test_parent_quote_with_exact_local_code_link_can_bind_child(span_output):
    candidate = next(iter(span_output.candidates.values()))
    child = "https://github.com/example/actual-code"
    text = ("Official code for " + span_output.context.doc.paper.title
            + " (2401.12345). [Code](" + child + ")")
    candidate.url = child
    source = span_output.sources[span_output.sid]
    source.snapshot_ref = capture_snapshot(kind="project_page",
        normalized_json={"operation": "fetch", "text": text}, normalized_text=text,
        fetch_status="ok", http_status=200)
    span_output.documents[span_output.sid] = text
    registry = object.__new__(code_discovery.DiscoverySession)
    registry.ctx = span_output.context
    registry.prefix = span_output.sid.removeprefix("code_discovery_src_").removesuffix("_1")
    registry.citation_spans = {}
    registry.register_citation_spans(span_output.sid, text, source.snapshot_ref)
    span_output.audit["citation_spans_snapshot_ref"] = capture_snapshot(
        kind="code_discovery_citation_spans", normalized_json={"spans": registry.citation_spans})
    accepted = code_discovery._Judgments.model_validate(span_output.raw)
    accepted.candidates[0].citations[0].excerpt = text
    span_output.audit["output_snapshot_ref"] = capture_snapshot(kind="code_discovery_validated_output",
        normalized_text=accepted.model_dump_json(), parent_ref=span_output.audit["raw_output_snapshot_ref"])
    assert resolve(span_output)[1] == 1


def blocked_page(state):
    text = "Just a moment... Please enable JavaScript and cookies to continue."
    reason = code_discovery.page_content_problem("Just a moment...", text)
    assert reason == "content_browser_verification"
    url = "https://arxiv.org/abs/2401.12345v1"
    parent = capture_snapshot(kind="code_discovery_fetch", normalized_json={
        "operation": "fetch", "url": url, "title": "Just a moment...", "text": text,
        "status": "ok", "http_status": 200, "content_status": "blocked", "content_reason_code": reason,
    }, normalized_text=text, requested_url=url, fetch_status="ok", http_status=200,
        metadata={"origin_http_status": None})
    session = code_discovery.DiscoverySession(state.context, check_code(state.context), None)
    observed = SimpleNamespace(status="ok", http_status=200, retrieved_at=state.context.generated_at,
                               snapshot_ref=parent)
    session.add_source(observed, url=url, kind="project_page", reason="Blocked content fixture.",
                       normalized_text=text, content_problem=reason)
    return session.result.sources[-1], parent, reason


def test_challenge_audit_preserves_successful_provider_parent(span_output):
    source, parent, reason = blocked_page(span_output)
    saved = span_output.store.read(source.snapshot_ref)
    assert discovery_page_readable(source, saved, span_output.store, span_output.helpers) == (False, reason)
    assert source.fetch_status == "access_denied" and source.http_status == 200
    provider = span_output.store.read(parent)
    assert provider.manifest["fetch_status"] == "ok" and provider.manifest["http_status"] == 200


@pytest.mark.parametrize("metadata,fetch_status,http_status", [
    ({"content_status": "readable"}, "access_denied", 200),
    ({"transport_fetch_status": "access_denied"}, "access_denied", 200),
    ({"provider_http_status": 403}, "access_denied", 200),
    ({"origin_http_status": 403}, "access_denied", 200),
    ({}, "ok", 200),
    ({}, "access_denied", 403),
])
def test_challenge_audit_rejects_inconsistent_content_or_transport_state(
        span_output, metadata, fetch_status, http_status):
    source, _, _ = blocked_page(span_output)
    source.snapshot_ref = span_output.store.derive(source.snapshot_ref, metadata=metadata,
                                                  fetch_status=fetch_status, http_status=http_status)
    source.fetch_status, source.http_status = fetch_status, http_status
    with pytest.raises(ValueError, match="Mismatch|fabricated"):
        discovery_page_readable(source, span_output.store.read(source.snapshot_ref),
                                span_output.store, span_output.helpers)


@pytest.mark.parametrize("mutation", [None, "completed", "permission", "tool_ok", "no_limitation"])
def test_blocked_page_audit_requires_partial_unreadable_state(span_output, mutation):
    source, _, reason = blocked_page(span_output)
    result = check_code(span_output.context)
    result.sources = [source]
    result.discovery.status = "completed" if mutation == "completed" else "partial"
    result.discovery.reason = "Provider succeeded; page content remains blocked."
    result.discovery.limitations = [] if mutation == "no_limitation" else [reason]
    result.discovery.budget.max_source_reads = 1
    result.discovery.budget.max_candidates = 1
    result.discovery.budget.max_tool_calls = 1
    result.discovery.budget.source_reads_used = 1
    result.discovery.budget.candidates_inspected = 1
    result.discovery.budget.tool_calls_used = 1
    result.discovery.candidates = [code_discovery.CredibilityCodeDiscoveryCandidate(
        candidate_id="blocked", url=source.url, source_ids=[source.source_id])]
    audit = {"status": result.discovery.status, "budget": result.discovery.budget.model_dump(mode="json"),
        "attempt_count": 0, "attempts": [], "model_call_count": 0, "tool_call_count": 1,
        "external_request_count": 1, "candidate_origins": {"blocked": [source.source_id]}
        if mutation == "permission" else {}, "tool_calls": [{"name": "fetch_discovery_page",
            "source_id": source.source_id, "candidate_id": "blocked", "reason": reason,
            "status": "ok" if mutation == "tool_ok" else "access_denied"}]}
    result.discovery.audit_ref = capture_snapshot(kind="code_discovery_audit", normalized_json=audit)
    summary = {"code_discovery": result.discovery.model_dump(mode="json"),
        "discovery_status": result.discovery.status,
        "discovery_budget": result.discovery.budget.model_dump(mode="json"),
        "discovery_candidates": [row.model_dump(mode="json") for row in result.discovery.candidates],
        "discovery_audit_snapshot_ref": result.discovery.audit_ref, "source_links_verified": True,
        "discovery_audit_snapshot_verified": True}
    args = (result, summary, span_output.context, span_output.store, {source.source_id: source},
            span_output.helpers, {"http_calls": [{"provider": "deepseek_fetch", "method": "POST",
                "url": "https://api.deepseek.com/v1/tools/fetch"}]})
    if mutation:
        with pytest.raises(ValueError, match="Blocked page"):
            audit_discovery(*args)
    else:
        assert audit_discovery(*args)["discovery_status"] == "partial"
