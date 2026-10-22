"""Strict unique anchor correction preserves model meaning and existing guards."""
from __future__ import annotations

import copy
import json

import pytest

from app.credibility import claim_model, snapshots
from app.credibility.context import CheckContext
from app.llm.base import LLMResponse
from app.llm.retry import RetryPolicy
from app.parsing.base import PaperElement, ParsedDocument, TextBlock
from app.schemas.paper import Paper

CLAIM = "The method reduces memory overhead when switching tasks."
QUOTE = "We can recover W0 by subtracting BA, a quick operation with very little memory overhead."
CONDITIONS = {"metric": "memory overhead", "dataset": None, "baseline": None, "setting": "switching tasks"}


@pytest.fixture
def anchor_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(claim_model, "log_run", lambda record: None)
    records = []
    capture = snapshots.capture_snapshot

    def spy(**kwargs):
        ref = capture(**kwargs)
        records.append((ref, kwargs))
        return ref

    monkeypatch.setattr(snapshots, "capture_snapshot", spy)
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2106.09685v2", "upload_id": None},
        repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None, unresolved_reason=None,
        assessment_id="cred_unique_anchor", model_id="model_standard_v1", doc=ParsedDocument(
            paper=Paper(paper_id="strict_anchor", title="Strict anchor test", page_count=3, parse_quality="good"),
            source_sha256="f" * 64, blocks=[
                TextBlock(CLAIM, 1, chunk_id="abstract", section_role="abstract"),
                TextBlock(QUOTE, 2, chunk_id="chunk_0009", section_role="results"),
                TextBlock("There is no additional inference latency.", 3,
                          chunk_id="chunk_0010", section_role="results"),
                TextBlock("References: an unrelated result.", 3, chunk_id="references", section_role="references"),
            ],
        ),
    )
    return ctx, records


def _extraction():
    return {"selection_scope": "All abstract and introduction claims.", "scope_complete": True,
            "scope_limitations": [], "claims": [{
                "claim_id": "C7", "statement": CLAIM, "importance": "core", "claim_conditions": CONDITIONS,
                "claim_evidence": [{"evidence_id": "claim_quote", "mode": "quote", "excerpt": CLAIM,
                                    "chunk_id": "abstract", "conditions": CONDITIONS}],
            }]}


def _judgment(anchor="chunk_0010", quote=QUOTE):
    return {"judgments": [{
        "claim_id": "C7", "comparison_evidence": [{
            "evidence_id": "Q_C7_2", "mode": "quote", "excerpt": quote,
            "chunk_id": anchor, "conditions": CONDITIONS,
        }], "search_scope": "All later results blocks.", "proposed_outcome": "supported",
        "reason": "The later text states the claimed memory overhead benefit.",
    }]}


def _task(ctx, *, claim_side=False):
    block_key = "untrusted_abstract_and_introduction" if claim_side else "untrusted_later_body"
    task = {block_key: [
        {"chunk_id": block.chunk_id} for block in ctx.doc.blocks
        if (block.section_role in claim_model.CLAIM_ROLES if claim_side
            else block.section_role not in claim_model._BODY_FORBIDDEN_ROLES)
    ]}
    if not claim_side:
        task["untrusted_later_elements"] = [{"element_id": element.element_id} for element in ctx.doc.elements]
    return task


def _corrections(records):
    return [(ref, kwargs) for ref, kwargs in records if kwargs["kind"] == "claim_anchor_correction"]


def test_unique_wrong_chunk_is_reanchored_without_any_model_repair_or_semantic_change(anchor_env):
    ctx, records = anchor_env
    initial = _judgment()

    class FakeDeepSeek:
        model_id, model_version = "model_standard_v1", "deepseek-anchor-test"

        def __init__(self):
            self.responses, self.calls = [_extraction(), initial], 0

        def complete_json(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse(json.dumps(self.responses.pop(0)), self.model_id, self.model_version)

    client = FakeDeepSeek()
    result = claim_model.generate_claim_candidates(ctx, client, retry_policy=RetryPolicy(max_attempts=1))
    span = result.candidates.claims[0].comparison_evidence[0]
    assert client.calls == 2 and result.prompt_version == "credibility-claims-live-1.6"
    assert span.chunk_id == "chunk_0009" and span.excerpt == QUOTE
    assert span.conditions.model_dump() == CONDITIONS
    assert result.candidates.claims[0].proposed_outcome == "supported"
    assert result.candidates.claims[0].reason == initial["judgments"][0]["reason"]
    ref, record = _corrections(records)[0]
    assert record["metadata"]["assessment_id"] == ctx.assessment_id
    change = snapshots.read_snapshot(ref).normalized_json["changes"][0]
    assert change["original_anchor"] == {"chunk_id": "chunk_0010"}
    assert change["corrected_anchor"] == {"chunk_id": "chunk_0009"}
    assert change["original_excerpt"] == change["matched_source_excerpt"] == QUOTE


@pytest.mark.parametrize("duplicate", ["another_chunk", "same_chunk", "element"])
def test_multiple_strict_matches_are_not_guessed(anchor_env, duplicate):
    ctx, records = anchor_env
    if duplicate == "another_chunk":
        ctx.doc.blocks.append(TextBlock(QUOTE, 3, chunk_id="duplicate", section_role="results"))
    elif duplicate == "same_chunk":
        ctx.doc.blocks[1].text += "\n" + QUOTE
    else:
        ctx.doc.elements.append(PaperElement("duplicate", "table", caption=QUOTE, section_role="results"))
    result = claim_model._Judgments.model_validate(_judgment())
    before = result.model_dump()
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    assert result.model_dump() == before
    assert _corrections(records) == []
    with pytest.raises(ValueError, match="quote"):
        claim_model._validate_judgments(ctx, claim_model._Extraction.model_validate(_extraction()), result)


@pytest.mark.parametrize("role", ["abstract", "introduction", "background", "references", "acknowledgments"])
def test_forbidden_source_role_is_never_used_as_an_automatic_destination(anchor_env, role):
    ctx, records = anchor_env
    ctx.doc.blocks[1].section_role = role
    result = claim_model._Judgments.model_validate(_judgment())
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    assert result.judgments[0].comparison_evidence[0].chunk_id == "chunk_0010"
    assert _corrections(records) == []


def test_explicit_forbidden_anchor_is_not_silently_promoted_to_eligible_evidence(anchor_env):
    ctx, records = anchor_env
    result = claim_model._Judgments.model_validate(_judgment(anchor="references"))
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    assert result.judgments[0].comparison_evidence[0].chunk_id == "references"
    assert _corrections(records) == []
    with pytest.raises(ValueError, match="outside|cannot serve|quote"):
        claim_model._validate_judgments(ctx, claim_model._Extraction.model_validate(_extraction()), result)


def test_exact_match_in_a_source_not_supplied_to_this_stage_is_not_used(anchor_env):
    ctx, records = anchor_env
    result = claim_model._Judgments.model_validate(_judgment())
    task = {"untrusted_later_body": [{"chunk_id": "chunk_0010"}], "untrusted_later_elements": []}
    claim_model._reanchor_unique_quotes(ctx, task, result, "claim_judgment")
    assert result.judgments[0].comparison_evidence[0].chunk_id == "chunk_0010"
    assert _corrections(records) == []


def test_numeric_declarations_are_still_validated_after_correcting_the_anchor(anchor_env):
    ctx, records = anchor_env
    ctx.doc.blocks[1].text = "Memory overhead is 90 units."
    output = _judgment(quote=ctx.doc.blocks[1].text)
    output["judgments"][0]["comparison_evidence"][0]["values"] = ["100"]
    result = claim_model._Judgments.model_validate(output)
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    span = result.judgments[0].comparison_evidence[0]
    assert span.chunk_id == "chunk_0009" and span.values == ["100"]
    assert span.conditions.model_dump() == CONDITIONS
    with pytest.raises(ValueError, match="100 does not appear"):
        claim_model._validate_judgments(ctx, claim_model._Extraction.model_validate(_extraction()), result)
    assert len(_corrections(records)) == 1


def test_changed_number_or_missing_word_space_is_never_a_strict_anchor_match(anchor_env):
    ctx, records = anchor_env
    ctx.doc.blocks[1].text = "Memoryoverhead is 90 units."
    for quote in ("Memory overhead is 90 units.", "Memoryoverhead is 95 units."):
        result = claim_model._Judgments.model_validate(_judgment(quote=quote))
        claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
        assert result.judgments[0].comparison_evidence[0].chunk_id == "chunk_0010"
    assert _corrections(records) == []


def test_unique_element_match_corrects_anchor_and_keeps_parser_page(anchor_env):
    ctx, records = anchor_env
    ctx.doc.blocks[1].text = "No quoted result in this block."
    ctx.doc.elements.append(PaperElement("table1", "table", caption=QUOTE, page_number=3, section_role="results"))
    result = claim_model._Judgments.model_validate(_judgment())
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    span = result.judgments[0].comparison_evidence[0]
    assert span.chunk_id is None and span.element_id == "table1"
    claim_model._validate_judgments(ctx, claim_model._Extraction.model_validate(_extraction()), result)
    assert span.conditions.model_dump() == CONDITIONS and span.excerpt == QUOTE
    assert _corrections(records)[0][1]["normalized_json"]["changes"][0]["corrected_anchor"] == {"element_id": "table1"}


def test_claim_extraction_reanchor_stays_inside_abstract_introduction_pool(anchor_env):
    ctx, records = anchor_env
    ctx.doc.blocks.insert(1, TextBlock("A different introduction statement.", 1,
                                     chunk_id="intro", section_role="introduction"))
    payload = _extraction()
    payload["claims"][0]["claim_evidence"][0]["chunk_id"] = "intro"
    result = claim_model._Extraction.model_validate(payload)
    before_conditions = copy.deepcopy(result.claims[0].claim_conditions.model_dump())
    claim_model._reanchor_unique_quotes(ctx, _task(ctx, claim_side=True), result, "claim_extraction")
    assert result.claims[0].claim_evidence[0].chunk_id == "abstract"
    assert result.claims[0].claim_conditions.model_dump() == before_conditions
    assert len(_corrections(records)) == 1


@pytest.mark.parametrize("destination", ["chunk", "element"])
def test_dual_anchors_are_resolved_only_by_one_strict_eligible_source(anchor_env, destination):
    ctx, records = anchor_env
    caption = "A different eligible table passage." if destination == "chunk" else QUOTE
    ctx.doc.elements.append(PaperElement("table1", "table", caption=caption, page_number=3, section_role="results"))
    if destination == "element":
        ctx.doc.blocks[1].text = "No matching quote here."
    payload = _judgment(anchor="chunk_0010")
    payload["judgments"][0]["comparison_evidence"][0]["element_id"] = "table1"
    result = claim_model._Judgments.model_validate(payload)
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    span = result.judgments[0].comparison_evidence[0]
    expected = {"chunk_id": "chunk_0009"} if destination == "chunk" else {"element_id": "table1"}
    assert {key: value for key, value in (("chunk_id", span.chunk_id), ("element_id", span.element_id))
            if value is not None} == expected
    assert span.excerpt == QUOTE and span.conditions.model_dump() == CONDITIONS
    claim_model._validate_judgments(ctx, claim_model._Extraction.model_validate(_extraction()), result)
    audit = _corrections(records)[0][1]["normalized_json"]["changes"][0]
    assert audit["original_anchor"] == {"chunk_id": "chunk_0010", "element_id": "table1"}
    assert audit["corrected_anchor"] == expected


@pytest.mark.parametrize("forbidden", [False, True])
def test_dual_anchor_ambiguity_or_forbidden_original_is_never_auto_cleared(anchor_env, forbidden):
    ctx, records = anchor_env
    ctx.doc.elements.append(PaperElement("table1", "table", caption=QUOTE, page_number=3,
                                        section_role="references" if forbidden else "results"))
    payload = _judgment()
    payload["judgments"][0]["comparison_evidence"][0]["element_id"] = "table1"
    result = claim_model._Judgments.model_validate(payload)
    before = result.model_dump()
    claim_model._reanchor_unique_quotes(ctx, _task(ctx), result, "claim_judgment")
    assert result.model_dump() == before and _corrections(records) == []
    with pytest.raises(ValueError, match="exactly one"):
        claim_model._validate_judgments(ctx, claim_model._Extraction.model_validate(_extraction()), result)
