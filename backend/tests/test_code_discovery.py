"""Actual bounded discovery orchestration with deterministic provider doubles."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.credibility.code_checks import check_code
from app.credibility.code_discovery import (
    DiscoverySession,
    _Judgments,
    discover_code,
    implementation_relation,
    paper_queries,
    safe_candidate_url,
)
from app.credibility.code_lookup import Fetch, now
from app.credibility.context import CheckContext
from app.credibility.snapshots import capture_snapshot, read_snapshot, verify_report_snapshots
from app.llm.base import LLMResponse, LLMToolCall, LLMToolResponse
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "A Reliable Method for Learning"
REPO = "https://github.com/alice/method"
API = "https://api.github.com/repos/alice/method"
SHA = "a" * 40
README = f"# {TITLE}\nOfficial implementation of {TITLE} (2401.12345).\nRun train.py."


@pytest.fixture
def ctx():
    return CheckContext(source={"type": "arxiv", "arxiv_id": "2401.12345v1"}, repo_root=Path("."),
        generated_at=now(), ref=None, unresolved_reason=None, model_id="model_standard_v1",
        doc=ParsedDocument(paper=Paper(paper_id="discovery_test", title=TITLE, arxiv_id="2401.12345v1",
                                      authors=["Alice Smith"], parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(chunk_id="body", section_id="body", section_title="Method",
                              section_role="body", page_start=1, page_end=1,
                              text="We evaluate our method in empirical experiments.")]))


class Provider:
    def __init__(self, *, status="ok", text=README, hits=None, **kwargs):
        self.status, self.text, self.hits = status, text, hits
        self.request_count = 0
        self.urls = []

    def result(self, operation, *, url=None, hits=(), text=""):
        self.request_count += 1
        ref = capture_snapshot(kind="discovery_provider", normalized_json={"hits": hits},
            normalized_text=text or None, requested_url=url or "https://api.deepseek.com/v1/tools/search",
            final_url=url, fetch_status=self.status, http_status=200 if self.status == "ok" else 429,
            retrieved_at=now())
        return SimpleNamespace(operation=operation, status=self.status, http_status=200 if self.status == "ok" else 429,
            retrieved_at=now(), snapshot_ref=ref, title=TITLE, text=text, hits=hits,
            reason_code="search_empty" if operation == "search" and not hits else self.status, truncated=False)

    def search(self, query, count=5):
        self.urls.append(query)
        hits = self.hits if self.hits is not None else [{"url": REPO, "title": TITLE, "snippet": README}]
        return self.result("search", hits=hits if self.status == "ok" else [])

    def fetch(self, url):
        self.urls.append(url)
        return self.result("fetch", url=url, text=self.text if self.status == "ok" else "")


class Client:
    supports_native_tools = True
    model_version = "deepseek_test"

    def __init__(self, kind="official_implementation", *, final_override=None):
        self.index, self.kind, self.final_override = 0, kind, final_override
        self.choices = []

    def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
        self.index += 1
        self.choices.append(tool_choice)
        task = json.loads(messages[1]["content"])
        calls, text = [], ""
        if self.index == 1:
            calls = [LLMToolCall(f"q{index}", "search_paper_code", json.dumps({"query_id": row["query_id"]}))
                     for index, row in enumerate(task["registered_queries"])]
        elif self.index == 2:
            registry = json.loads(messages[-1]["content"])["candidate_registry"]
            candidate = next(row for row in registry if row["url"] == REPO)
            calls = [LLMToolCall("page", "fetch_discovery_page",
                                 json.dumps({"candidate_id": candidate["candidate_id"]}))]
        else:
            observed = json.loads(messages[-1]["content"])
            candidate = next(row for row in observed["candidate_registry"] if row["url"] == REPO)
            text = json.dumps({"candidates": [{"candidate_id": candidate["candidate_id"],
                "resource_kind": self.kind, "reason": "Inspected actual page text.",
                "citations": [{"source_id": observed["source_id"],
                               "span_id": observed["citation_spans"][0]["span_id"]}]}],
                "reason": "Executed exact identity searches and read the matching candidate."})
            if self.final_override is not None:
                text = self.final_override
        assistant = {"role": "assistant", "content": text, "reasoning_content": "PRIVATE_REASONING"}
        if calls:
            assistant["tool_calls"] = [{"id": call.call_id, "type": "function", "function": {
                "name": call.name, "arguments": call.arguments}} for call in calls]
        return LLMToolResponse(text, calls, assistant, "model_standard_v1", self.model_version,
                               response_id=f"response-{self.index}")

    def complete_json(self, system, user, *, max_tokens):
        task = json.loads(user)
        repository = task["repositories"][0]
        source = next(row for row in task["external_documents"] if row["text"] == README)
        return LLMResponse(json.dumps({"repositories": [{"repository_id": repository["repository_id"],
            "association": "confirmed", "reason": "Explicit implementation and paper identity in pinned README.",
            "citations": [{"source_id": source["source_id"], "excerpt": README}]}], "discussions": []}),
            "model_standard_v1", self.model_version)


def test_no_pdf_links_runs_real_native_tool_loop_and_only_nominates(ctx):
    base = check_code(ctx)
    assert base.checks.repositories == [] and base.checks.code_availability == "not_found_in_scope"
    provider, client = Provider(), Client()
    run = discover_code(ctx, base, client, provider)
    assert run.result.discovery.status == "completed"
    assert len(run.model_calls) == 3
    assert len(run.intake_candidates) == 1
    assert run.result.checks.repositories == []
    candidate = next(row for row in run.result.discovery.candidates if row.url == REPO)
    assert candidate.status == "candidate" and candidate.repository_id is None
    assert run.result.discovery.budget.source_reads_used == provider.request_count == 4
    assert run.result.discovery.budget.tool_calls_used == 4
    assert base.sources == []  # no mutation of the producer's base facts
    verify_report_snapshots({"sources": run.result.sources, "code_discovery": run.result.discovery})
    for attempt in run.audit["attempts"]:
        archived = read_snapshot(attempt["input_snapshot_ref"]).normalized_json
        assert "PRIVATE_REASONING" not in json.dumps(archived)


def test_one_model_search_call_executes_registered_group_once(ctx):
    provider = Provider()
    session = DiscoverySession(ctx, check_code(ctx), provider, batch_queries=True)
    observed = session.execute("q", "search_paper_code", {"query_id": "discovery_query_1"})
    assert len(observed["additional_registered_queries"]) == 2
    assert session.summary.budget.queries_used == provider.request_count == 3
    assert session.summary.budget.tool_calls_used == 1
    assert session.execute("again", "search_paper_code", {"query_id": "discovery_query_2"})["repeated"]
    assert provider.request_count == 3


def test_final_quote_correction_has_reserved_round_without_extra_source_reads(ctx):
    class RepairClient(Client):
        def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
            if self.index == 3:
                self.index += 1
                self.choices.append(tool_choice)
                observed = next(json.loads(row["content"]) for row in reversed(messages)
                                if row["role"] == "tool" and "text" in json.loads(row["content"]))
                candidate = next(row for row in observed["candidate_registry"] if row["url"] == REPO)
                text = json.dumps({"candidates": [{"candidate_id": candidate["candidate_id"],
                    "resource_kind": "official_implementation", "reason": "Corrected own-page quote.",
                    "citations": [{"source_id": observed["source_id"],
                                   "span_id": observed["citation_spans"][0]["span_id"]}]}],
                    "reason": "Bounded corrected output."})
                return LLMToolResponse(text, [], {"role": "assistant", "content": text},
                                       "model_standard_v1", self.model_version, response_id="repaired")
            response = super().complete_tools(messages, tools, max_tokens=max_tokens, tool_choice=tool_choice)
            if self.index == 1:
                response.tool_calls = response.tool_calls[:1]
                response.assistant_message["tool_calls"] = response.assistant_message["tool_calls"][:1]
            elif self.index == 3:
                value = json.loads(response.text)
                value["candidates"][0]["citations"][0]["excerpt"] = "A fabricated declaration"
                response.text = json.dumps(value)
                response.assistant_message["content"] = response.text
            return response

    provider, client = Provider(), RepairClient()
    run = discover_code(ctx, check_code(ctx), client, provider)
    assert run.result.discovery.status == "completed" and len(run.intake_candidates) == 1
    assert client.choices == ["auto", "auto", "none", "none"]
    assert run.result.discovery.budget.model_rounds_used == 4
    assert run.result.discovery.budget.tool_calls_used == 2
    assert provider.request_count == 4
    assert run.audit["repair_validation_code"] == "json_schema_invalid"


def test_public_summary_does_not_repeat_model_claims_about_unread_resources(ctx):
    unverified = "All unread GitHub pages contain no implementation, and every library is unofficial."
    final = json.dumps({"candidates": [], "reason": unverified})
    run = discover_code(ctx, check_code(ctx), Client(final_override=final), Provider())
    assert run.result.discovery.status == "completed"
    assert unverified not in run.result.discovery.reason
    assert "registered URLs remain unread" in run.result.discovery.reason
    assert run.audit["model_rationale_unverified"] == unverified


@pytest.mark.parametrize("kind,text", [
    ("third_party_related", f"State-tracking benchmark, motivated by {TITLE}; this is our own benchmark code."),
    ("reading_list", f"Reading list\n- {TITLE}\n- Another paper"),
])
def test_reading_list_and_related_benchmark_never_enter_intake(ctx, kind, text):
    run = discover_code(ctx, check_code(ctx), Client(kind), Provider(text=text))
    assert run.intake_candidates == []
    candidate = next(row for row in run.result.discovery.candidates if row.url == REPO)
    assert candidate.status == "rejected" and candidate.resource_kind == kind


def test_explicit_third_party_reproduction_does_not_earn_official_code_points(ctx):
    text = f"Third-party reproduction of {TITLE} (2401.12345). This is independent code."
    run = discover_code(ctx, check_code(ctx), Client("third_party_reproduction"), Provider(text=text))
    assert run.intake_candidates == []
    candidate = next(row for row in run.result.discovery.candidates if row.url == REPO)
    assert candidate.resource_kind == "third_party_reproduction"
    assert run.result.checks.code_availability == "not_found_in_scope"


@pytest.mark.parametrize("kind", ["official_implementation", "implementation_candidate"])
def test_mentions_paper_and_distant_code_cannot_pass_implementation_guard(ctx, kind):
    text = "Official implementation of another paper.\n" + "Unrelated content. " * 60 + TITLE
    run = discover_code(ctx, check_code(ctx), Client(kind), Provider(text=text))
    assert run.intake_candidates == [] and run.result.discovery.status == "partial"
    assert all(row.status != "verified" for row in run.result.discovery.candidates)


def test_fabricated_quote_and_unauthorized_score_are_atomic_failures(ctx):
    for final in ('{"candidates":[],"reason":"done","score":10}', "not json"):
        run = discover_code(ctx, check_code(ctx), Client(final_override=final), Provider())
        assert run.result.discovery.status == "partial" and not run.intake_candidates
        assert len(run.result.sources) == 4


def test_unregistered_url_unknown_query_and_secret_urls_are_blocked(ctx):
    provider = Provider()
    session = DiscoverySession(ctx, check_code(ctx), provider)
    assert session.execute("x", "fetch_discovery_page", {"candidate_id": "https://evil.test"})["status"] == "error"
    assert session.execute("y", "search_paper_code", {"query_id": "invented"})["status"] == "error"
    assert session.execute("z", "search_paper_code", {"query_id": "discovery_query_1", "url": REPO})[
        "status"] == "error"
    assert provider.request_count == 0
    for url in ("http://github.com/alice/method", "https://user:secret@example.com", "https://127.0.0.1/",
                "https://10.1.1.1/", "https://example.com/?api_key=secret", "https://foo.local/",
                "https://github.com/alice/../method"):
        assert safe_candidate_url(url) is None


def test_successful_empty_search_and_rate_limit_have_different_status(ctx):
    session = DiscoverySession(ctx, check_code(ctx), Provider(hits=[]))
    assert session.execute("a", "search_paper_code", {"query_id": "discovery_query_1"})["status"] == "ok"
    assert session.summary.queries[0].status == "completed" and session.summary.queries[0].result_count == 0
    failed = DiscoverySession(ctx, check_code(ctx), Provider(status="rate_limited"))
    assert failed.execute("a", "search_paper_code", {"query_id": "discovery_query_1"})["status"] == "rate_limited"
    assert failed.summary.queries[0].status == "failed" and failed.summary.limitations


def test_duplicate_tool_and_page_reads_do_not_spend_provider_requests(ctx):
    provider = Provider()
    session = DiscoverySession(ctx, check_code(ctx), provider, max_calls=3)
    query = {"query_id": "discovery_query_1"}
    session.execute("a", "search_paper_code", query)
    assert session.execute("b", "search_paper_code", query)["repeated"]
    assert session.execute("b", "search_paper_code", query)["status"] == "error"
    assert session.execute("c", "search_paper_code", query)["reason"] == "tool_budget_exhausted"
    assert provider.request_count == 1


def test_query_identity_is_bounded_and_cannot_be_model_generated(ctx):
    assert len(paper_queries(ctx)) == 3
    assert all(len(row.query) <= 128 for row in paper_queries(ctx))
    assert "2401.12345" in paper_queries(ctx)[0].query
    ctx.doc.paper.title, ctx.doc.paper.arxiv_id, ctx.source = None, None, {}
    assert paper_queries(ctx) == []


def test_local_relation_does_not_join_two_citations(ctx):
    assert implementation_relation(ctx, [README])
    assert not implementation_relation(ctx, ["Official implementation of another method", TITLE])
    assert not implementation_relation(ctx, [f"Our code is inspired by {TITLE}"])


def test_candidate_fetch_link_registry_cannot_be_forged_by_final_answer(ctx):
    provider = Provider(text=f"# {TITLE}\nCode for {TITLE}: [Code]({REPO}/tree/paper/src)")
    session = DiscoverySession(ctx, check_code(ctx), provider)
    session.execute("s", "search_paper_code", {"query_id": "discovery_query_1"})
    parent = next(row for row in session.summary.candidates if row.url == REPO)
    observed = session.execute("f", "fetch_discovery_page", {"candidate_id": parent.candidate_id})
    linked = next(row for row in session.summary.candidates if row.url == REPO + "/tree/paper/src")
    assert observed["source_id"] in session.candidate_origins[linked.candidate_id]
    with pytest.raises(ValueError, match="unknown_discovery_judgment"):
        session.apply(_Judgments.model_validate({"candidates": [{"candidate_id": "forged-repo",
            "resource_kind": "implementation_candidate", "citations": [], "reason": "made up"}], "reason": "done"}))


def test_production_execution_searches_then_confirms_and_reads_fixed_code(ctx, monkeypatch):
    from app.config import get_settings
    from app.credibility.execution import check_code_with_model
    from app.credibility.item_scoring import score_code_result
    from app.credibility.rubric import load_credibility_rubric

    settings = get_settings()
    monkeypatch.setattr(settings, "credibility_code_model_enabled", True)
    monkeypatch.setattr(settings, "credibility_code_discovery_enabled", True)
    monkeypatch.setattr(settings, "credibility_code_tool_mode", "prefetch")
    monkeypatch.setattr(settings, "deepseek_api_key", "TEST_ONLY")
    client = Client()
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda _: client)
    monkeypatch.setattr("app.credibility.code_discovery_provider.DeepSeekCodeDiscoveryProvider", Provider)
    urls = []

    def lookup(url):
        urls.append(url)
        if url == API:
            data = {"full_name": "alice/method", "default_branch": "main", "private": False}
        elif "/commits/" in url:
            data = {"sha": SHA}
        elif "/readme" in url:
            data = {"encoding": "base64", "content": base64.b64encode(README.encode()).decode()}
        elif "/git/trees/" in url:
            data = {"tree": [{"type": "blob", "path": "train.py"}], "truncated": False}
        elif "/contents/" in url:
            data = {"encoding": "base64", "content": base64.b64encode(b"def train():\n    return 1\n").decode()}
        elif "/issues?" in url:
            data = []
        else:
            raise AssertionError(url)
        return Fetch(url, "ok", 200, now(), data=data)

    monkeypatch.setattr("app.credibility.code_checks.LOOKUP.get", lookup)
    result = check_code_with_model(ctx)
    assert result.discovery.status == "completed"
    assert result.checks.code_availability == "available"
    assert result.checks.repositories[0].association == "confirmed"
    assert result.checks.repositories[0].inspected_revision == SHA
    assert result.discovery.candidates[1].status == "verified"
    assert all("?ref=" + SHA in url for url in urls if "/readme" in url or "/contents/" in url)
    assert any("/contents/train.py" in url for url in urls)
    assert result.external_request_count == len(urls) + result.discovery.budget.source_reads_used <= 40
    assert len(result.model_calls) == 4
    assert score_code_result(result, rubric=load_credibility_rubric()).dimension.score == 7
    verify_report_snapshots({"sources": result.sources, "code_discovery": result.discovery})
    # Continuing saved, successful discovery facts must never repeat searches
    # or GitHub reads merely to assemble the same code result.
    ctx.previous_check = result.model_dump(mode="json")
    original_reads, original_turns = list(urls), client.index
    resumed = check_code_with_model(ctx)
    assert urls == original_reads and client.index == original_turns
    assert resumed.external_request_count == 0
    assert resumed.discovery.audit_ref == result.discovery.audit_ref
    assert resumed.external_review["base_facts_reused"] is True
    assert resumed.checks.code_availability == "available"
