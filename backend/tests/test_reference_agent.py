"""Bounded reference agent: the model proposes sources, the program verifies them offline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.credibility import code_discovery_provider, reference_agent, reference_arxiv, reference_publishers, references
from app.credibility.code_discovery_provider import DiscoveryProviderResult
from app.credibility.context import CheckContext
from app.credibility.coverage import build_reference_summary
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_sources import ReferenceFallback
from app.credibility.snapshots import capture_snapshot, read_snapshot, verify_report_snapshots
from app.llm.base import LLMToolCall, LLMToolResponse
from app.llm.retry import LLMTimeout
from app.parsing.base import ParsedDocument, TextBlock
from app.pipeline import orchestrator
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

STAMP = "2026-10-20T01:02:03Z"
SYSTEM_CARD = "https://openai.com/index/o3-mini-system-card/"
REFERENCES = """Jane Doe and John Smith. A reliable reference with independent source evidence. 2020.

OpenAI. o3-mini system card. 2025.

Wei Li and Ana Costa. Robust graph matching under noisy correspondences. 2024.

John Smith. Personal communication. 2021."""


class FakeClient:
    """Scripted native-tool turns: a list of (name, args) is one tool round, a string a final answer."""

    supports_native_tools = True
    model_version = "fake-tools-1"

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
        self.requests.append({"tool_choice": tool_choice, "messages": json.loads(json.dumps(messages))})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, list):
            calls = [LLMToolCall(f"call_{len(self.requests)}_{index}", name, json.dumps(args))
                     for index, (name, args) in enumerate(turn)]
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"id": call.call_id, "type": "function",
                 "function": {"name": call.name, "arguments": call.arguments}} for call in calls]}
            return LLMToolResponse("", calls, message, "model_standard_v1", self.model_version)
        return LLMToolResponse(turn, [], {"role": "assistant", "content": turn}, "model_standard_v1",
                               self.model_version)


class FakeProvider:
    def __init__(self, pages=None, hits=None, fetch_status="ok"):
        self.pages, self.hits, self.fetch_status = pages or {}, hits or [], fetch_status
        self.request_count = 0

    def search(self, query, count=5):
        self.request_count += 1
        ref = capture_snapshot(kind="code_discovery_search", normalized_json={"query": query, "hits": self.hits},
                               fetch_status="ok", http_status=200, retrieved_at=STAMP,
                               requested_url="https://api.deepseek.com/v1/tools/search")
        return DiscoveryProviderResult("search", "ok", 200, STAMP, "search_results", snapshot_ref=ref,
                                       query=query, hits=list(self.hits))

    def fetch(self, url):
        self.request_count += 1
        status = self.fetch_status if url in self.pages else "not_found"
        text = self.pages.get(url, "") if status == "ok" else ""
        ref = capture_snapshot(kind="code_discovery_fetch", normalized_text=text or None, requested_url=url,
                               fetch_status=status, http_status=200 if status == "ok" else None,
                               retrieved_at=STAMP)
        return DiscoveryProviderResult("fetch", status, 200 if status == "ok" else None, STAMP,
                                       "fetch_content" if status == "ok" else "provider_timeout",
                                       snapshot_ref=ref, url=url, title="Page", text=text)


def context(raw=REFERENCES, **options):
    options.setdefault("model_id", "model_standard_v1")
    return CheckContext(source={}, repo_root=Path("."), generated_at=STAMP, ref=None, unresolved_reason=None,
        doc=ParsedDocument(
            paper=Paper(paper_id="agent-reference", parse_quality=ParseQuality.GOOD), blocks=[TextBlock(
                text=raw, chunk_id="references-1", page_start=1, page_end=1, section_role="references",
                section_title="References")]), **options)


def final(*proposals, reason="Bounded search finished."):
    return json.dumps({"proposals": [
        {"reference_id": rid, "source_url": url, "quote": quote, "source_kind": kind, "reason": "found"}
        for rid, url, quote, kind in proposals], "reason": reason})


@pytest.fixture
def pipeline(monkeypatch):
    """Deterministic sources find nothing; the agent receives the injected client/provider."""
    settings = get_settings()
    monkeypatch.setattr(settings, "reference_multisource_enabled", True)
    monkeypatch.setattr(settings, "credibility_reference_agent_enabled", True)
    ref = capture_snapshot(kind="crossref_response", raw=b'{"message":{"items":[]}}',
                           normalized_json={"message": {"items": []}}, fetch_status="ok", http_status=200,
                           retrieved_at=STAMP, requested_url="https://api.crossref.org/works?query=test")
    empty = references._LookupResult("https://api.crossref.org/works?query=test", "ok", 200, STAMP, (), False, ref)
    lookup = references.CrossrefLookup()
    monkeypatch.setattr(lookup, "lookup", lambda *args, **kwargs: empty)
    monkeypatch.setattr(references, "_LOOKUP", lookup)
    monkeypatch.setattr(references, "resolve_versions", lambda *args, **kwargs: __import__(
        "app.credibility.reference_versions", fromlist=["VersionResolution"]).VersionResolution({}, []))
    monkeypatch.setattr(reference_arxiv, "resolve_arxiv", lambda *args, **kwargs: ReferenceFallback())
    monkeypatch.setattr(reference_publishers, "resolve_publishers", lambda *args, **kwargs: ReferenceFallback())
    state = {}

    def install(client, provider):
        state.update(client=client, provider=provider)
        monkeypatch.setattr(orchestrator, "build_llm_client", lambda model_id: client)
        monkeypatch.setattr(code_discovery_provider, "discovery_provider_for", lambda client, **kwargs: provider)
        return state

    return install


def item(result, reference_id):
    return next(row for row in result.checks.items if row.reference_id == reference_id)


SYSTEM_CARD_PAGE = ("# OpenAI o3-mini System Card\n\nPublished by OpenAI, January 31, 2025.\n\n"
                    "This system card describes the safety work for o3-mini.")


def test_agent_accepts_official_page_only_after_program_checks(pipeline):
    client = FakeClient([
        [("search_web", {"query": "OpenAI o3-mini system card"})],
        [("fetch_page", {"url": SYSTEM_CARD, "reference_id": "ref_002"})],
        final(("ref_002", SYSTEM_CARD, "OpenAI o3-mini System Card", "official_non_scholarly_page")),
    ])
    provider = FakeProvider(pages={SYSTEM_CARD: SYSTEM_CARD_PAGE},
                            hits=[{"url": SYSTEM_CARD, "title": "o3-mini System Card", "snippet": "OpenAI"}])
    pipeline(client, provider)
    result = references.check_references(context())

    assert [row.reference_id for row in result.checks.items if row.outcome == "matched"] == ["ref_002"]
    matched = item(result, "ref_002")
    assert matched.reason.startswith("[non_scholarly_official_page]")
    candidate = matched.candidates[-1]
    assert candidate.candidate_id == matched.selected_candidate_id and candidate.metadata.authors == ["OpenAI"]
    assert candidate.metadata.year == 2025
    evidence = next(e for e in result.evidence if e.evidence_id in candidate.evidence_ids)
    assert evidence.mode == "quote" and evidence.source_id == candidate.source_id
    assert not any("ref_002" in finding.item_ids for finding in result.findings)
    # Every agent source is an archived observation of the read page.
    source = next(s for s in result.sources if s.source_id == candidate.source_id)
    assert source.url == SYSTEM_CARD and read_snapshot(source.snapshot_ref).normalized_text == SYSTEM_CARD_PAGE
    verify_report_snapshots(result)
    # Personal communication and other items stay with the deterministic result; only two items were tried.
    task = json.loads(client.requests[0]["messages"][1]["content"])
    assert [row["reference_id"] for row in task["references"]] == ["ref_001", "ref_002", "ref_003"]
    assert item(result, "ref_004").reason.startswith("[personal_communication]")
    assert item(result, "ref_001").reason.endswith("Reference agent found no source within its bounded search.")
    assert [call.purpose for call in result.model_calls] == ["reference_source_agent"] * 3
    audit = read_snapshot(result.agent_audit_ref)
    assert audit.manifest["kind"] == "reference_agent_audit" and audit.normalized_json["status"] == "completed"
    tool_calls = audit.normalized_json["tool_calls"]
    assert [row["name"] for row in tool_calls] == ["search_web", "fetch_page"]
    assert all(read_snapshot(row["tool_snapshot_ref"]).normalized_json["output"] for row in tool_calls)
    assert [request["tool_choice"] for request in client.requests] == ["auto", "auto", "auto"]
    assert result.checks.matched_count == 1 and "Reference agent (completed) verified 1 of 3" in \
        result.dimension.summary


def test_scholarly_record_from_crossref_tool_is_accepted(pipeline, monkeypatch):
    record_ref = capture_snapshot(kind="crossref_response", normalized_json={"items": 1}, fetch_status="ok",
                                  http_status=200, retrieved_at=STAMP, requested_url="https://api.crossref.org/x")
    found = references._LookupResult("https://api.crossref.org/x", "ok", 200, STAMP, (
        CredibilityBibliographicMetadata(title="Robust Graph Matching under Noisy Correspondences",
                                         authors=["Wei Li", "Ana Costa"], year=2024, doi="10.1000/rgm",
                                         arxiv_id=None),), False, record_ref)
    seen = []
    monkeypatch.setattr(reference_agent, "_default_crossref", lambda metadata: seen.append(metadata) or found)
    client = FakeClient([
        [("crossref_search", {"reference_id": "ref_003"})],
        final(("ref_003", "https://doi.org/10.1000/rgm", "Robust Graph Matching under Noisy Correspondences",
               "scholarly_record")),
    ])
    pipeline(client, FakeProvider())
    result = references.check_references(context())
    matched = item(result, "ref_003")
    assert matched.outcome == "matched" and matched.reason.startswith("[agent_verified_source]")
    assert matched.candidates[-1].metadata.doi == "10.1000/rgm"
    assert matched.candidates[-1].metadata.authors == ["Wei Li", "Ana Costa"]
    assert seen[0].title == "Robust graph matching under noisy correspondences" and seen[0].doi is None
    # The model receives the exact record text the program later checks quotes against.
    record = json.loads(client.requests[1]["messages"][-1]["content"])["records"][0]
    assert record == {"url": "https://doi.org/10.1000/rgm", "text": (
        "Robust Graph Matching under Noisy Correspondences\nWei Li; Ana Costa\n2024\nDOI: 10.1000/rgm")}
    verify_report_snapshots(result)


@pytest.mark.parametrize(("page", "failure"), [
    ("# Some other system card\n\nPublished by OpenAI.", "title"),
    ("# OpenAI o3-mini system card mirror\n\nRepost by a fan blog.".replace("OpenAI ", ""), "author"),
])
def test_proposal_rejected_when_title_or_author_missing_from_page(pipeline, page, failure):
    client = FakeClient([
        [("search_web", {"query": "o3-mini system card"})],
        [("fetch_page", {"url": SYSTEM_CARD})],
        final(("ref_002", SYSTEM_CARD, page.splitlines()[0].lstrip("# "), "official_non_scholarly_page")),
    ])
    provider = FakeProvider(pages={SYSTEM_CARD: page}, hits=[{"url": SYSTEM_CARD, "title": "x", "snippet": ""}])
    pipeline(client, provider)
    result = references.check_references(context())
    rejected = item(result, "ref_002")
    assert rejected.outcome == "unverified"
    assert rejected.reason.startswith("[target_not_found_in_scope]")
    assert f"does not contain the cited {failure}" in rejected.reason
    # The attempt is recorded as an archived source, without changing check status or the reason code.
    source = next(s for s in result.sources if s.source_id == "src_ref_002_agent_1")
    assert source.source_id in rejected.source_ids and source.fetch_status == "ok"
    assert result.dimension.check_status == "completed" and result.checks.matched_count == 0
    summary = build_reference_summary(result.checks)
    assert {row.reason_code for row in summary.unresolved_items if row.reference_id == "ref_002"} == {
        "target_not_found_in_scope"}
    verify_report_snapshots(result)


def test_unread_or_invented_source_is_rejected_without_attaching_a_source(pipeline):
    invented = "https://openai.com/never-read"
    client = FakeClient([final(("ref_002", invented, "OpenAI o3-mini System Card", "official_non_scholarly_page"))])
    pipeline(client, FakeProvider())
    result = references.check_references(context())
    rejected = item(result, "ref_002")
    assert rejected.outcome == "unverified" and "but the source was not read in this run" in rejected.reason
    assert not any(s.source_id.endswith("_agent_1") for s in result.sources)


def test_provider_failure_keeps_item_unverified(pipeline):
    client = FakeClient([
        [("search_web", {"query": "o3-mini"})],
        [("fetch_page", {"url": SYSTEM_CARD, "reference_id": "ref_002"})],
        final(("ref_002", SYSTEM_CARD, "OpenAI o3-mini System Card", "official_non_scholarly_page")),
    ])
    provider = FakeProvider(pages={SYSTEM_CARD: SYSTEM_CARD_PAGE}, fetch_status="timeout",
                            hits=[{"url": SYSTEM_CARD, "title": "x", "snippet": ""}])
    pipeline(client, provider)
    result = references.check_references(context())
    rejected = item(result, "ref_002")
    assert rejected.outcome == "unverified" and "provider_timeout" in rejected.reason
    tool_output = json.loads(client.requests[2]["messages"][-1]["content"])
    assert tool_output["status"] == "timeout"
    assert result.dimension.check_status == "completed"


def test_model_failure_falls_back_to_deterministic_result_without_checkpoint(pipeline):
    saved = {}
    client = FakeClient([LLMTimeout("slow")])
    pipeline(client, FakeProvider())
    ctx = context(checkpoint_load=lambda stage, key: saved.get((stage, key)),
                  checkpoint_save=lambda stage, key, payload: saved.__setitem__((stage, key), payload))
    result = references.check_references(ctx)
    assert result.checks.matched_count == 0
    assert all("Reference agent did not complete (LLMTimeout)" in item(result, rid).reason
               for rid in ("ref_001", "ref_002", "ref_003"))
    assert not saved and len(result.model_calls) == 1


def test_round_and_tool_budgets_are_enforced():
    client = FakeClient([
        [("search_web", {"query": "one"}), ("search_web", {"query": "two"})],
        "not json",
    ])
    provider = FakeProvider()
    ctx = context()
    rows = [reference_agent.CredibilityReferenceItem(
        reference_id="ref_001", raw_text="OpenAI. o3-mini system card. 2025.", paper_evidence_ids=["ev"],
        parsed=CredibilityBibliographicMetadata(title="o3-mini system card", authors=["OpenAI"], year=2025,
                                                doi=None, arxiv_id=None),
        outcome="unverified", reason="[target_not_found_in_scope] x", source_ids=[], candidates=[],
        selected_candidate_id=None, score_rule_id=None, item_score=None)]
    run = reference_agent.run_reference_agent(ctx, rows, client=client, provider=provider, max_rounds=2,
                                              max_tool_calls=1)
    assert run.payload["status"] == "budget_exhausted"
    assert run.payload["decisions"][0]["code"] == "agent_budget_exhausted"
    assert provider.request_count == 1
    second = json.loads(client.requests[1]["messages"][-1]["content"])
    assert second == {"status": "error", "reason": "tool_budget_exhausted"}
    assert [request["tool_choice"] for request in client.requests] == ["auto", "none"]
    assert len(run.model_calls) == 2


def test_checkpoint_reuse_does_not_call_model_or_provider_again(pipeline):
    saved = {}

    def ctx():
        return context(checkpoint_load=lambda stage, key: saved.get((stage, key)),
                       checkpoint_save=lambda stage, key, payload: saved.__setitem__((stage, key), payload))

    client = FakeClient([
        [("search_web", {"query": "o3-mini"})],
        [("fetch_page", {"url": SYSTEM_CARD})],
        final(("ref_002", SYSTEM_CARD, "OpenAI o3-mini System Card", "official_non_scholarly_page")),
    ])
    provider = FakeProvider(pages={SYSTEM_CARD: SYSTEM_CARD_PAGE},
                            hits=[{"url": SYSTEM_CARD, "title": "x", "snippet": ""}])
    pipeline(client, provider)
    first = references.check_references(ctx())
    assert len(saved) == 1 and provider.request_count == 2

    class Forbidden(FakeClient):
        def complete_tools(self, *args, **kwargs):
            raise AssertionError("checkpointed agent must not call the model again")

    class NoProvider(FakeProvider):
        def search(self, *args, **kwargs):
            raise AssertionError("checkpointed agent must not search again")

        fetch = search

    pipeline(Forbidden([]), NoProvider())
    second = references.check_references(ctx())
    assert [row.model_dump() for row in second.checks.items] == [row.model_dump() for row in first.checks.items]
    assert [call.model_dump() for call in second.model_calls] == [call.model_dump() for call in first.model_calls]
    assert second.agent_audit_ref == first.agent_audit_ref
    verify_report_snapshots(second)


def test_agent_does_not_run_when_disabled_or_without_model(pipeline, monkeypatch):
    class Forbidden(FakeClient):
        def complete_tools(self, *args, **kwargs):
            raise AssertionError("agent must not run")

    pipeline(Forbidden([]), FakeProvider())
    assert references.check_references(context(model_id=None)).model_calls == []
    monkeypatch.setattr(get_settings(), "credibility_reference_agent_enabled", False)
    result = references.check_references(context())
    assert result.model_calls == [] and result.agent_audit_ref is None


def test_program_matching_rules():
    page = "Adam Dahlgren Lindström and Savitha Sam Abraham. CLEVR-Math: A dataset. Piotr Dollár; Wei Li."
    assert reference_agent.authors_on_page(["Adam Dahlgren Lindstr ¨om"], page) == ["Adam Dahlgren Lindstr ¨om"]
    assert reference_agent.authors_on_page(["Doll´ar"], page) == ["Doll´ar"]
    # Short surnames match whole words only, never fragments of other words.
    assert reference_agent.authors_on_page(["Yu Li"], "A clinical evaluation") == []
    assert reference_agent.title_on_page("CLEVR-Math: a dataset", page)
    assert not reference_agent.title_on_page("CLEVR-Math: a dataset for compositional reasoning", page)
    assert reference_agent.quote_on_page("CLEVR-Math:  A dataset.", page) == "quote"
    assert reference_agent.quote_on_page("CLEVR Math A dataset Piotr", page) == "paraphrase"
    assert reference_agent.quote_on_page("an invented sentence entirely", page) is None
    assert reference_agent.official_host("https://www.cdc.gov/nchs/nhanes/", [
        "Centers for Disease Control and Prevention"])
    assert reference_agent.official_host("https://openai.com/index/gpt-4v-system-card/", ["OpenAI"])
    assert reference_agent.official_host("https://github.com/JaidedAI/EasyOCR", ["JaidedAI"])
    assert not reference_agent.official_host("https://example-blog.com/openai-card", ["PitchBook Data"])
