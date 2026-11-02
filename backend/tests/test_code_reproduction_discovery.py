"""Offline contracts for bounded reproduction-report snippet supplementation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.credibility.code_checks import check_code
from app.credibility.code_discovery_provider import DiscoveryProviderResult
from app.credibility.code_reproduction_discovery import (
    PROMPT_VERSION,
    REPRODUCTION_VERSION,
    _canonical_url,
    reproduction_queries,
    search_reproduction_reports,
)
from app.credibility.context import CheckContext
from app.credibility.item_scoring import score_code_result
from app.credibility.models import CredibilityCodeDiscoveryCandidate, CredibilityExternalSource, CredibilityRepository
from app.credibility.rubric import load_credibility_rubric
from app.credibility.snapshots import (
    SnapshotError,
    SnapshotStore,
    capture_snapshot,
    read_snapshot,
    verify_report_snapshots,
)
from app.llm.base import LLMResponse
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "FreeAnchor: Learning to Match Anchors for Visual Object Detection"
URL = "https://openreview.net/pdf?id=Nrdu2F6jhF"
STAMP = "2026-10-21T00:00:00Z"
HIT = {"url": URL, "title": "Reproducibility report for FreeAnchor",
       "snippet": "We successfully reproduced the baseline result of FreeAnchor paper."}


@pytest.fixture
def ctx():
    return CheckContext(
        source={"type": "arxiv", "arxiv_id": "1909.02466v1"}, repo_root=Path("."),
        generated_at=STAMP, ref=None, unresolved_reason=None, model_id="test-model",
        doc=ParsedDocument(
            paper=Paper(paper_id="freeanchor", title=TITLE, arxiv_id="1909.02466v1",
                        parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(chunk_id="body", text="We evaluate the proposed detection method.", page_start=1)],
        ),
    )


@pytest.fixture
def result(ctx):
    checked = check_code(ctx)
    checked.checks.code_availability = "available"
    checked.discovery.status = "not_needed"
    checked.discovery.reason = "Existing official implementation evidence is sufficient."
    return checked


class Provider:
    search_endpoint = "https://api.deepseek.com/v1/tools/search"
    search_label = "deepseek_search"

    def __init__(self, hits=None, *, replies=None, max_requests=2):
        self.hits = [HIT] if hits is None else hits
        self.replies = replies
        self.max_requests = max_requests
        self.request_count = 0
        self.queries = []
        self.snapshots = []

    @property
    def remaining_requests(self):
        return max(0, self.max_requests - self.request_count)

    def search(self, query, *, count):
        assert count == 5 and self.remaining_requests > 0
        self.queries.append(query)
        self.request_count += 1
        reply = self.replies[self.request_count - 1] if self.replies else {"hits": self.hits}
        if reply.get("exception"):
            raise RuntimeError("sensitive exception text must never enter the audit")
        status = reply.get("status", "ok")
        hits = reply.get("hits", []) if status == "ok" else []
        ref = capture_snapshot(
            kind="code_discovery_search", normalized_json={"query": query, "hits": hits},
            requested_url=self.search_endpoint, fetch_status=status,
            http_status=200 if status == "ok" else 429, retrieved_at=STAMP,
        )
        self.snapshots.append(ref)
        return DiscoveryProviderResult(
            operation="search", status=status, http_status=200 if status == "ok" else 429,
            retrieved_at=STAMP, reason_code="search_results", snapshot_ref=ref,
            query=query, hits=hits,
        )


class Client:
    model_version = "deepseek-offline-test"

    def __init__(self, *, transform=None, fail=False):
        self.tasks = []
        self.transform, self.fail = transform, fail

    def complete_json(self, system, task, *, temperature, max_tokens):
        assert "UNTRUSTED DATA" in system and temperature == 0.0
        assert 0 < max_tokens <= 4000
        task = json.loads(task)
        self.tasks.append(task)
        if self.fail:
            raise RuntimeError("secret provider exception")
        labels = {"candidates": [{
            "candidate_id": row["candidate_id"], "relation": "this_paper_reproduction",
            "outcome": "success_reported", "span_ids": [row["registered_spans"][0]["span_id"]],
        } for row in task["candidates"]]}
        if self.transform:
            labels = self.transform(labels, task)
        text = labels if isinstance(labels, str) else json.dumps(labels)
        return LLMResponse(text=text, model_id="test-model", model_version=self.model_version,
                           raw={"reasoning_content": "private reasoning must never be persisted"})


def run(ctx, result, provider=None, client=None, *, cap=60):
    return search_reproduction_reports(ctx, result, client, provider, max_source_reads=cap)


def assert_closed(result):
    sources = {row.source_id for row in result.sources}
    evidence = {row.evidence_id for row in result.evidence}
    queries = {row.query_id for row in result.discovery.queries}
    assert len(sources) == len(result.sources)
    assert len(evidence) == len(result.evidence)
    assert len(queries) == len(result.discovery.queries)
    assert len({row.candidate_id for row in result.discovery.candidates}) == len(result.discovery.candidates)
    for query in result.discovery.queries:
        assert set(query.source_ids) <= sources
    for candidate in result.discovery.candidates:
        assert set(candidate.source_ids) <= sources
        assert set(candidate.evidence_ids) <= evidence
        assert set(candidate.query_ids) <= queries
    for quote in result.evidence:
        if quote.origin == "external":
            assert quote.source_id in sources


def test_available_official_code_still_gets_exact_unverified_reproduction_lead(ctx, result):
    before = result.model_copy(deep=True)
    provider, client = Provider(), Client()
    checked = run(ctx, result, provider, client)
    assert checked.result is result and checked.status == "completed"
    assert provider.queries == ['"FreeAnchor" reproducibility', '"FreeAnchor" reproduction']
    assert len(client.tasks) == 1
    candidate, = result.discovery.candidates
    assert (candidate.resource_kind, candidate.status, candidate.repository_id) == (
        "third_party_reproduction", "candidate", None,
    )
    assert "Search-snippet-level, unverified" in candidate.reason
    assert "reports success" in candidate.reason
    assert result.checks == before.checks and result.dimension == before.dimension
    rubric = load_credibility_rubric("0.2-provisional-complete")
    assert score_code_result(result, rubric=rubric).dimension == score_code_result(before, rubric=rubric).dimension
    quote = result.evidence[-1]
    assert quote.location_confidence == "low" and "baseline result" in quote.excerpt
    source = next(row for row in result.sources if row.source_id == quote.source_id)
    snapshot = read_snapshot(source.snapshot_ref)
    assert source.kind == "metadata" and snapshot.normalized_text == quote.excerpt
    assert snapshot.manifest["metadata"]["evidence_level"] == "search_snippet"
    assert snapshot.manifest["final_url"] is None
    assert result.external_request_count == result.discovery.budget.source_reads_used == 2
    assert result.discovery.budget.queries_used == result.discovery.budget.tool_calls_used == 2
    assert result.discovery.budget.model_rounds_used == 1
    assert len(checked.audit["tool_calls"]) == 2 and len(checked.audit["attempts"]) == 1
    assert all(row["execution"] == "fixed_backend_search" for row in checked.audit["tool_calls"])
    assert_closed(result)
    verify_report_snapshots({"sources": result.sources, "code_discovery": result.discovery})


@pytest.mark.parametrize("hits", [
    [], [{"url": URL, "title": "Different Paper reproduction", "snippet": "We replicated it."}],
])
def test_empty_or_unrelated_results_do_not_call_model_or_assert_absence(ctx, result, hits):
    client = Client(fail=True)
    checked = run(ctx, result, Provider(hits), client)
    assert checked.status == "completed" and not client.tasks
    assert result.discovery.candidates == []
    assert "no report exists" not in result.discovery.reason.lower()
    assert "absence" not in result.discovery.reason.lower()


def test_queries_bound_long_titles_and_do_not_use_ordinary_title_prefix(ctx):
    ctx.doc.paper.title = "Understanding: " + "long detailed scientific title " * 12
    queries = reproduction_queries(ctx)
    assert len(queries) == 2 and all(len(query) <= 128 for query in queries)
    assert all(query.startswith('"Understanding: long detailed') for query in queries)


def test_brand_identity_requires_token_boundary(ctx, result):
    ctx.doc.paper.title = "LoRA: Low Rank Adaptation of Large Language Models"
    hits = [{"url": "https://example.org/report", "title": "AdaLoRA reproducibility",
             "snippet": "We reproduce AdaLoRA successfully."}]
    client = Client()
    checked = run(ctx, result, Provider(hits), client)
    assert checked.status == "completed" and not client.tasks and not result.discovery.candidates


@pytest.mark.parametrize("hit", [
    {"url": "https://arxiv.org/pdf/1909.02466v2", "title": "FreeAnchor reproducibility", "snippet": HIT["snippet"]},
    {"url": "https://example.org/mirror", "title": TITLE, "snippet": HIT["snippet"]},
    {"url": "https://example.org/mirror", "title": TITLE + " | Papers with Code", "snippet": HIT["snippet"]},
    {"url": "https://github.com/Alice/Official/issues/2", "title": HIT["title"], "snippet": HIT["snippet"]},
    {"url": "http://127.0.0.1/report", "title": HIT["title"], "snippet": HIT["snippet"]},
    {"url": "https://example.org/report?api_key=private", "title": HIT["title"], "snippet": HIT["snippet"]},
])
def test_original_paper_official_repository_mirrors_and_unsafe_urls_are_excluded(ctx, result, hit):
    source = CredibilityExternalSource(
        source_id="official", kind="repository", url="https://api.github.com/repos/alice/official",
        fetch_status="ok", http_status=200, retrieved_at=STAMP, observed_at=STAMP,
        is_cached=False, snapshot_ref=None, association_reason="Confirmed official repository.",
    )
    result.sources.append(source)
    result.checks.repositories.append(CredibilityRepository(
        repository_id="official", source_id="official", association="confirmed", association_reason="Official.",
        association_evidence_ids=[], availability="accessible", implementation_evidence_ids=[], inspected_revision=None,
    ))
    client = Client()
    checked = run(ctx, result, Provider([hit]), client)
    assert checked.status == "completed" and not client.tasks and not result.discovery.candidates


def test_openreview_pdf_forum_deduplicate_with_all_search_provenance(ctx, result):
    forum_hit = {**HIT, "url": "https://openreview.net/forum?id=Nrdu2F6jhF&utm_source=search"}
    provider = Provider(replies=[{"hits": [HIT]}, {"hits": [forum_hit]}])
    client = Client()
    run(ctx, result, provider, client)
    candidate, = result.discovery.candidates
    assert candidate.url == "https://openreview.net/forum?id=Nrdu2F6jhF"
    assert len(candidate.query_ids) == 2 and len(candidate.source_ids) == len(candidate.evidence_ids) == 1
    assert len(client.tasks[0]["candidates"]) == 1
    assert _canonical_url("https://openreview.net/pdf?id=one&id=two") is None
    assert_closed(result)


@pytest.mark.parametrize("readme_url", [
    "https://www.GitHub.com/Sylvain-Topeza/inria-internship/blob/main/README.md",
    "https://github.com/SYlVAIN-Topeza/INRIA-internship/raw/refs/heads/main/readme.rst",
    "https://raw.githubusercontent.com/Sylvain-Topeza/inria-internship/main/README.md",
])
def test_github_repository_and_root_readme_merge_without_losing_query_sources_or_spans(ctx, result, readme_url):
    root_hit = {**HIT, "url": "https://github.com/Sylvain-Topeza/inria-internship"}
    readme_hit = {**HIT, "url": readme_url,
                 "snippet": "The FreeAnchor reproduction reports successful baseline results."}
    provider = Provider(replies=[{"hits": [root_hit]}, {"hits": [readme_hit]}])

    def cite_all(labels, task):
        labels["candidates"][0]["span_ids"] = [span["span_id"]
                                                for span in task["candidates"][0]["registered_spans"]]
        return labels

    client = Client(transform=cite_all)
    before = result.checks.model_dump()
    checked = run(ctx, result, provider, client)
    candidate, = result.discovery.candidates
    assert checked.status == "completed" and candidate.url == "https://github.com/sylvain-topeza/inria-internship"
    assert len(client.tasks[0]["candidates"]) == 1
    assert len(client.tasks[0]["candidates"][0]["registered_spans"]) == 2
    assert len(candidate.query_ids) == len(candidate.source_ids) == len(candidate.evidence_ids) == 2
    assert result.checks.model_dump() == before
    assert_closed(result)
    verify_report_snapshots({"sources": result.sources, "code_discovery": result.discovery})


def test_github_other_repositories_issues_and_report_subdirectories_remain_distinct(ctx, result):
    root = "https://github.com/Sylvain-Topeza/inria-internship"
    other = "https://github.com/another-author/inria-internship/blob/main/README.md"
    assert _canonical_url(root) != _canonical_url(other)
    for path in ("/issues/42", "/blob/main/reports/README.md", "/blob/main/reports/reproduction.md"):
        assert _canonical_url(root + path) != _canonical_url(root)
    checked = run(ctx, result, Provider([{**HIT, "url": root}, {**HIT, "url": other}]), Client())
    assert checked.status == "completed" and len(result.discovery.candidates) == 2
    assert {row.url for row in result.discovery.candidates} == {
        "https://github.com/sylvain-topeza/inria-internship", "https://github.com/another-author/inria-internship",
    }
    assert_closed(result)


def test_official_repository_root_readme_and_raw_report_subpaths_remain_excluded(ctx, result):
    result.sources.append(CredibilityExternalSource(
        source_id="official", kind="repository", url="https://api.github.com/repos/Sylvain-Topeza/inria-internship",
        fetch_status="ok", http_status=200, retrieved_at=STAMP, observed_at=STAMP,
        is_cached=False, snapshot_ref=None, association_reason="Confirmed official repository fixture.",
    ))
    result.checks.repositories.append(CredibilityRepository(
        repository_id="official", source_id="official", association="confirmed", association_reason="Official.",
        association_evidence_ids=[], availability="accessible", implementation_evidence_ids=[], inspected_revision=None,
    ))
    hits = [{**HIT, "url": url} for url in [
        "https://www.github.com/Sylvain-Topeza/inria-internship/blob/main/README.md",
        "https://raw.githubusercontent.com/Sylvain-Topeza/inria-internship/main/README.md",
        "https://raw.githubusercontent.com/Sylvain-Topeza/inria-internship/main/reports/report.md",
        "https://github.com/Sylvain-Topeza/inria-internship/issues/42",
    ]]
    client = Client()
    checked = run(ctx, result, Provider(hits), client)
    assert checked.status == "completed" and not client.tasks and result.discovery.candidates == []


@pytest.mark.parametrize("fault", ["invented_id", "invented_span", "other_candidate_span", "duplicate_id", "missing_id",
                                  "empty_positive_citation", "free_reason", "invalid_json", "invalid_outcome"])
def test_invalid_model_identity_or_citations_are_rejected_atomically_without_retry(ctx, result, fault):
    hits = [HIT, {**HIT, "url": "https://example.org/independent-report"}]

    def corrupt(labels, task):
        rows = labels["candidates"]
        if fault == "invented_id":
            rows[1]["candidate_id"] = "invented"
        elif fault == "invented_span":
            rows[1]["span_ids"] = ["invented"]
        elif fault == "other_candidate_span":
            rows[1]["span_ids"] = rows[0]["span_ids"]
        elif fault == "duplicate_id":
            rows[1]["candidate_id"] = rows[0]["candidate_id"]
        elif fault == "missing_id":
            rows.pop()
        elif fault == "empty_positive_citation":
            rows[1]["span_ids"] = []
        elif fault == "free_reason":
            rows[1]["reason"] = "unread claims must never be published"
        elif fault == "invalid_outcome":
            rows[1]["outcome"] = "independently_verified"
        elif fault == "invalid_json":
            return "sensitive invalid provider output"
        return labels

    client = Client(transform=corrupt)
    checked = run(ctx, result, Provider(hits), client)
    assert checked.status == "partial" and len(client.tasks) == len(checked.model_calls) == 1
    assert result.discovery.candidates == [] and result.evidence == []
    assert checked.audit["attempts"][0]["status"] == "failed"
    assert "labels_snapshot_ref" not in checked.audit["attempts"][0]


@pytest.mark.parametrize("relation", ["uncertain", "not_this_paper_reproduction"])
def test_model_can_reject_rule_matched_but_semantically_unrelated_snippets(ctx, result, relation):
    def reject(labels, task):
        for row in labels["candidates"]:
            row.update(relation=relation, outcome="not_stated", span_ids=[])
        return labels

    checked = run(ctx, result, Provider(), Client(transform=reject))
    assert checked.status == "completed" and result.discovery.candidates == [] and result.evidence == []


@pytest.mark.parametrize("room", [0, 1, 2])
def test_global_read_cap_is_shared_and_skipped_queries_remain_not_run(ctx, result, room):
    result.external_request_count = 60 - room
    provider = Provider(hits=[])
    checked = run(ctx, result, provider, Client())
    assert result.external_request_count == 60 and provider.request_count == room
    assert len(checked.audit["tool_calls"]) == room
    assert sum(query.status == "not_run" for query in result.discovery.queries) == 2 - room
    assert checked.status == ("completed" if room == 2 else "partial")
    assert result.discovery.budget.source_reads_used == room


def test_unavailable_search_is_disabled_not_an_incomplete_check(ctx, result):
    before = result.discovery.status
    checked = run(ctx, result, None, Client())
    assert checked.status == "disabled" and result.discovery.status == before
    assert checked.warnings and checked.warnings[0] in result.checks.scope_limitations
    assert result.external_request_count == 0 and not result.discovery.candidates and not result.sources


@pytest.mark.parametrize("failure", ["http", "exception", "model", "client_missing"])
def test_failure_is_audited_counted_and_preserved_without_negative_inference(ctx, result, failure):
    provider = Provider()
    if failure in {"http", "exception"}:
        reply = {"status": "rate_limited"} if failure == "http" else {"exception": True}
        provider = Provider(replies=[reply, reply])
    client = None if failure == "client_missing" else Client(fail=failure == "model")
    checked = run(ctx, result, provider, client)
    assert checked.status == result.discovery.status == "partial" and checked.warnings
    assert result.external_request_count == (0 if provider is None else provider.request_count)
    assert not result.discovery.candidates
    if failure in {"http", "exception"}:
        assert not result.sources  # Failed searches remain audit observations, not failed repository facts.
    archive = json.dumps(read_snapshot(result.discovery.audit_ref).normalized_json)
    assert "sensitive exception" not in archive and "secret provider" not in archive
    verify_report_snapshots({"sources": result.sources, "code_discovery": result.discovery})


@pytest.mark.parametrize("prior", ["partial", "failed"])
def test_successful_supplement_never_upgrades_preexisting_incomplete_discovery(ctx, result, prior):
    result.discovery.status = prior
    checked = run(ctx, result, Provider(), Client())
    assert checked.status == "completed" and result.discovery.status == prior
    assert len(result.discovery.candidates) == 1


@pytest.mark.parametrize("mode", ["success", "empty", "search_failure", "model_failure", "no_budget", "no_provider"])
def test_continuation_reuses_all_attempt_outcomes_without_paid_reads_or_duplicate_ids(ctx, result, mode):
    provider = None if mode == "no_provider" else Provider(hits=[] if mode == "empty" else None)
    if mode == "search_failure":
        provider = Provider(replies=[{"status": "rate_limited"}, {"status": "rate_limited"}])
    if mode == "no_budget":
        result.external_request_count = 60
    client = Client(fail=mode == "model_failure")
    first = run(ctx, result, provider, client)
    original = result.model_dump(mode="json")
    prior_reads, prior_calls = provider.request_count if provider else 0, len(client.tasks)
    # The surrounding executor resets current-attempt HTTP usage before replay.
    result.external_request_count = 0
    second = run(ctx, result, provider, client)
    assert second.status == first.status and second.audit["reused"]
    assert second.audit["external_request_count"] == second.audit["attempt_count"] == 0
    assert second.audit["tool_calls"] == second.audit["attempts"] == second.audit["rounds"] == []
    assert second.model_calls == [] and result.external_request_count == 0
    assert (provider.request_count if provider else 0, len(client.tasks)) == (prior_reads, prior_calls)
    after = result.model_dump(mode="json")
    original["external_request_count"] = 0
    assert after == original
    assert_closed(result)


def test_audit_links_original_discovery_and_all_snippet_ancestors_and_detects_tampering(ctx, result):
    previous = capture_snapshot(kind="code_discovery_audit", normalized_json={"status": "completed"})
    result.discovery.audit_ref = previous
    checked = run(ctx, result, Provider(), Client())
    audit = read_snapshot(result.discovery.audit_ref)
    assert audit.manifest["kind"] == "code_discovery_audit"
    assert audit.normalized_json["previous_discovery_audit_ref"] == previous
    assert checked.audit["attempts"][0]["input_snapshot_ref"]
    verify_report_snapshots({"sources": result.sources, "code_discovery": result.discovery})
    store = SnapshotStore()
    store._path(previous.rsplit(":", 1)[-1]).write_bytes(b"tampered")
    with pytest.raises(SnapshotError, match="integrity"):
        run(ctx, result, Provider(), Client())


def test_empty_source_failed_attempt_is_restored_after_executor_rebuilds_base_facts(ctx, result):
    first = run(ctx, result, Provider(replies=[{"status": "rate_limited"}, {"status": "rate_limited"}]), Client())
    assert first.status == "partial" and not result.sources
    ctx.previous_check = result.model_dump(mode="json")
    # This is execution.check_code_with_model's reuse_facts=False path: empty
    # saved sources cause fresh base checks rather than cloning the old result.
    fresh = check_code(ctx)
    provider, client = Provider(), Client()
    resumed = run(ctx, fresh, provider, client)
    assert resumed.audit["reused"] and resumed.status == fresh.discovery.status == "partial"
    assert provider.request_count == 0 and client.tasks == [] and fresh.external_request_count == 0
    assert len(fresh.discovery.queries) == 2
    assert all(query.status == "failed" for query in fresh.discovery.queries)
    assert first.warnings == resumed.warnings
    assert all(message in fresh.checks.scope_limitations for message in first.warnings)
    assert_closed(fresh)
    verify_report_snapshots({"sources": fresh.sources, "code_discovery": fresh.discovery})


def test_archived_report_survives_failed_base_source_refresh_without_repeating_paid_work(ctx, result):
    run(ctx, result, Provider(), Client())
    # An unrelated failed base source forces execution to re-read repository
    # facts, but must not discard already completed report supplementation.
    result.sources.append(CredibilityExternalSource(
        source_id="unrelated_failed_source", kind="metadata", url="https://example.org/failed",
        fetch_status="timeout", http_status=None, retrieved_at=STAMP, observed_at=STAMP,
        is_cached=False, snapshot_ref=None, association_reason="Unrelated earlier base read failed.",
    ))
    ctx.previous_check = result.model_dump(mode="json")
    fresh = check_code(ctx)
    fresh.external_request_count = 7  # Real reads spent rebuilding the base facts this turn.
    current_audit = capture_snapshot(kind="code_discovery_audit", normalized_json={"current_base_search": True})
    fresh.discovery.audit_ref = current_audit
    provider, client = Provider(), Client()
    resumed = run(ctx, fresh, provider, client)
    assert resumed.audit["reused"] and resumed.status == "completed"
    assert fresh.external_request_count == 7 and provider.request_count == 0 and not client.tasks
    assert len(fresh.discovery.candidates) == 1
    assert fresh.discovery.candidates[0] == result.discovery.candidates[0]
    assert all(source.source_id.startswith("reproduction_src_") for source in fresh.sources)
    assert "unrelated_failed_source" not in {source.source_id for source in fresh.sources}
    wrapper = read_snapshot(fresh.discovery.audit_ref).normalized_json
    assert wrapper["previous_discovery_audit_ref"] == current_audit
    assert wrapper["restored_reproduction_audit_ref"] == resumed.audit["reused_audit_ref"]
    assert_closed(fresh)
    verify_report_snapshots({"sources": fresh.sources, "code_discovery": fresh.discovery})


@pytest.mark.parametrize("kind", [
    "official_implementation", "implementation_candidate", "third_party_related", "reading_list",
])
def test_restoring_archived_report_cannot_overwrite_current_protected_discovery(ctx, result, kind):
    run(ctx, result, Provider(), Client())
    old_candidate = result.discovery.candidates[0]
    ctx.previous_check = result.model_dump(mode="json")
    fresh = check_code(ctx)
    protected = CredibilityCodeDiscoveryCandidate(
        candidate_id="current_protected", url=old_candidate.url, resource_kind=kind,
        reason="Current source-bound result.",
    )
    fresh.discovery.candidates.append(protected)
    resumed = run(ctx, fresh, Provider(), Client())
    assert resumed.audit["reused"] and fresh.discovery.candidates == [protected]
    assert protected.reason == "Current source-bound result."
    assert not fresh.evidence
    assert_closed(fresh)


def test_public_artifacts_contain_only_typed_labels_and_no_model_reasoning(ctx, result):
    checked = run(ctx, result, Provider(), Client())
    attempt = checked.audit["attempts"][0]
    labels = read_snapshot(attempt["labels_snapshot_ref"]).normalized_json
    assert set(labels) == {"candidates"}
    assert all(set(row) == {"candidate_id", "relation", "outcome", "span_ids"} for row in labels["candidates"])
    assert "private reasoning" not in json.dumps(labels)


def test_new_prompt_and_behavior_versions_are_part_of_request_fingerprint():
    from app.credibility.runtime import request_versions

    versions = request_versions()
    assert REPRODUCTION_VERSION in versions.values() and PROMPT_VERSION in versions.values()


def test_near_duplicate_snippets_of_one_candidate_become_one_evidence():
    from app.credibility.code_reproduction_discovery import _distinct_quotes

    short = {"text": "Reproduction of the paper. Includes pipeline.", "source_id": "a"}
    longer = {"text": "Reproduction of the paper.  Includes pipeline. Conducted at Inria...", "source_id": "b"}
    other = {"text": "README: deterministic dataset ordering for reproduction.", "source_id": "c"}
    assert _distinct_quotes([short, longer, other]) == [longer, other]
    assert _distinct_quotes([short, dict(short)]) == [short]
