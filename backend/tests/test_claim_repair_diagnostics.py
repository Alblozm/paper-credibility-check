"""Repair adds exact source context while keeping quote/numeric guards strict."""
from __future__ import annotations

import copy
import json

import pytest

from app.credibility import claim_model, snapshots
from app.credibility.context import CheckContext
from app.credibility.locate import find_source_quote
from app.llm.base import LLMResponse
from app.llm.retry import LLMOutputInvalid, RetryPolicy
from app.parsing.base import PaperElement, ParsedDocument, TextBlock
from app.schemas.paper import Paper

ACTUAL = (
    "We finda logarithmicchaindoesnot addmuch, while a linear chain affords more power "
    "on inherently sequential reasoning problems."
)
RESTORED_SPACES = (
    "We finda logarithmic chaindoes not addmuch, while a linear chain affords more power "
    "on inherently sequential reasoning problems."
)
# Dropping a word still fails both the strict and spacing-tolerant matchers.
DROPPED_NEGATION = (
    "We finda logarithmicchaindoes addmuch, while a linear chain affords more power "
    "on inherently sequential reasoning problems."
)
RESULT = "A linear chain can recognize all regular languages."


class FakeDeepSeek:
    model_id = "model_standard_v1"
    model_version = "deepseek-repair-test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.tasks = []

    def complete_json(self, system_prompt, user_prompt, **kwargs):
        self.tasks.append(json.loads(user_prompt))
        value = self.responses.pop(0)
        return LLMResponse(json.dumps(value), self.model_id, self.model_version,
                           raw={"id": "offline", "reasoning_content": "PRIVATE_REASONING_NOT_FOR_ARTIFACTS"})


@pytest.fixture
def repair_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(claim_model, "log_run", lambda record: None)
    captured = []
    real_capture = snapshots.capture_snapshot

    def capture(**kwargs):
        ref = real_capture(**kwargs)
        captured.append((ref, kwargs))
        return ref

    monkeypatch.setattr(snapshots, "capture_snapshot", capture)
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2310.07923v5", "upload_id": None},
        repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None,
        unresolved_reason=None, assessment_id="cred_repair_diagnostic", model_id="model_standard_v1",
        doc=ParsedDocument(
            paper=Paper(paper_id="paper005_case", title="Theoretical paper", page_count=2, parse_quality="good"),
            source_sha256="a" * 64, blocks=[
                TextBlock(ACTUAL, 1, chunk_id="paper_005_v5_chunk_0003", section_role="introduction"),
                TextBlock(RESULT, 2, chunk_id="result", section_role="results"),
            ], elements=[PaperElement("table1", "table", caption="Metric 90%; baseline 80%.",
                                      extracted_text="Accuracy 90%.", page_number=2, section_role="results")],
        ),
    )
    return ctx, captured


def extraction(quote=ACTUAL):
    return {
        "selection_scope": "All supplied abstract and introduction blocks.",
        "scope_complete": True, "scope_limitations": [], "claims": [{
            "claim_id": "C015", "statement": "A linear chain affords more power than a logarithmic chain.",
            "importance": "core", "claim_conditions": {}, "claim_evidence": [{
                "evidence_id": "E022", "mode": "quote", "excerpt": quote,
                "chunk_id": "paper_005_v5_chunk_0003",
            }],
        }],
    }


def judgments():
    return {"judgments": [{
        "claim_id": "C015", "comparison_evidence": [{"evidence_id": "result_quote", "mode": "quote",
                                                       "excerpt": RESULT, "chunk_id": "result"}],
        "search_scope": "All later results.", "proposed_outcome": "supported",
        "reason": "The result states the claimed regular-language recognition ability.",
    }]}


def invoke(ctx, responses):
    client = FakeDeepSeek(responses)
    result = claim_model.generate_claim_candidates(ctx, client, retry_policy=RetryPolicy(max_attempts=1))
    return result, client


def test_paper005_restored_spaces_are_accepted_as_the_exact_source_slice(repair_env):
    ctx, _ = repair_env
    assert find_source_quote(ACTUAL, RESTORED_SPACES) == ACTUAL
    result, client = invoke(ctx, [extraction(RESTORED_SPACES), judgments()])
    assert result.candidates.claims[0].claim_evidence[0].excerpt == ACTUAL
    assert len(client.tasks) == 2 and len(ctx.model_history) == 2


def test_invalid_quote_repair_receives_exact_source_without_weakening_matcher(repair_env):
    ctx, _ = repair_env
    assert find_source_quote(ACTUAL, DROPPED_NEGATION) is None
    result, client = invoke(ctx, [extraction(DROPPED_NEGATION), extraction(), judgments()])
    repair = client.tasks[1]
    assert "E022" in repair["validation_errors"]
    assert repair["untrusted_exact_source_context"] == [{
        "claim_id": "C015", "evidence_id": "E022", "chunk_id": "paper_005_v5_chunk_0003",
        "source_found": True, "section_role": "introduction", "eligible_for_this_stage": True,
        "source_text": ACTUAL,
    }]
    instruction = repair["repair_instruction"]
    assert "character for character" in instruction and "Do not improve PDF typography" in instruction
    assert "insert word spaces" in instruction and "EVERY original" in instruction
    assert "evidence_id and claim statement" in instruction
    assert result.candidates.claims[0].claim_evidence[0].excerpt == ACTUAL
    assert result.prompt_version == "credibility-claims-live-1.6"
    assert len(ctx.model_history) == 3


def test_final_repair_failure_preserves_validation_error_and_actual_model_history(repair_env):
    ctx, captured = repair_env
    client = FakeDeepSeek([extraction(DROPPED_NEGATION), extraction(DROPPED_NEGATION)])
    with pytest.raises(LLMOutputInvalid, match="after one constrained repair"):
        claim_model.generate_claim_candidates(ctx, client, retry_policy=RetryPolicy(max_attempts=1))
    artifacts = [(ref, kwargs) for ref, kwargs in captured if kwargs["kind"] == "model_validation_error"]
    assert len(artifacts) == 1
    ref, kwargs = artifacts[0]
    stored = snapshots.read_snapshot(ref)
    assert stored.normalized_json["repair_attempted"] is True
    assert "E022" in stored.normalized_json["validation_errors"]
    assert "paper_005_v5_chunk_0003" in stored.normalized_json["validation_errors"]
    assert kwargs["metadata"]["purpose"] == "claim_extraction_repair"
    assert kwargs["metadata"]["assessment_id"] == ctx.assessment_id
    assert len([kwargs for _, kwargs in captured if kwargs["kind"] == "model_output"]) == 2
    assert [call["purpose"] for call in ctx.model_history] == ["claim_extraction", "claim_extraction_repair"]
    assert "PRIVATE_REASONING_NOT_FOR_ARTIFACTS" not in json.dumps(captured)


@pytest.mark.parametrize("change", ["claim_id", "statement", "evidence_id", "add_claim", "add_evidence"])
def test_typography_repair_cannot_change_frozen_claim_or_evidence_identity(repair_env, change):
    ctx, _ = repair_env
    repaired = extraction()
    claim = repaired["claims"][0]
    if change == "claim_id":
        claim["claim_id"] = "different"
    elif change == "statement":
        claim["statement"] += " Additional conclusion."
    elif change == "evidence_id":
        claim["claim_evidence"][0]["evidence_id"] = "different"
    elif change == "add_claim":
        added = copy.deepcopy(claim)
        added["claim_id"], added["statement"] = "C016", "A new claim."
        added["claim_evidence"][0]["evidence_id"] = "E023"
        repaired["claims"].append(added)
    else:
        added = copy.deepcopy(claim["claim_evidence"][0])
        added["evidence_id"] = "E023"
        claim["claim_evidence"].append(added)
    with pytest.raises(LLMOutputInvalid, match="preserve"):
        invoke(ctx, [extraction(DROPPED_NEGATION), repaired])


def test_judgment_repair_repeats_actual_element_text_and_keeps_digits_strict(repair_env):
    ctx, _ = repair_env
    bad = judgments()
    bad["judgments"][0]["comparison_evidence"] = [{
        "evidence_id": "element_quote", "mode": "quote", "element_id": "table1", "excerpt": "Metric 95%.",
    }]
    good = copy.deepcopy(bad)
    good["judgments"][0]["comparison_evidence"][0]["excerpt"] = "Accuracy 90%."
    _, client = invoke(ctx, [extraction(), bad, good])
    context = client.tasks[-1]["untrusted_exact_source_context"][0]
    assert context["element_id"] == "table1"
    assert context["source_text"] == "Metric 90%; baseline 80%.\nAccuracy 90%."
    assert context["eligible_for_this_stage"] is True
    assert "change numbers" in client.tasks[-1]["repair_instruction"]
    isolated, _ = invoke(ctx, [extraction(), bad, bad])
    assert isolated.candidates.claims[0].proposed_outcome == "unable_to_determine"
    assert isolated.candidates.claims[0].comparison_evidence == []


def test_invalid_unknown_source_id_is_not_fabricated_in_repair_context(repair_env):
    ctx, _ = repair_env
    bad = judgments()
    bad["judgments"][0]["comparison_evidence"][0]["chunk_id"] = "invented_chunk"
    bad["judgments"][0]["comparison_evidence"][0]["excerpt"] = "No such source quote."
    _, client = invoke(ctx, [extraction(), bad, judgments()])
    repeated = client.tasks[-1]["untrusted_exact_source_context"]
    assert repeated[0]["chunk_id"] == "invented_chunk" and repeated[0]["source_found"] is False
    assert "source_text" not in repeated[0]


def test_schema_diagnostic_omits_arbitrary_provider_values(repair_env):
    ctx, captured = repair_env
    invalid = extraction()
    invalid["unexpected_provider_value"] = "UNTRUSTED_VALUE_NOT_NEEDED_IN_VALIDATION_ERROR"
    with pytest.raises(LLMOutputInvalid):
        invoke(ctx, [invalid, invalid])
    diagnostic = next(kwargs["normalized_json"]["validation_errors"] for _, kwargs in captured
                      if kwargs["kind"] == "model_validation_error")
    assert "unexpected_provider_value" in diagnostic
    assert "UNTRUSTED_VALUE_NOT_NEEDED_IN_VALIDATION_ERROR" not in diagnostic


def test_quote_repair_cannot_silently_change_conditions_or_importance(repair_env):
    ctx, _ = repair_env
    corrected = extraction()
    corrected["claims"][0]["claim_conditions"] = {"dataset": "Invented dataset"}
    with pytest.raises(LLMOutputInvalid, match="non-quote fields"):
        invoke(ctx, [extraction(DROPPED_NEGATION), corrected])


def test_judgment_quote_repair_cannot_relabel_an_outcome(repair_env):
    ctx, _ = repair_env
    bad = judgments()
    bad["judgments"][0]["comparison_evidence"][0]["excerpt"] = "Not present."
    repaired = judgments()
    repaired["judgments"][0]["proposed_outcome"] = "partially_supported"
    isolated, _ = invoke(ctx, [extraction(), bad, repaired])
    item = isolated.candidates.claims[0]
    assert item.proposed_outcome == "unable_to_determine"
    assert item.comparison_evidence == []
    assert bad["judgments"][0]["reason"] in item.reason
    assert "partially_supported" not in item.reason


def two_claims():
    value = extraction()
    second = copy.deepcopy(value["claims"][0])
    second.update(claim_id="C016", statement="A linear chain recognizes regular languages.")
    second["claim_evidence"][0]["evidence_id"] = "E023"
    value["claims"].append(second)
    return value


def two_judgments():
    value = judgments()
    second = copy.deepcopy(value["judgments"][0])
    second["claim_id"] = "C016"
    second["comparison_evidence"][0]["evidence_id"] = "result_quote2"
    value["judgments"].append(second)
    return value


@pytest.mark.parametrize("corruption", ["role", "quote", "number", "anchor"])
def test_invalid_comparison_span_only_downgrades_affected_claim(repair_env, corruption):
    ctx, captured = repair_env
    bad = two_judgments()
    span = bad["judgments"][0]["comparison_evidence"][0]
    if corruption == "role":
        span.update(chunk_id="paper_005_v5_chunk_0003", excerpt=ACTUAL)
    elif corruption == "quote":
        span["excerpt"] = "Fabricated proof."
    elif corruption == "number":
        span["values"] = ["999"]
    else:
        span.update(chunk_id="missing", excerpt="Fabricated proof.")
    result, _ = invoke(ctx, [two_claims(), bad, bad])
    first, second = result.candidates.claims
    assert first.proposed_outcome == "unable_to_determine" and first.comparison_evidence == []
    assert second.proposed_outcome == "supported" and len(second.comparison_evidence) == 1
    assert any("C015" in limitation for limitation in result.candidates.scope_limitations)
    diagnostic = next(k["normalized_json"] for _, k in captured if k["kind"] == "model_validation_error")
    assert diagnostic["isolated"] is True and diagnostic["isolation_limitations"]


def test_extraction_excludes_unanchored_claim_but_keeps_other_claims(repair_env):
    ctx, _ = repair_env
    bad = two_claims()
    bad["claims"][0]["claim_evidence"][0]["excerpt"] = DROPPED_NEGATION
    later = two_judgments()
    later["judgments"] = later["judgments"][1:]
    result, client = invoke(ctx, [bad, bad, later])
    assert [claim.claim_id for claim in result.candidates.claims] == ["C016"]
    assert result.candidates.scope_complete is False
    assert any("C015 excluded" in x for x in result.candidates.scope_limitations)
    assert [claim["claim_id"] for claim in client.tasks[-1]["fixed_extracted_claims"]["claims"]] == ["C016"]


def test_repair_identity_violation_recovers_first_extraction_not_repair(repair_env):
    ctx, _ = repair_env
    bad = two_claims()
    bad["claims"][0]["claim_evidence"][0]["excerpt"] = DROPPED_NEGATION
    repaired = copy.deepcopy(bad)
    repaired["claims"][1]["statement"] = "Injected conclusion."
    later = two_judgments()
    later["judgments"] = later["judgments"][1:]
    result, _ = invoke(ctx, [bad, repaired, later])
    assert result.candidates.claims[0].statement == bad["claims"][1]["statement"]
    assert result.candidates.scope_complete is False


@pytest.mark.parametrize("duplicate", ["claim", "evidence"])
def test_structural_duplicate_ids_still_fail_instead_of_isolation(repair_env, duplicate):
    ctx, _ = repair_env
    bad = two_claims()
    if duplicate == "claim":
        bad["claims"][1]["claim_id"] = "C015"
    else:
        bad["claims"][1]["claim_evidence"][0]["evidence_id"] = "E022"
    with pytest.raises(LLMOutputInvalid, match="Duplicate"):
        invoke(ctx, [bad, bad])


def test_mixed_valid_and_invalid_comparison_evidence_is_not_assumed_fully_supporting(repair_env):
    ctx, _ = repair_env
    bad = judgments()
    bad["judgments"][0]["comparison_evidence"].append({
        "evidence_id": "invalid", "mode": "quote", "excerpt": "Not present.", "chunk_id": "result",
    })
    result, _ = invoke(ctx, [extraction(), bad, bad])
    item = result.candidates.claims[0]
    assert item.proposed_outcome == "unable_to_determine"
    assert len(item.comparison_evidence) == 1
    assert item.comparison_evidence[0].excerpt == RESULT


def test_isolated_checkpoint_retains_diagnostics_and_does_not_call_model_again(repair_env):
    ctx, _ = repair_env
    saved = {}
    ctx.checkpoint_load = lambda purpose, fingerprint: saved.get((purpose, fingerprint))
    ctx.checkpoint_save = lambda purpose, fingerprint, payload: saved.__setitem__((purpose, fingerprint), payload)
    bad = judgments()
    bad["judgments"][0]["comparison_evidence"][0]["excerpt"] = "Not present."
    first, _ = invoke(ctx, [extraction(), bad, bad])
    second, client = invoke(ctx, [])
    assert client.tasks == []
    assert second.candidates.model_dump() == first.candidates.model_dump()
    assert second.candidates.scope_limitations
