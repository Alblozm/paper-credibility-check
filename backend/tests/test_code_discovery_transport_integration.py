"""Offline checks across the real discovery adapter and native tool loop."""

import json
from pathlib import Path

import httpx
import pytest

from app.credibility.code_checks import check_code
from app.credibility.code_discovery import DiscoverySession, _Judgments, discover_code
from app.credibility.code_discovery_provider import DeepSeekCodeDiscoveryProvider
from app.credibility.code_lookup import now
from app.credibility.context import CheckContext
from app.credibility.snapshots import read_snapshot, verify_report_snapshots
from app.llm.base import LLMToolCall, LLMToolResponse
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "A Reliable Method for Learning"
REPO = "https://github.com/alice/method"
TEXT = f"Official implementation of {TITLE} (2401.12345). Run train.py."


@pytest.fixture
def ctx():
    return CheckContext(source={"type": "arxiv", "arxiv_id": "2401.12345v1"}, repo_root=Path("."),
        generated_at=now(), ref=None, unresolved_reason=None, model_id="model_standard_v1",
        doc=ParsedDocument(paper=Paper(paper_id="discovery_transport", title=TITLE, arxiv_id="2401.12345v1",
            authors=["Alice Smith"], parse_quality=ParseQuality.GOOD), blocks=[TextBlock(
                chunk_id="body", section_id="body", section_title="Method", section_role="body",
                page_start=1, page_end=1, text="We evaluate our method in empirical experiments.")]))


def test_real_adapter_search_fetch_into_candidate_proof_has_portable_snapshot_closure(ctx):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": REPO, "title": TITLE,
                "snippet": "A search-only lead", "content": "Search content must never grant a quotation"}]})
        return httpx.Response(200, json={"title": TITLE, "content": TEXT})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekCodeDiscoveryProvider(api_key="offline-only", client=client,
                                                resolver=lambda _: ["93.184.216.34"])
        session = DiscoverySession(ctx, check_code(ctx), provider)
        searched = session.execute("q1", "search_paper_code", {"query_id": "discovery_query_1"})
        candidate = next(row for row in session.summary.candidates if row.url == REPO)
        with pytest.raises(ValueError, match="own_fetched_document"):
            session.apply(_Judgments.model_validate({"candidates": [{"candidate_id": candidate.candidate_id,
                "resource_kind": "implementation_candidate", "reason": "not evidence",
                "citations": [{"source_id": searched["source_id"], "excerpt": "A search-only lead"}]}],
                "reason": "not evidence"}))
        fetched = session.execute("f1", "fetch_discovery_page", {"candidate_id": candidate.candidate_id})
        intake = session.apply(_Judgments.model_validate({"candidates": [{"candidate_id": candidate.candidate_id,
            "resource_kind": "official_implementation", "reason": "Exact declaration read.",
            "citations": [{"source_id": fetched["source_id"], "excerpt": TEXT}]}], "reason": "bounded"}))
    assert len(intake) == 1 and candidate.status == "candidate" and candidate.repository_id is None
    assert provider.request_count == len(requests) == session.summary.budget.source_reads_used == 2
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)
    assert all(0 < value <= 20 for request in requests for value in request.extensions["timeout"].values())
    assert session.documents[fetched["source_id"]] == TEXT
    source = next(row for row in session.result.sources if row.source_id == fetched["source_id"])
    derived = read_snapshot(source.snapshot_ref)
    provider_snapshot = read_snapshot(derived.manifest["parent_ref"])
    assert provider_snapshot.normalized_json["body"] == TEXT
    assert provider_snapshot.manifest["metadata"]["origin_redirect_chain_verified"] is False
    verify_report_snapshots({"sources": session.result.sources, "code_discovery": session.summary})


def test_real_adapter_rate_limit_and_zero_budget_propagate_without_fabricated_content(ctx):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, text="private error body must never reach the model")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekCodeDiscoveryProvider(api_key="offline-only", client=client, max_requests=1,
                                                resolver=lambda _: ["93.184.216.34"])
        session = DiscoverySession(ctx, check_code(ctx), provider)
        first = session.execute("q1", "search_paper_code", {"query_id": "discovery_query_1"})
        second = session.execute("q2", "search_paper_code", {"query_id": "discovery_query_2"})
    assert first["status"] == "rate_limited" and second["reason"] == "request_budget_exhausted"
    assert len(requests) == provider.request_count == session.summary.budget.source_reads_used == 1
    assert [query.status for query in session.summary.queries[:2]] == ["failed", "failed"]
    assert not session.documents and session.summary.limitations
    assert all("private" not in json.dumps(read_snapshot(source.snapshot_ref).normalized_json)
               for source in session.result.sources)


def test_unread_registered_candidates_preserve_incomplete_discovery_status(ctx):
    class SearchOnlyClient:
        model_version = "offline-only"

        def __init__(self):
            self.index = 0

        def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
            self.index += 1
            calls = [LLMToolCall(f"q{i}", "search_paper_code", json.dumps({"query_id": f"discovery_query_{i}"}))
                     for i in (1, 2)] if self.index == 1 else []
            text = "" if calls else json.dumps({"candidates": [], "reason": "Searches complete."})
            assistant = {"role": "assistant", "content": text}
            if calls:
                assistant["tool_calls"] = [{"id": c.call_id, "type": "function", "function": {
                    "name": c.name, "arguments": c.arguments}} for c in calls]
            return LLMToolResponse(text, calls, assistant, "model_standard_v1", self.model_version)

    def handler(_):
        return httpx.Response(200, json={"results": [{"url": REPO, "title": TITLE, "snippet": "promising"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekCodeDiscoveryProvider(api_key="offline-only", client=client,
                                                resolver=lambda _: ["93.184.216.34"])
        run = discover_code(ctx, check_code(ctx), SearchOnlyClient(), provider)
    assert run.result.discovery.status == "partial"
    assert any("page" in item and "read" in item for item in run.result.discovery.limitations)
    assert run.result.discovery.budget.candidates_inspected == 0
    assert len(run.result.discovery.candidates) == 2 and not run.intake_candidates
