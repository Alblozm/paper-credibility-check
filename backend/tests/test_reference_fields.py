"""Model-split reference fields are used only where they are printed in the entry."""
from __future__ import annotations

import json

from app.credibility.reference_extraction import metadata_hints
from app.credibility.reference_fields import ReferenceFields, _grounded, merge_hints, parse_reference_fields
from app.llm.base import LLMResponse
from tests.test_reconcile import _context

ODDS = (
    "Akshay Paruchuri, Jake Garrison, Shun Liao, John Hernandez, Jacob Sunshine, Tim Althoff, Xin Liu, and "
    "Daniel McDuff. What are the odds? language models are capable of probabilistic reasoning. arXiv preprint "
    "arXiv:2406.12830, 2024."
)
GLUED = (
    "Philipp Schoenegger, Peter S Park, Ezra Karger, Sean Trott, and Philip E Tetlock. AI-augmented "
    "predictions: LLM assistants improve human forecasting accuracy.ACM Transactions on Interactive "
    "Intelligent Systems, 15(1):1–25, 2025."
)


def test_sentence_rules_cut_or_glue_these_titles():
    assert metadata_hints(ODDS)["title"] == "What are the odds?"
    assert "ACM Transactions" in metadata_hints(GLUED)["title"]


def test_printed_fields_are_kept_and_merged():
    row = ReferenceFields(
        entry_id="E1", title="What are the odds? language models are capable of probabilistic reasoning",
        authors=["Akshay Paruchuri", "Daniel McDuff"], year=2024, arxiv_id="2406.12830",
    )
    fields = _grounded(row, ODDS)
    merged = merge_hints(metadata_hints(ODDS), fields)
    assert merged["title"].endswith("probabilistic reasoning") and merged["arxiv_id"] == "2406.12830"
    assert merged["year"] == 2024


def test_unprinted_title_authors_or_ids_are_dropped():
    row = ReferenceFields(
        entry_id="E1", title="What Are the Odds? Language Models Are Capable of Probabilistic Reasoning in Practice",
        authors=["Someone Else"], year=2023, doi="10.18653/v1/2024.emnlp-main.654", arxiv_id="2406.99999",
    )
    assert _grounded(row, ODDS) == {"title": None, "authors": [], "year": None, "doi": None, "arxiv_id": None}
    rules = metadata_hints(ODDS)
    assert merge_hints(rules, _grounded(row, ODDS)) == rules


class _Client:
    model_version = "offline"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def complete_json(self, system, user, **kwargs):
        self.prompts.append(json.loads(user))
        entries = [{"entry_id": row["entry_id"], **self.reply(row["text"])} for row in self.prompts[-1]["entries"]]
        return LLMResponse(text=json.dumps({"entries": entries}), model_id="m", model_version="offline")


def test_entries_are_batched_and_calls_recorded(monkeypatch):
    def reply(text):
        return {"title": "AI-augmented predictions: LLM assistants improve human forecasting accuracy"
                if text == GLUED else None, "authors": [], "year": None}

    client = _Client(reply)
    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", lambda model_id: client)
    monkeypatch.setattr("app.credibility.reference_fields.BATCH_SIZE", 2)
    ctx = _context()
    ctx.model_id = "model_standard_v1"
    calls = []
    fields = parse_reference_fields(ctx, [GLUED, ODDS, GLUED], calls)
    assert len(client.prompts) == 2 and len(calls) == 2
    assert fields[0]["title"].endswith("forecasting accuracy") and fields[1]["title"] is None
    assert fields[2]["title"] == fields[0]["title"]


def test_failed_model_keeps_the_sentence_rules(monkeypatch):
    def broken(model_id):
        raise ValueError("offline")

    monkeypatch.setattr("app.pipeline.orchestrator.build_llm_client", broken)
    ctx = _context()
    ctx.model_id = "model_standard_v1"
    assert parse_reference_fields(ctx, [ODDS], []) is None


def test_one_letter_surname_typos_agree_only_with_matching_given_names():
    from app.credibility.reference_matching import _authors_agree

    cited = ["Rohan Taori", "Ludwig Schimdt"]
    assert _authors_agree(cited, ["Rohan Taori", "Ludwig Schmidt"], complete=True) is True
    assert _authors_agree(["Ludwig Schimdt"], ["Peter Schmidt"], complete=True) is False
    assert _authors_agree(["Ann Lee"], ["Ann Lei"], complete=True) is False  # short surnames stay exact
    assert _authors_agree(["Jane Smith"], ["Jane Smythe"], complete=True) is False
