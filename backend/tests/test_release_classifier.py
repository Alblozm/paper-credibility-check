"""Model labels decide what a release claim asserts; repository matching stays deterministic."""
from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.credibility.models import CredibilityModelCall
from app.credibility.reconcile import SUPPORTED_BY_CODE_RULE, reconcile_artifact_claims
from app.credibility.release_classifier import ReleaseLabel, classify_release_claims, safe_classify
from app.credibility.rubric import load_credibility_rubric
from app.credibility.shell import CheckSet, assemble_report
from app.llm.base import LLMResponse
from tests.test_reconcile import _claims, _code, _context, _references

_LORA = (
    "We release a package that facilitates the integration of LoRA with PyTorch models and provide our "
    "implementations and model checkpoints for RoBERTa, DeBERTa, and GPT-2 at https://github.com/example/paper."
)


def _label(**overrides) -> ReleaseLabel:
    values = {"claim_id": "C1", "is_release_claim": True, "release_is_current": True, "artifacts": ["code"],
              "other_assertions": False, "rationale": "Release claim."}
    return ReleaseLabel(**(values | overrides))


def test_sentence_rules_miss_the_lora_wording_that_model_labels_cover():
    claim, code = _claims(_LORA), _code()
    assert reconcile_artifact_claims(claim, code).checks.items[0].outcome == "unable_to_determine"
    seen = []
    labelled = reconcile_artifact_claims(
        claim, code,
        classify=lambda rows: seen.extend(rows) or {"C1": _label(artifacts=["code", "model_weights"])},
    )
    assert seen == [{"claim_id": "C1", "statement": _LORA, "paper_quotes": [_LORA]}]
    item = labelled.checks.items[0]
    assert item.outcome == "partially_supported" and item.reconciled_from.outcome == "unable_to_determine"


def test_code_only_label_is_supported_by_the_code_rule():
    item = reconcile_artifact_claims(
        _claims("We release a package for LoRA."), _code(), classify=lambda rows: {"C1": _label()},
    ).checks.items[0]
    assert item.outcome == "supported" and item.score_rule_id == SUPPORTED_BY_CODE_RULE


@pytest.mark.parametrize("label", [
    {"is_release_claim": False}, {"release_is_current": False}, {"other_assertions": True},
    {"artifacts": ["data"]}, {"artifacts": ["model_weights"]},
])
def test_non_release_future_combined_or_non_code_labels_do_not_change_claims(label):
    item = reconcile_artifact_claims(
        _claims(_LORA), _code(), classify=lambda rows: {"C1": _label(**label)},
    ).checks.items[0]
    assert item.outcome == "unable_to_determine" and item.reconciled_from is None


def test_model_labels_never_override_a_different_repository_url():
    statement = "We release our code at https://github.com/other/repo."
    item = reconcile_artifact_claims(
        _claims(statement), _code(), classify=lambda rows: {"C1": _label()},
    ).checks.items[0]
    assert item.outcome == "unable_to_determine"


def test_claims_without_availability_wording_are_not_sent_to_the_model():
    seen = []
    reconcile_artifact_claims(
        _claims("Our method improves accuracy by 4 points."), _code(), classify=lambda rows: seen.extend(rows) or {},
    )
    assert seen == []


def test_unavailable_model_falls_back_to_the_sentence_rules(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("model unavailable")

    monkeypatch.setattr("app.credibility.release_classifier.classify_release_claims", broken)
    ctx = _context()
    calls: list[CredibilityModelCall] = []
    assert safe_classify(ctx, [{"claim_id": "C1", "statement": "x", "paper_quotes": ["x"]}], calls) is None
    item = reconcile_artifact_claims(
        _claims(), _code(), classify=lambda rows: safe_classify(ctx, rows, calls),
    ).checks.items[0]
    assert item.outcome == "supported"


class _Client:
    model_version = "offline-model"

    def __init__(self, payloads):
        self.payloads, self.prompts = list(payloads), []

    def complete_json(self, system, user, **kwargs):
        self.prompts.append(json.loads(user))
        return LLMResponse(text=self.payloads.pop(0), model_id="offline", model_version="offline-model")


def test_classifier_validates_ids_records_calls_and_reuses_its_checkpoint(monkeypatch):
    good = json.dumps({"claims": [_label().model_dump()]})
    client = _Client([json.dumps({"claims": []}), good])
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: client)
    monkeypatch.setattr("app.llm.retry.time.sleep", lambda _seconds: None)
    store = {}
    ctx = _context()
    ctx.model_id = "model_standard_v1"
    ctx.checkpoint_load = lambda purpose, fp: store.get((purpose, fp))
    ctx.checkpoint_save = lambda purpose, fp, payload: store.__setitem__((purpose, fp), payload)
    rows = [{"claim_id": "C1", "statement": _LORA, "paper_quotes": [_LORA]}]
    calls: list[CredibilityModelCall] = []
    labels = classify_release_claims(ctx, rows, calls)
    assert labels["C1"].artifacts == ["code"] and len(calls) == 2  # wrong IDs are retried once
    assert client.prompts[0]["claims"] == rows
    again: list[CredibilityModelCall] = []
    assert classify_release_claims(ctx, rows, again)["C1"] == labels["C1"] and len(again) == 2
    assert client.payloads == []  # the checkpoint answered without another model call


def test_assembly_uses_model_labels_and_reports_the_model_call(monkeypatch):
    monkeypatch.setattr(get_settings(), "credibility_release_classifier_enabled", True)
    payload = json.dumps({"claims": [_label(artifacts=["code", "model_weights"]).model_dump()]})
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: _Client([payload]))
    claim, code, references = _claims(_LORA), _code(), _references()
    ctx = _context()
    ctx.model_id = "model_standard_v1"
    report = assemble_report(
        ctx, report_id="release_report", rubric=load_credibility_rubric("0.2-provisional-complete"),
        checks=CheckSet(claims=lambda _ctx: claim, references=lambda _ctx: references, code=lambda _ctx: code),
    )[0]
    assert report.checks.claims.items[0].outcome == "partially_supported"
    assert report.dimensions[0].score == 7
    assert [call.purpose for call in report.versions.model_calls] == ["artifact_release_classification"]
