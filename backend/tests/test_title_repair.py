"""Garbled small-caps titles may change spacing and case only."""
from __future__ import annotations

import json

import pytest

from app.llm.base import LLMResponse
from app.parsing.base import ParsedDocument, TextBlock
from app.parsing.title_repair import needs_repair, repair_title
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper

GARBLED = "OPEN ESTIMATE : E VALUATING LLM S ON REASONING UNDER UNCERTAINTY WITH REAL -WORLD DATA"


def _doc(title):
    return ParsedDocument(
        paper=Paper(paper_id="p", title=title, parse_quality=ParseQuality.GOOD),
        blocks=[TextBlock(text=title, chunk_id="c1", page_start=1, section_role="front_matter")],
    )


class _Client:
    model_version = "offline"

    def __init__(self, title):
        self.title, self.calls = title, 0

    def complete_json(self, system, user, **kwargs):
        self.calls += 1
        return LLMResponse(text=json.dumps({"title": self.title}), model_id="m", model_version="offline")


@pytest.mark.parametrize("title,expected", [
    (GARBLED, True), ("Attention Is All You Need", False), ("LoRA: Low-Rank Adaptation of LLMs", False), (None, False),
])
def test_only_garbled_titles_are_sent(title, expected):
    assert needs_repair(title) is expected


def test_spacing_and_case_repair_is_accepted(monkeypatch):
    fixed = "OpenEstimate: Evaluating LLMs on Reasoning under Uncertainty with Real-World Data"
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: _Client(fixed))
    doc = _doc(GARBLED)
    repair_title(doc, "model_standard_v1")
    assert doc.paper.title == fixed


@pytest.mark.parametrize("answer", [
    "OpenEstimate: Evaluating Large Language Models on Reasoning under Uncertainty", "", None,
])
def test_changed_words_or_empty_answers_are_rejected(monkeypatch, answer):
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: _Client(answer))
    doc = _doc(GARBLED)
    repair_title(doc, "model_standard_v1")
    assert doc.paper.title == GARBLED


def test_model_failure_keeps_the_title(monkeypatch):
    def broken(model_id):
        raise RuntimeError("offline")

    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", broken)
    doc = _doc(GARBLED)
    repair_title(doc, "model_standard_v1")
    assert doc.paper.title == GARBLED


def test_clean_titles_never_call_the_model(monkeypatch):
    client = _Client("x")
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: client)
    repair_title(_doc("Attention Is All You Need"), "model_standard_v1")
    assert client.calls == 0
