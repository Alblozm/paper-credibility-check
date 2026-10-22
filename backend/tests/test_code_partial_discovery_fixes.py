"""Bounded final-JSON/source-span repair and truthful unreadable content states."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.credibility.code_checks import check_code
from app.credibility.code_discovery import (
    DiscoverySession,
    DiscoveryValidationError,
    _Judgments,
    _ModelJudgments,
    discover_code,
)
from app.credibility.code_discovery_provider import DeepSeekCodeDiscoveryProvider, page_content_problem
from app.credibility.code_lookup import now
from app.credibility.context import CheckContext
from app.credibility.snapshots import capture_snapshot, read_snapshot
from app.llm.base import LLMToolCall, LLMToolResponse
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

TITLE = "A Reliable Method for Learning"
REPO = "https://github.com/alice/method"
TEXT = f"Official implementation of {TITLE} (2401.12345)." + r" \(A\&B\, 2026\)."
DSML = '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="fetch_discovery_page">not a tool call</｜｜DSML｜｜ calls>'


@pytest.fixture
def ctx():
    return CheckContext(source={"type": "arxiv", "arxiv_id": "2401.12345v1"}, repo_root=Path("."),
        generated_at=now(), ref=None, unresolved_reason=None, model_id="model_standard_v1",
        doc=ParsedDocument(paper=Paper(paper_id="partial_fixes", title=TITLE, arxiv_id="2401.12345v1",
            authors=["Alice Smith"], parse_quality=ParseQuality.GOOD), blocks=[TextBlock(
                chunk_id="body", section_id="body", section_title="Method", section_role="body",
                page_start=1, page_end=1, text="Empirical evaluation of our method.")]))


class Provider:
    def __init__(self, text=TEXT, status="ok"):
        self.text, self.status, self.request_count, self.urls = text, status, 0, []

    def observed(self, operation, *, url=None, hits=(), text=""):
        self.request_count += 1
        snapshot = capture_snapshot(kind=f"code_discovery_{operation}",
            normalized_json={"operation": operation, "url": url, "hits": hits, "text": text, "body": text},
            normalized_text=text, requested_url=url, fetch_status=self.status,
            http_status=200 if self.status == "ok" else 404, retrieved_at=now())
        return SimpleNamespace(status=self.status, text=text, title=TITLE, hits=hits, snapshot_ref=snapshot,
            http_status=200 if self.status == "ok" else 404, retrieved_at=now(), reason_code=self.status,
            truncated=False)

    def search(self, query, count=5):
        return self.observed("search", hits=[{"url": REPO, "title": TITLE, "snippet": "Lead only"}])

    def fetch(self, url):
        self.urls.append(url)
        return self.observed("fetch", url=url, text=self.text)


def fetched(ctx):
    provider = Provider()
    session = DiscoverySession(ctx, check_code(ctx), provider)
    candidate = session.register(REPO)
    observed = session.fetch(candidate.candidate_id)
    return session, candidate, observed


def judgment(candidate, source, *, span_id=None, excerpt=None, kind="official_implementation"):
    citation = {"source_id": source}
    if span_id is not None:
        citation["span_id"] = span_id
    if excerpt is not None:
        citation["excerpt"] = excerpt
    return _Judgments.model_validate({"candidates": [{"candidate_id": candidate,
        "resource_kind": kind, "reason": "Exact fetched declaration.", "citations": [citation]}],
        "reason": "Bounded source-based classification."})


def test_registered_span_server_copies_exact_escaped_source(ctx):
    session, candidate, page = fetched(ctx)
    span = page["citation_spans"][0]
    value = judgment(candidate.candidate_id, page["source_id"], span_id=span["span_id"])
    intake = session.apply(value)
    assert intake and value.candidates[0].citations[0].excerpt == TEXT
    assert session.result.evidence[-1].excerpt == TEXT
    assert session.citation_spans[span["span_id"]]["source_snapshot_ref"] == page["snapshot_ref"]


@pytest.mark.parametrize("attack", ["unknown_span", "wrong_source", "other_candidate", "changed_excerpt"])
def test_span_never_expands_source_or_candidate_permissions(ctx, attack):
    session, candidate, page = fetched(ctx)
    source, span = page["source_id"], page["citation_spans"][0]["span_id"]
    excerpt = None
    if attack == "unknown_span":
        span = "invented-span"
    elif attack == "wrong_source":
        source = "unfetched-source"
    elif attack == "other_candidate":
        candidate = session.register("https://github.com/alice/other")
    else:
        excerpt = TEXT.replace("Official", "Unofficial")
    with pytest.raises(DiscoveryValidationError) as error:
        session.apply(judgment(candidate.candidate_id, source, span_id=span, excerpt=excerpt))
    assert error.value.issue["candidate_id"] == candidate.candidate_id
    assert error.value.issue["source_id"] == source
    assert error.value.issue["span_id"] == span
    assert session.result.evidence == []


def test_span_does_not_waive_identity_or_allow_unauthorized_output_fields(ctx):
    session = DiscoverySession(ctx, check_code(ctx), Provider(text="Official implementation of a different paper."))
    candidate = session.register(REPO)
    page = session.fetch(candidate.candidate_id)
    with pytest.raises(ValueError, match="paper_identity_missing"):
        session.apply(judgment(candidate.candidate_id, page["source_id"], span_id=page["citation_spans"][0]["span_id"]))
    value = judgment(candidate.candidate_id, page["source_id"], excerpt=page["text"]).model_dump()
    value["score"] = 10
    with pytest.raises(ValueError):
        _Judgments.model_validate(value)


@pytest.mark.parametrize("use_span", [False, True])
def test_parent_official_declaration_cannot_be_borrowed_by_unread_dependency(ctx, use_span):
    dependency = "https://github.com/bob/dependency"
    provider = Provider(text=TEXT + f"\n\nDependency: [utility]({dependency})")
    session = DiscoverySession(ctx, check_code(ctx), provider)
    parent = session.register(REPO)
    page = session.fetch(parent.candidate_id)
    child = next(row for row in session.summary.candidates if row.url == dependency)
    assert page["source_id"] not in session.candidate_origins.get(child.candidate_id, set())
    options = {"span_id": page["citation_spans"][0]["span_id"]} if use_span else {"excerpt": TEXT}
    with pytest.raises(DiscoveryValidationError):
        session.apply(judgment(child.candidate_id, page["source_id"], **options))
    assert not session.result.evidence and not session.registry()[-1]["read"]


def test_project_code_link_can_support_candidate_but_quote_must_include_actual_link(ctx):
    text = f"Official implementation of {TITLE} (2401.12345). [Code]({REPO})"
    provider = Provider(text=text)
    session = DiscoverySession(ctx, check_code(ctx), provider)
    project = session.register("https://example.org/project")
    page = session.fetch(project.candidate_id)
    child = next(row for row in session.summary.candidates if row.url == REPO)
    assert page["source_id"] in session.candidate_origins[child.candidate_id]
    with pytest.raises(DiscoveryValidationError, match="parent_quote_lacks_target_binding"):
        session.apply(judgment(child.candidate_id, page["source_id"],
                               excerpt=f"Official implementation of {TITLE} (2401.12345)."))
    assert session.apply(judgment(child.candidate_id, page["source_id"],
                                 span_id=page["citation_spans"][0]["span_id"]))


class Client:
    model_version = "offline-only"

    def __init__(self, *, protocol_error=False, bad_final=False):
        self.round, self.choices = 0, []
        self.protocol_error, self.bad_final = protocol_error, bad_final

    def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
        self.round += 1
        self.choices.append(tool_choice)
        calls, text = [], ""
        if self.round == 1:
            calls = [LLMToolCall("search", "search_paper_code", '{"query_id":"discovery_query_1"}')]
        elif self.round == 2:
            registry = json.loads(messages[-1]["content"])["candidate_registry"]
            candidate = next(row for row in registry if row["url"] == REPO)
            calls = [LLMToolCall("page", "fetch_discovery_page",
                                 json.dumps({"candidate_id": candidate["candidate_id"]}))]
        elif self.round == 3 and self.protocol_error:
            text = DSML
        else:
            observed = next(json.loads(row["content"]) for row in reversed(messages)
                            if row["role"] == "tool" and "citation_spans" in json.loads(row["content"]))
            candidate = next(row for row in observed["candidate_registry"] if row["url"] == REPO)
            if self.bad_final:
                value = judgment(candidate["candidate_id"], observed["source_id"], excerpt="Invented 99% accuracy")
            else:
                value = judgment(candidate["candidate_id"], observed["source_id"],
                                 span_id=observed["citation_spans"][0]["span_id"])
            text = value.model_dump_json(exclude_none=True)
        assistant = {"role": "assistant", "content": text}
        if calls:
            assistant["tool_calls"] = [{"id": c.call_id, "type": "function", "function": {
                "name": c.name, "arguments": c.arguments}} for c in calls]
        return LLMToolResponse(text, calls, assistant, "model_standard_v1", self.model_version)


def test_protocol_text_repairs_with_registered_span_within_same_four_round_budget(ctx):
    provider, client = Provider(), Client(protocol_error=True)
    run = discover_code(ctx, check_code(ctx), client, provider)
    assert run.result.discovery.status == "completed" and len(run.intake_candidates) == 1
    assert client.choices == ["auto", "auto", "none", "none"]
    assert provider.request_count == 4 and len(run.model_calls) == 4
    assert run.audit["repair_validation_code"] == "provider_protocol_text_in_final_output"
    assert len(run.audit["output_snapshot_refs"]) == 2
    raw = read_snapshot(run.audit["raw_output_snapshot_ref"])
    validated = read_snapshot(run.audit["output_snapshot_ref"])
    assert raw.normalized_text != validated.normalized_text
    assert validated.manifest["parent_ref"] == run.audit["raw_output_snapshot_ref"]
    assert validated.normalized_json["candidates"][0]["citations"][0]["excerpt"] == TEXT
    registry = read_snapshot(run.audit["citation_spans_snapshot_ref"]).normalized_json["spans"]
    assert len(registry) == 1


def test_unfixed_quote_after_protocol_repair_remains_partial_with_specific_diagnostic(ctx):
    run = discover_code(ctx, check_code(ctx), Client(protocol_error=True, bad_final=True), Provider())
    assert run.result.discovery.status == "partial" and run.intake_candidates == []
    assert run.audit["attempt_count"] == 4 and len(run.audit["output_snapshot_refs"]) == 2
    assert run.audit["validation_code"] == "json_schema_invalid"
    assert any(row["location"][-1] == "span_id" for row in run.audit["validation_issue"]["fields"])
    assert "json_schema_invalid" in run.result.discovery.reason


@pytest.mark.parametrize("citation", [
    {"source_id": "fetched", "excerpt": TEXT},
    {"source_id": "fetched", "span_id": "registered", "excerpt": TEXT},
])
def test_new_model_schema_forbids_free_quotes_but_legacy_archive_parser_remains(citation):
    value = {"candidates": [{"candidate_id": "candidate", "resource_kind": "official_implementation",
        "reason": "Fixture declaration.", "citations": [citation]}], "reason": "Bounded review."}
    assert _Judgments.model_validate(value).candidates[0].citations[0].excerpt == TEXT
    with pytest.raises(ValueError):
        _ModelJudgments.model_validate(value)
    schema = _ModelJudgments.model_json_schema()["$defs"]["_ModelCitation"]
    assert set(schema["properties"]) == {"source_id", "span_id"}
    assert set(schema["required"]) == {"source_id", "span_id"}
    assert schema["additionalProperties"] is False


def test_tool_and_repair_expose_only_registered_candidate_span_permissions(ctx):
    class RegistryClient(Client):
        def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
            if self.round >= 2:
                observed = next(json.loads(row["content"]) for row in reversed(messages)
                                if row["role"] == "tool" and "citation_spans" in json.loads(row["content"]))
                self.registry = observed["candidate_citation_registry"]
                candidate = next(row for row in self.registry if row["url"] == REPO)
                assert candidate["allowed_citations"] == [{"source_id": observed["source_id"],
                    "span_ids": [observed["citation_spans"][0]["span_id"]]}]
                seed = next(row for row in self.registry if row["url"].startswith("https://arxiv.org/abs/"))
                assert seed["allowed_citations"] == []
            if self.round == 3:
                repair = messages[-1]["content"]
                assert "excerpt is forbidden" in repair
                assert json.loads(repair.split("candidate_citation_registry: ", 1)[1]) == self.registry
            return super().complete_tools(messages, tools, max_tokens=max_tokens, tool_choice=tool_choice)

    provider, client = Provider(), RegistryClient(protocol_error=True)
    run = discover_code(ctx, check_code(ctx), client, provider)
    assert run.result.discovery.status == "completed" and run.intake_candidates
    assert run.audit["model_citation_protocol"] == "registered_spans_only"
    assert provider.request_count == 4 and run.audit["attempt_count"] == 4


def test_parent_registry_lists_only_locally_bound_code_spans(ctx):
    declaration = f"Official implementation of {TITLE} (2401.12345). [Code]({REPO})"
    provider = Provider(text=declaration + "\n\nOther paragraph about unrelated systems.")
    session = DiscoverySession(ctx, check_code(ctx), provider)
    parent = session.register("https://example.org/project")
    observed = session.fetch(parent.candidate_id)
    child = next(row for row in session.summary.candidates if row.url == REPO)
    dependency = session.register("https://github.com/bob/dependency", source_ids=[observed["source_id"]],
                                  parent_source=observed["source_id"])
    registry = {row["candidate_id"]: row for row in session.citation_registry()}
    assert registry[dependency.candidate_id]["allowed_citations"] == []
    allowed = registry[child.candidate_id]["allowed_citations"]
    assert len(allowed) == 1
    assert allowed[0]["source_id"] == observed["source_id"]
    assert allowed[0]["span_ids"] == [observed["citation_spans"][0]["span_id"]]
    assert len(registry[parent.candidate_id]["allowed_citations"][0]["span_ids"]) == 2


def test_unread_candidate_can_stay_undetermined_without_fabricated_negative_quote(ctx):
    session = DiscoverySession(ctx, check_code(ctx), Provider())
    candidate = session.register("https://github.com/reader/notes")
    model = _ModelJudgments.model_validate({"reason": "This URL was not read in the bounded search.",
        "candidates": [{"candidate_id": candidate.candidate_id, "resource_kind": "undetermined",
                        "reason": "No fetched content.", "citations": []}]})
    assert session.apply(_Judgments.model_validate(model.model_dump(mode="json"))) == []
    assert candidate.resource_kind == "undetermined" and candidate.evidence_ids == []
    assert session.citation_registry()[-1]["allowed_citations"] == []


@pytest.mark.parametrize("valid_span", [False, True])
def test_mixed_final_after_dsml_cannot_borrow_search_metadata_even_with_valid_other_spans(ctx, valid_span):
    class MixedClient(Client):
        def complete_tools(self, messages, tools, *, max_tokens, tool_choice):
            response = super().complete_tools(messages, tools, max_tokens=max_tokens, tool_choice=tool_choice)
            if self.round != 4:
                return response
            value = json.loads(response.text)
            searched = next(json.loads(row["content"]) for row in messages
                            if row["role"] == "tool" and "results" in json.loads(row["content"]))
            observed = next(json.loads(row["content"]) for row in reversed(messages)
                            if row["role"] == "tool" and "citation_spans" in json.loads(row["content"]))
            seed = next(row for row in observed["candidate_registry"] if row["url"].startswith("https://arxiv.org/"))
            citation = {"source_id": searched["source_id"]}
            if valid_span:
                citation["span_id"] = observed["citation_spans"][0]["span_id"]
            else:
                citation["excerpt"] = '"A paper title from a search snippet"'
            value["candidates"].append({"candidate_id": seed["candidate_id"], "resource_kind": "reading_list",
                "reason": "Unfetched lead misclassified from search metadata.", "citations": [citation]})
            response.text = json.dumps(value)
            response.assistant_message["content"] = response.text
            return response

    provider, client = Provider(), MixedClient(protocol_error=True)
    run = discover_code(ctx, check_code(ctx), client, provider)
    assert run.result.discovery.status == "partial" and run.intake_candidates == []
    assert run.audit["attempt_count"] == len(run.model_calls) == 4
    assert provider.request_count == 4 and len(run.audit["output_snapshot_refs"]) == 2
    assert run.audit["validation_code"] == ("discovery_span_outside_fetched_candidate"
                                            if valid_span else "json_schema_invalid")
    assert not run.result.evidence  # A valid earlier candidate cannot partly admit the batch.


@pytest.mark.parametrize("title,text,reason", [
    ("OpenReview", "Verifying your browser\nComplete the check below to continue.", "content_browser_verification"),
    ("Just a moment...", "Enable JavaScript to complete the security check.", "content_browser_verification"),
    ("Login", "Sign in to OpenReview\nEmail\nPassword\nSign in to continue", "content_login_required"),
    ("CAPTCHA", "Human verification\nComplete the CAPTCHA below.", "content_captcha"),
])
def test_transport_success_is_preserved_when_content_is_not_readable(ctx, title, text, reason):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"title": title, "content": text}))) as client:
        provider = DeepSeekCodeDiscoveryProvider(api_key="offline-only", client=client,
                                                resolver=lambda _: ["93.184.216.34"])
        session = DiscoverySession(ctx, check_code(ctx), provider)
        candidate = session.register("https://openreview.net/forum?id=paper")
        observed = session.fetch(candidate.candidate_id)
    assert observed["status"] == "access_denied" and observed["transport_status"] == "ok"
    assert observed["provider_http_status"] == 200 and observed["reason"] == reason
    assert observed["text"] == "" and observed["citation_spans"] == [] and not session.documents
    source = session.result.sources[-1]
    assert source.fetch_status == "access_denied" and source.http_status == 200
    child = read_snapshot(source.snapshot_ref)
    parent = read_snapshot(child.manifest["parent_ref"])
    assert parent.manifest["fetch_status"] == "ok" and parent.manifest["http_status"] == 200
    assert parent.normalized_json["content_status"] == "blocked"
    assert child.manifest["metadata"]["transport_fetch_status"] == "ok"
    assert child.manifest["metadata"]["origin_http_status"] is None
    assert any(reason in item for item in session.summary.limitations)
    assert not session.registry()[-1]["read"]


@pytest.mark.parametrize("text", [
    "# A Study of Browser Verification\nWe discuss CAPTCHA methods in a complete research article.",
    "# Repository\nCode for a paper. Sign in to GitHub appears in the navigation.",
    "# OpenReview\nA real paper abstract, authors and method. Our login model has a password field.",
])
def test_normal_content_mentioning_login_or_captcha_is_not_blocked(text):
    assert page_content_problem("Research paper", text) is None


def test_pdf_candidates_are_not_fetched_and_remain_unread(ctx):
    session = DiscoverySession(ctx, check_code(ctx), Provider())
    candidate = session.register("https://proceedings.example/paper.pdf", title=TITLE)
    observed = session.execute("pdf", "fetch_discovery_page", {"candidate_id": candidate.candidate_id})
    assert observed["status"] == "not_run" and observed["reason"] == "pdf_document_not_fetched_by_code_discovery"
    assert session.provider.request_count == session.fetches == 0
    assert session.summary.budget.tool_calls_used == 1 and session.result.sources == []
    assert not session.registry()[-1]["read"] and len(session.skipped_reads) == 1


def test_matching_read_lead_skips_metadata_without_erasing_previous_failed_read(ctx):
    provider = Provider(status="not_found")
    session = DiscoverySession(ctx, check_code(ctx), provider)
    failed = session.register("https://example.org/failed")
    session.fetch(failed.candidate_id)
    provider.status = "ok"
    repo = session.register(REPO)
    session.fetch(repo.candidate_id)
    mirror = session.register("https://example.org/abstract")
    result = session.fetch(mirror.candidate_id)
    assert result["status"] == "not_run" and result["reason"] == "implementation_lead_ready_for_fixed_revision_check"
    assert provider.request_count == 2 and session.fetches == 2
    assert session.result.sources[0].fetch_status == "not_found" and session.summary.limitations
    assert not session.registry()[-1]["read"]
