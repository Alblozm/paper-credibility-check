"""Provider output failures remain billed attempts, never recovered judgments."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import httpx
import pytest

from app.credibility.code_lookup import Fetch
from app.credibility.code_model import enrich_code_checks
from app.credibility.context import CheckContext
from app.credibility.models import CodeCheckResult
from app.credibility.snapshots import capture_snapshot, read_snapshot
from app.llm.deepseek import DeepSeekClient, safe_provider_diagnostics
from app.llm.retry import LLMOutputInvalid
from app.parsing.base import ParsedDocument
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

API = "https://api.github.com/repos/alice/method"
ISSUE = "https://github.com/alice/method/issues/23"
BODY = "I cannot reproduce the paper result; could you explain the evaluation setup?"
SECRET = "never-persist-this-provider-secret"


@pytest.fixture
def ctx():
    return CheckContext(source={"type": "arxiv", "arxiv_id": "2401.12345"}, repo_root=Path("."),
                        generated_at="2026-10-19T00:00:00Z", ref=None, unresolved_reason=None,
                        doc=ParsedDocument(paper=Paper(paper_id="paper", title="A Reliable Learning Method",
                                                      arxiv_id="2401.12345", parse_quality=ParseQuality.GOOD)))


@pytest.fixture
def result():
    ref = capture_snapshot(kind="issue", normalized_json=[{"html_url": ISSUE, "body": BODY}])
    source = dict(source_id="repo", kind="repository", url=API, fetch_status="ok", http_status=200,
                  snapshot_ref=None, retrieved_at="2026-10-19T00:00:00Z", observed_at="2026-10-19T00:00:00Z",
                  is_cached=False, association_reason="Candidate")
    return CodeCheckResult.model_validate({"dimension": {"dimension_id": "code_reproduction",
        "applicability": "applicable", "applicability_reason": "Empirical method", "check_status": "completed",
        "score_status": "unable_to_assess", "score": None, "weight": 1, "score_rule_ids": [],
        "summary": "Pending", "unassessed_reason": None, "evidence_ids": [], "finding_ids": []},
        "checks": {"scope_complete": False, "scope_limitations": [], "searches": [],
        "code_availability": "unable_to_verify", "reproduction_signal": "unable_to_verify", "repositories": [{
        "repository_id": "repo", "source_id": "repo", "association": "confirmed",
        "association_reason": "Prior source-bound confirmation", "association_evidence_ids": [],
        "availability": "accessible", "implementation_evidence_ids": [], "inspected_revision": "a" * 40}],
        "discussions": [{"discussion_id": "thread", "repository_id": "repo", "source_ids": ["issue"],
        "evidence_ids": [], "signal": "discussion_only", "relevance": "undetermined", "independence": "unknown",
        "conditions": "Unknown", "resolution": "unknown", "author_response_source_ids": [],
        "scoring_eligible": False, "reason": "Pending"}], "base_score_rule_id": None, "score_rule_id": None},
        "sources": [source, {**source, "source_id": "issue", "kind": "issue", "url": ISSUE, "snapshot_ref": ref}],
        "evidence": [], "findings": []})


def envelope(content="", *, finish="stop", calls=None, usage=None):
    message = {"role": "assistant", "content": content, "reasoning_content": SECRET}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"finish_reason": finish, "message": message}], "model": "deepseek-test",
            "usage": usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 20}}


def final(*, conditions="Setup not established", authority=False):
    judgment = {"discussion_id": "thread", "signal": "failure_reported", "relevance": "relevant",
                "conditions": conditions, "resolution": "unknown", "reason": "A quoted public failure report.",
                "citations": [{"source_id": "issue", "excerpt": BODY}]}
    if authority:
        judgment["scoring_eligible"] = True
    return envelope(json.dumps({"repositories": [], "discussions": [judgment]}))


def tool():
    return envelope(finish="tool_calls", calls=[{"id": "read1", "type": "function", "function": {
        "name": "read_issue", "arguments": '{"repository_id":"repo","number":23}'}}])


@pytest.fixture
def transport(monkeypatch):
    original = httpx.Client

    def configure(responses):
        pending, requests = deque(responses), []

        def handler(request):
            requests.append(json.loads(request.content))
            assert pending, "No request beyond the original model budget is allowed"
            item = pending.popleft()
            return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)

        monkeypatch.setattr("app.llm.deepseek.httpx.Client", lambda **_: original(
            transport=httpx.MockTransport(handler)))
        return DeepSeekClient("test-key"), requests

    return configure


class Lookup:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return Fetch(url, "ok", 200, "2026-10-19T00:00:00Z", data={"html_url": ISSUE, "body": BODY})


def test_length_attempt_diagnostics_are_static_typed_and_no_bad_calls_are_admitted(transport):
    response = envelope(SECRET, finish="length", calls=tool()["choices"][0]["message"]["tool_calls"],
                        usage={"prompt_tokens": 321, "completion_tokens": 16000, "total_tokens": 16321,
                               "reasoning_content": SECRET})
    client, requests = transport([response])
    with pytest.raises(LLMOutputInvalid) as error:
        client.complete_tools([], [])
    assert error.value.provider_diagnostics == {"provider": "deepseek", "error_code": "response_truncated",
        "http_status": 200, "finish_reason": "length", "choices_count": 1, "tool_call_count": 1,
        "usage": {"prompt_tokens": 321, "completion_tokens": 16000, "total_tokens": 16321}}
    assert SECRET not in str(error.value) + json.dumps(vars(error.value)) and len(requests) == 1


@pytest.mark.parametrize("data,code", [
    ([], "invalid_response_envelope"), ({"choices": []}, "invalid_choices"),
    ({"choices": [None]}, "invalid_choice"), ({"choices": [{"message": []}]}, "invalid_assistant_message"),
    (envelope("ok", calls={}), "invalid_tool_calls"),
    (envelope("ok", calls=["bad"]), "invalid_tool_call"),
    (envelope([], calls=[]), "invalid_assistant_content"),
    (envelope("ok", usage={"prompt_tokens": True}), "invalid_usage"),
    (envelope("ok", usage={"completion_tokens": -1}), "invalid_usage"),
])
def test_malformed_success_envelopes_have_specific_non_prose_diagnostics(transport, data, code):
    client, _ = transport([data])
    with pytest.raises(LLMOutputInvalid) as error:
        client.complete_tools([], [])
    assert error.value.provider_diagnostics["error_code"] == code
    assert SECRET not in json.dumps(vars(error.value))


def test_http200_non_json_has_diagnostic_without_response_body(transport):
    client, _ = transport([httpx.Response(200, text=SECRET)])
    with pytest.raises(LLMOutputInvalid) as error:
        client.complete_tools([], [])
    assert error.value.provider_diagnostics == {
        "provider": "deepseek", "error_code": "invalid_json_response", "http_status": 200}
    assert SECRET not in json.dumps(vars(error.value))


@pytest.mark.parametrize("key,value", [("error_code", []), ("error_code", {}),
                                      ("finish_reason", []), ("finish_reason", {}),
                                      ("usage", SECRET)])
def test_diagnostic_projection_rejects_untyped_values_without_masking_failure(key, value):
    facts = {"provider": "deepseek", "error_code": "invalid_choices", "finish_reason": "stop",
             "http_status": 200, key: value, "raw": SECRET, "response_id": SECRET}
    safe = safe_provider_diagnostics(facts)
    assert SECRET not in json.dumps(safe)
    if key == "error_code":
        assert safe == {}
    else:
        assert safe["error_code"] == "invalid_choices" and key not in safe


def test_native_one_retry_preserves_failed_paid_attempt_without_raw_content(ctx, result, transport):
    client, requests = transport([envelope(SECRET, finish="length"), final()])
    lookup = Lookup()
    output = enrich_code_checks(ctx, result, client, mode="native_tools", lookup=lookup)
    assert output.status == "completed" and output.audit["attempt_count"] == 2
    assert len(output.model_calls) == output.audit["model_call_count"] == 1
    assert [a["status"] for a in output.audit["attempts"]] == ["failed", "returned"]
    first = output.audit["attempts"][0]
    assert first["provider_diagnostics"]["finish_reason"] == "length"
    assert first["usage"]["completion_tokens"] == 20
    terminal = read_snapshot(first["result_snapshot_ref"])
    assert terminal.normalized_json["status"] == "failed" and terminal.manifest["parent_ref"] == first[
        "attempt_snapshot_ref"]
    assert [r["tool_choice"] for r in requests] == ["auto", "none"]
    assert "same review task" in requests[-1]["messages"][-1]["content"]
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert output.audit["provider_output_retry_count"] == 1
    assert output.audit["external_request_count"] == output.audit["tool_call_count"] == 0 and not lookup.urls
    assert SECRET not in json.dumps(output.audit)
    assert not output.checks.checks.discussions[0].scoring_eligible


@pytest.mark.parametrize("max_rounds,failures,reason", [
    (1, 1, "model_round_budget_exhausted"), (3, 2, "one_provider_output_retry_exhausted")])
def test_no_retry_when_exhausted_and_never_more_than_one_recovery(ctx, result, transport,
                                                               max_rounds, failures, reason):
    client, requests = transport([envelope(SECRET, finish="length")] * failures)
    output = enrich_code_checks(ctx, result, client, mode="native_tools", max_rounds=max_rounds, lookup=Lookup())
    assert output.status == "failed" and output.checks.dimension.check_status == "partial"
    assert output.audit["attempt_count"] == len(requests) == failures and not output.model_calls
    assert output.audit["provider_output_retry_skipped_reason"] == reason
    assert all(a["status"] == "failed" for a in output.audit["attempts"])
    assert "output_snapshot_ref" not in output.audit


def test_retry_preserves_valid_assistant_tool_history_and_original_request_budget(ctx, result, transport):
    client, requests = transport([tool(), envelope(SECRET, finish="length"), final()])
    result.external_request_count = 39
    lookup = Lookup()
    output = enrich_code_checks(ctx, result, client, mode="native_tools", lookup=lookup)
    assert output.status == "completed" and output.audit["attempt_count"] == 3
    assert len(output.model_calls) == 2 and output.checks.external_request_count == 40
    assert output.audit["tool_call_count"] == len(lookup.urls) == 1
    assert [r["tool_choice"] for r in requests] == ["auto", "none", "none"]
    history = requests[-1]["messages"]
    assert any(row.get("tool_calls") for row in history if row["role"] == "assistant")
    assert any(row.get("tool_call_id") == "read1" for row in history if row["role"] == "tool")
    assert SECRET not in json.dumps(output.audit)
    for attempt in output.audit["attempts"]:
        assert SECRET not in json.dumps(read_snapshot(attempt["input_snapshot_ref"]).normalized_json)


@pytest.mark.parametrize("responses,max_rounds", [([tool()], 1),
    ([envelope(SECRET, finish="length"), tool()], 3)])
def test_none_tool_choice_never_executes_provider_tool_calls(ctx, result, transport, responses, max_rounds):
    client, requests = transport(responses)
    lookup = Lookup()
    output = enrich_code_checks(ctx, result, client, mode="native_tools", max_rounds=max_rounds, lookup=lookup)
    assert output.status == "failed" and output.audit["validation_issue"]["code"] == "SEMANTIC_TOOLS_DISALLOWED"
    assert len(requests) == len(responses) and not lookup.urls and output.audit["tool_call_count"] == 0


@pytest.mark.parametrize("initial_tool", [False, True])
def test_provider_failure_and_numeric_repair_share_actual_attempt_slots(ctx, result, transport, initial_tool):
    bad = final(conditions="The measured accuracy is 99.99 percent.")
    responses = ([tool(), envelope(SECRET, finish="length"), bad] if initial_tool
                 else [envelope(SECRET, finish="length"), bad, bad])
    client, requests = transport(responses)
    output = enrich_code_checks(ctx, result, client, mode="native_tools", max_rounds=3, lookup=Lookup())
    assert output.status in {"failed", "partial"} and len(requests) == output.audit["attempt_count"] == 3
    assert "output_snapshot_ref" not in output.audit
    assert output.checks.checks.discussions[0].conditions == "Unknown"
    assert output.audit["validation_issue"]["code"] == "SEMANTIC_CONDITIONS_NUMBER_UNSUPPORTED"
    if initial_tool:
        assert output.audit["repair_skipped_reason"] == "model_round_budget_exhausted"


def test_provider_failure_during_json_repair_can_use_only_last_existing_slot(ctx, result, transport):
    client, requests = transport([final(conditions="Unsupported value 99.99"),
                                  envelope(SECRET, finish="length"), final()])
    output = enrich_code_checks(ctx, result, client, mode="native_tools", max_rounds=3, lookup=Lookup())
    assert output.status == "completed" and output.audit["attempt_count"] == len(requests) == 3
    assert [a["status"] for a in output.audit["attempts"]] == ["returned", "failed", "returned"]
    assert output.audit["repair_attempts"] == output.audit["provider_output_retry_count"] == 1
    assert output.audit["rounds"][-1]["repair"] is True


def test_permission_violation_is_not_provider_retried_or_schema_repaired(ctx, result, transport):
    client, requests = transport([final(authority=True)])
    output = enrich_code_checks(ctx, result, client, mode="native_tools", lookup=Lookup())
    assert output.status == "failed" and output.audit["attempt_count"] == len(requests) == 1
    assert "provider_output_retry_count" not in output.audit and "repair_attempts" not in output.audit


@pytest.mark.parametrize("mode,response", [("native_tools", httpx.Response(429, json={"error": SECRET})),
                                         ("prefetch", envelope(SECRET, finish="length"))])
def test_recovery_does_not_retry_http_errors_or_change_explicit_mode(ctx, result, transport, mode, response):
    client, requests = transport([response])
    output = enrich_code_checks(ctx, result, client, mode=mode, lookup=Lookup())
    assert output.status == "failed" and output.audit["attempt_count"] == len(requests) == 1
    assert "provider_output_retry_count" not in output.audit and output.mode == mode
    assert SECRET not in json.dumps(output.audit)
