"""Bounded target reads and shared-paper guards; no external model or network."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.credibility.code_lookup import Fetch
from app.credibility.code_model import enrich_code_checks
from app.credibility.context import CheckContext
from app.credibility.external_tools import ExternalToolSession
from app.credibility.models import CodeCheckResult, CredibilityDiscussion, CredibilityExternalSource
from app.credibility.snapshots import capture_snapshot, read_snapshot
from app.llm.base import LLMToolResponse
from app.parsing.base import ParsedDocument
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

API = "https://api.github.com/repos/owner/shared"
TITLE = "Ctrl-World: A Controllable Generative World Model for Robot Manipulation"
SHA = "a" * 40


@pytest.fixture
def ctx():
    return CheckContext(source={"type": "arxiv", "arxiv_id": "2510.10125"}, repo_root=Path("."),
                        generated_at="2026-10-19T00:00:00Z", ref=None, unresolved_reason=None,
                        doc=ParsedDocument(paper=Paper(paper_id="paper", title=TITLE, arxiv_id="2510.10125",
                                                      parse_quality=ParseQuality.GOOD)))


@pytest.fixture
def result():
    source = CredibilityExternalSource(source_id="repo_source", kind="repository", url=API, fetch_status="ok",
                                      http_status=200, snapshot_ref=None,
                                      retrieved_at="2026-10-19T00:00:00Z", observed_at="2026-10-19T00:00:00Z",
                                      is_cached=False, association_reason="Candidate")
    return CodeCheckResult.model_validate({"dimension": {"dimension_id": "code_reproduction",
        "applicability": "applicable", "applicability_reason": "Empirical contribution",
        "check_status": "completed", "score_status": "unable_to_assess", "score": None, "weight": 1,
        "score_rule_ids": [], "summary": "Pending", "unassessed_reason": None, "evidence_ids": [],
        "finding_ids": []}, "checks": {"scope_complete": False, "scope_limitations": [], "searches": [],
        "code_availability": "unable_to_verify", "reproduction_signal": "unable_to_verify", "repositories": [{
        "repository_id": "repo", "source_id": "repo_source", "association": "uncertain",
        "association_reason": "Candidate", "association_evidence_ids": [], "availability": "accessible",
        "implementation_evidence_ids": [], "inspected_revision": SHA}], "discussions": [],
        "base_score_rule_id": None, "score_rule_id": None}, "sources": [source], "evidence": [], "findings": []})


def add_readme(result, text):
    url = API + "/readme?ref=" + SHA
    ref = capture_snapshot(kind="repository", normalized_json={"encoding": "base64",
        "content": base64.b64encode(text.encode()).decode()}, requested_url=url)
    source = result.sources[0].model_copy(update={"source_id": "readme", "url": url, "snapshot_ref": ref})
    result.sources.append(source)
    result.followup_state["repo"] = {"repository": "owner/shared", "readme_source_id": "readme"}
    return source


def add_thread(result, body, *, number=23, data=None):
    result.checks.repositories[0].association = "confirmed"
    url = f"https://github.com/owner/shared/issues/{number}"
    ref = capture_snapshot(kind="issue", normalized_json=data if data is not None else [
        {"html_url": url, "title": "Question", "body": body, "comments": 0, "state": "open"}])
    source = result.sources[0].model_copy(update={"source_id": "thread", "kind": "issue", "url": url,
                                                "snapshot_ref": ref})
    result.sources.append(source)
    result.checks.discussions.append(CredibilityDiscussion(discussion_id="discussion", repository_id="repo",
        source_ids=["thread"], evidence_ids=[], signal="discussion_only", relevance="undetermined",
        independence="unknown", conditions="Unknown", resolution="unknown", author_response_source_ids=[],
        scoring_eligible=False, reason="Pending"))
    return source


class Client:
    supports_native_tools, model_id, model_version = True, "test", "test"

    def __init__(self, *, association=None, quote="", source="readme", relevance="relevant",
                 resolution="unknown", conditions="Settings not established"):
        self.association, self.quote, self.source = association, quote, source
        self.relevance, self.resolution, self.conditions = relevance, resolution, conditions
        self.tasks = []

    def complete_tools(self, messages, tools, **kwargs):
        self.tasks.append(json.loads(messages[1]["content"]))
        citations = [{"source_id": self.source, "excerpt": self.quote}] if self.quote else []
        output = {"repositories": [], "discussions": []}
        if self.association is not None:
            output["repositories"] = [{"repository_id": "repo", "association": self.association,
                "reason": "The pinned README identifies the implementation project.", "citations": citations}]
        else:
            output["discussions"] = [{"discussion_id": "discussion", "signal": "discussion_only",
                "relevance": self.relevance, "resolution": self.resolution, "conditions": self.conditions,
                "reason": "This is directly relevant to Ctrl-World and the problem is resolved.",
                "citations": citations}]
        raw = json.dumps(output)
        return LLMToolResponse(raw, [], {"role": "assistant", "content": raw}, "test", "test")


def no_get(_url):
    raise AssertionError("No network/provider operation expected")


@pytest.mark.parametrize("body", [
    "After reading your paper, we noticed that you used EEF for training on Droid.",
    "How was the evaluation data used in Table 1 and Table 2 of the paper obtained?",
])
def test_shared_generic_paper_or_table_reference_does_not_keep_affirmative_reason(ctx, result, body):
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2510.10125", "2602.12063"]}
    client = Client(quote=body, source="thread", conditions="Ctrl-World training has been reproduced")
    output = enrich_code_checks(ctx, result, client, mode="native_tools")
    discussion = output.checks.checks.discussions[0]
    assert output.status == "completed" and discussion.relevance == "undetermined"
    assert "directly relevant" not in discussion.reason and "problem is resolved" not in discussion.reason
    assert "Ctrl-World training" not in discussion.conditions
    assert not discussion.scoring_eligible
    assert client.tasks[0]["discussions"][0]["repository_supported_paper_ids"] == ["2510.10125", "2602.12063"]
    audit = output.audit["guard_original_judgments"][0]
    assert audit["before"]["relevance"] == "relevant" and audit["after"]["relevance"] == "undetermined"
    assert read_snapshot(output.audit["output_snapshot_ref"]).normalized_json["discussions"][0][
        "reason"] == discussion.reason.removeprefix("Model-assisted public-report interpretation requiring review: ")


def test_shared_explicit_current_paper_id_still_supports_relevance(ctx, result):
    body = "Question about the model in arXiv 2510.10125."
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2510.10125", "2602.12063"]}
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread"), mode="native_tools")
    assert output.checks.checks.discussions[0].relevance == "relevant"
    assert not output.audit["semantic_guards"]


def test_guarded_unknown_relevance_does_not_keep_stale_positive_feedback_summary(ctx, result):
    body = "A question about your paper."
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2510.10125", "2602.12063"]}
    result.checks.reproduction_signal = "failure_reported"
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread"), mode="native_tools")
    assert output.checks.checks.discussions[0].relevance == "undetermined"
    assert output.checks.checks.reproduction_signal == "unable_to_verify"


@pytest.mark.parametrize("name", ["Ctrl-World", "CtrlWorld"])
def test_pdf_thread_explicit_distinctive_model_name_identifies_current_paper(ctx, result, name):
    ctx.doc.paper.arxiv_id = None
    ctx.source = {"type": "pdf"}
    body = f"Can the {name} model be used on another robotic arm?"
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2510.10125", "2602.12063"]}
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread"), mode="native_tools")
    assert output.checks.checks.discussions[0].relevance == "relevant"


def test_related_model_name_containing_paper_alias_does_not_establish_identity(ctx, result):
    ctx.doc.paper.title = "LoRA: Low-Rank Adaptation of Large Language Models"
    ctx.doc.paper.arxiv_id = None
    ctx.source = {"type": "pdf"}
    body = "I have a question about AdaLoRA."
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2106.09685", "2303.10512"]}
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread"), mode="native_tools")
    assert output.checks.checks.discussions[0].relevance == "undetermined"


@pytest.mark.parametrize("prefix", ["Introduction", "Analysis", "On", "A General", "A-General"])
def test_ordinary_title_prefix_is_not_a_unique_shared_repository_identity(ctx, result, prefix):
    ctx.doc.paper.title = prefix + ": A Report about Robot Learning"
    ctx.doc.paper.arxiv_id = None
    ctx.source = {"type": "pdf"}
    body = "My question is about " + prefix
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2510.10125", "2602.12063"]}
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread"), mode="native_tools")
    assert output.checks.checks.discussions[0].relevance == "undetermined"


def test_resolution_guard_rewrites_reason_and_conditions_but_keeps_original_audit(ctx, result):
    body = "Could you provide an evaluation script?"
    add_thread(result, body)
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread", resolution="resolved",
        conditions="The problem is resolved by the author."), mode="native_tools")
    discussion = output.checks.checks.discussions[0]
    assert discussion.resolution == "unknown" and "problem is resolved" not in discussion.reason
    assert "resolved by the author" not in discussion.conditions
    assert output.audit["guard_original_judgments"][0]["before"]["resolution"] == "resolved"


def test_shared_guard_cannot_hide_unsupported_condition_numbers(ctx, result):
    body = "I have a question about your paper."
    add_thread(result, body)
    result.followup_state["repo"] = {"shared_paper_ids": ["2510.10125", "2602.12063"]}
    output = enrich_code_checks(ctx, result, Client(quote=body, source="thread", conditions="accuracy 99.99"),
                                mode="native_tools", max_rounds=1)
    assert output.status == "partial" and not output.audit.get("output_snapshot_ref")
    assert output.audit["validation_issue"]["code"] == "SEMANTIC_CONDITIONS_NUMBER_UNSUPPORTED"


def test_rejected_long_dependency_is_bounded_without_pretending_full_read(ctx, result):
    statement = "Official implementation of Another Method."
    source = add_readme(result, "# A different project\n" + statement + "\n" + "details " * 4000)
    output = enrich_code_checks(ctx, result, Client(association="rejected", quote=statement), mode="native_tools")
    assert output.status == "completed" and output.checks.checks.repositories[0].association == "rejected"
    coverage = output.audit["source_read_coverage"][source.source_id]
    assert not coverage["text_complete"] and not coverage["required_for_final_plan"]
    assert "remainder not read" in coverage["purpose"]
    assert output.audit["external_request_count"] == 0
    assert any("Text budget truncated" in w for w in output.warnings)


@pytest.mark.parametrize("association", ["confirmed", "uncertain"])
def test_long_required_repository_source_remains_partial(ctx, result, association):
    add_readme(result, TITLE + "\n" + "details " * 4000)
    client = Client(association=association, quote=TITLE if association == "confirmed" else "")
    output = enrich_code_checks(ctx, result, client, mode="native_tools")
    assert output.status == "partial"
    assert output.audit["source_read_coverage"]["readme"]["required_for_final_plan"]


def test_rejected_source_with_hidden_current_paper_identity_keeps_partial_and_local_ranges(ctx, result):
    add_readme(result, "# Another Method\n" + "details " * 4000 + "\nSupported paper: 2510.10125\n")
    client = Client(association="rejected", quote="Another Method")
    output = enrich_code_checks(ctx, result, client, mode="native_tools")
    assert output.status == "partial"
    doc = next(d for d in client.tasks[0]["external_documents"] if d["source_id"] == "readme")
    assert "2510.10125" in doc["text"]
    coverage = output.audit["source_read_coverage"]["readme"]
    assert len(coverage["retained_ranges"]) == 2 and not coverage["text_complete"]


def test_long_required_discussion_remains_partial(ctx, result):
    body = "Question about the paper. " + "discussion " * 2000
    add_thread(result, body)
    output = enrich_code_checks(ctx, result, Client(quote="Question about the paper.", source="thread"),
                                mode="native_tools")
    assert output.status == "partial" and output.audit["source_read_coverage"]["thread"]["required_for_final_plan"]


@pytest.mark.parametrize("rows", [[], [{"html_url": "https://github.com/owner/shared/issues/4", "body": "other"}],
    [{"html_url": "https://github.com/owner/shared/issues/23", "body": "duplicate"}] * 2])
def test_specific_issue_source_never_substitutes_missing_or_duplicate_listing_message(ctx, result, rows):
    src = add_thread(result, "", data=rows)
    session = ExternalToolSession(ctx, result)
    assert session.existing_document(src) == ""
    assert session.document_errors["thread"] == "original_message_not_unique"
    output = enrich_code_checks(ctx, result, Client(quote="", source="thread", relevance="undetermined"),
                                mode="native_tools")
    assert output.status == "partial" and output.audit["source_read_coverage"]["thread"]["error"]


@pytest.mark.parametrize("rows,valid", [([], True),
    ([{"html_url": "https://github.com/OWNER/shared/issues/23#issuecomment-2", "body": "same thread"}], True),
    ([{"html_url": "https://github.com/owner/shared/issues/24#issuecomment-2", "body": "other thread"}], False),
    ([{"body": "missing message URL"}], False),
    ([{"html_url": "https://github.com/owner/shared/issues/23#issuecomment-2", "body": "duplicate"}] * 2, False)])
def test_native_comment_list_is_bound_to_its_requested_thread(ctx, result, rows, valid):
    class Lookup:
        def get(self, url):
            return Fetch(url, "ok", 200, "2026-10-19T00:00:00Z", data=rows)
    session = ExternalToolSession(ctx, result, lookup=Lookup())
    value = session.execute("one", "read_issue_comments", {"repository_id": "repo", "number": 23})
    assert (value["status"] == "ok") is valid
    if valid:
        assert not session.document_errors
        if not rows:
            assert value["text"] == "" and not session.evidence
    else:
        assert value["text"] == "" and session.document_errors


def test_context_metadata_list_is_not_treated_as_a_concrete_thread(ctx, result):
    source = result.sources[0].model_copy(update={"kind": "metadata", "snapshot_ref": capture_snapshot(
        kind="metadata", normalized_json=[{"metadata": "not a thread listing"}])})
    session = ExternalToolSession(ctx, result)
    session.existing_document(source)
    assert not session.document_errors


def test_association_only_defers_discussions_without_false_budget_warning(ctx, result):
    add_thread(result, "Generic paper question")
    result.checks.repositories[0].association = "uncertain"
    add_readme(result, "Official implementation of Another Method.")
    output = enrich_code_checks(ctx, result, Client(association="rejected",
        quote="Official implementation of Another Method."), mode="native_tools", target_discussion_ids=[])
    assert output.status == "completed"
    assert output.audit["unreviewed_discussion_ids"] == []
    assert output.audit["deferred_discussion_ids"] == ["discussion"]
    assert not any("discussion item budget reached" in w for w in output.warnings)


def test_discussion_cap_retains_explicit_unreviewed_ids(ctx, result):
    add_thread(result, "Unknown paper context")
    result.checks.discussions.extend(result.checks.discussions[0].model_copy(
        update={"discussion_id": f"discussion_{n}"}, deep=True) for n in range(2, 14))

    class BoundedClient(Client):
        def complete_tools(self, messages, tools, **kwargs):
            task = json.loads(messages[1]["content"])
            raw = json.dumps({"repositories": [], "discussions": [{"discussion_id": did,
                "signal": "discussion_only", "relevance": "undetermined", "resolution": "unknown",
                "conditions": "Unknown", "reason": "No paper-specific judgment", "citations": []}
                for did in task["review_target_ids"]["discussions"]]})
            return LLMToolResponse(raw, [], {"role": "assistant", "content": raw}, "test", "test")

    output = enrich_code_checks(ctx, result, BoundedClient(), mode="native_tools")
    assert output.status == "completed"
    assert len(output.audit["target_discussion_ids"]) == 8
    assert output.audit["unreviewed_discussion_ids"] == [f"discussion_{n}" for n in range(9, 14)]
    assert len(output.checks.checks.discussions) == 13
    assert any("discussion item budget reached" in w for w in output.warnings)


def test_local_range_delimiter_is_not_quotable_as_original_source(ctx, result):
    add_readme(result, "Another Method\n" + "details " * 4000 + "\nPaper: 2510.10125\n")

    class CrossGapClient(Client):
        def complete_tools(self, messages, tools, **kwargs):
            task = json.loads(messages[1]["content"])
            text = next(d["text"] for d in task["external_documents"] if d["source_id"] == "readme")
            pos = text.index("[Archived source gap omitted]")
            self.quote = text[pos - 20:pos + 60]
            return super().complete_tools(messages, tools, **kwargs)

    output = enrich_code_checks(ctx, result, CrossGapClient(association="rejected"),
                                mode="native_tools", max_rounds=1)
    assert output.status == "failed" and output.audit["validation_issue"]["code"] == "SEMANTIC_QUOTE_NOT_FOUND"
    assert not output.audit.get("output_snapshot_ref")
