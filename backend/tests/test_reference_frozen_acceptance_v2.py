"""Offline acceptance against bibliography boundaries fixed from the source PDFs.

The fixtures contain the original input blocks, independent PDF-aligned spans,
and manually transcribed metadata. They are deliberately not generated from
``extract_reference_entries`` or Crossref candidates. The two Radford records
really have no cited year; the personal communication really has no DOI.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest

from app.credibility.context import CheckContext
from app.credibility.reference_extraction import extract_reference_entries, metadata_hints
from app.credibility.references import _LookupResult, check_references
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import Confidence, ParseQuality
from app.schemas.paper import Paper

FIXTURES = Path(__file__).parent / "fixtures"
GOLD = {
    "lora": json.loads((FIXTURES / "reference_gold_lora_v2.json").read_text()),
    "cot": json.loads((FIXTURES / "reference_gold_cot_v5.json").read_text()),
}


def _blocks(paper: str) -> list[TextBlock]:
    return [TextBlock(**{**block, "location_confidence": Confidence(block["location_confidence"])})
            for block in GOLD[paper]["blocks"]]


def _compact(value: str) -> str:
    """Ignore PDF typography, never words, name components, or numeric values."""
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(char for char in value if char.isalnum())


def _assert_metadata(actual: dict, expected: dict) -> None:
    # Spacing, ligatures, line hyphens, and accent glyphs vary between the two
    # original PDF text extractors. Every title letter and number must survive.
    title = _compact(actual["title"] or "")
    expected_title = _compact(expected["title"])
    if expected_title == "personalcommunication":
        # A communication has a date, not a separate publication title. Both
        # retaining and excluding its explicit date are conservative hints.
        assert title in {expected_title, "personalcommunicationmarch2024"}
    else:
        assert title == expected_title
    assert actual["authors"], expected
    assert _compact(actual["authors"][0]) == _compact(expected["first_author"])
    assert _compact(actual["authors"][-1]) == _compact(expected["last_author"])
    assert actual["year"] == expected["year"]
    assert actual["doi"] == expected["doi"]
    assert actual["arxiv_id"] == expected["arxiv_id"]


@pytest.mark.parametrize(("paper", "count"), [("lora", 62), ("cot", 30)])
def test_original_pdf_bibliography_boundaries_are_complete_and_exact(paper, count):
    entries, original_total, gaps = extract_reference_entries(_blocks(paper))
    assert len(GOLD[paper]["expected"]) == count
    assert len(entries) == original_total == count
    assert not gaps
    # Equality checks every original source character in every fragment. A
    # count-only test would miss merged entries balanced by spurious tails.
    for entry, expected in zip(entries, GOLD[paper]["expected"], strict=True):
        actual = [{"chunk_id": fragment.chunk_id, "text": fragment.text} for fragment in entry.fragments]
        assert actual == expected["fragments"], (paper, expected["index"])
        assert not re.search(r"\n(?:13|14|15)\s*$", entry.fragments[-1].text)


@pytest.mark.parametrize(
    ("paper", "index"),
    [(paper, i) for paper, gold in GOLD.items() for i in range(len(gold["expected"]))],
    ids=[f"{paper}-{i + 1:02d}" for paper, gold in GOLD.items() for i in range(len(gold["expected"]))],
)
def test_metadata_hints_against_manually_transcribed_pdf_bibliography(paper, index):
    expected = GOLD[paper]["expected"][index]
    original_text = "\n".join(fragment["text"] for fragment in expected["fragments"])
    _assert_metadata(metadata_hints(original_text), expected["metadata"])


@pytest.mark.parametrize(("paper", "count"), [("lora", 62), ("cot", 30)])
def test_reference_pipeline_queries_every_complete_source_entry_without_network(monkeypatch, paper, count):
    class OfflineLookup:
        calls: list = []

        def lookup(self, metadata):
            self.calls.append(metadata)
            return _LookupResult(
                url="https://api.crossref.org/works?query=offline-acceptance",
                fetch_status="ok",
                http_status=200,
                retrieved_at="2026-10-19T00:00:00Z",
            )

    lookup = OfflineLookup()
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    blocks = _blocks(paper)
    ctx = CheckContext(
        source={}, repo_root=Path("."), generated_at="2026-10-19T00:00:00Z", ref=None,
        doc=ParsedDocument(
            paper=Paper(paper_id=paper, parse_quality=ParseQuality.GOOD),
            blocks=blocks,
        ),
        unresolved_reason=None,
    )
    result = check_references(ctx)
    assert result.checks.extracted_count == result.checks.original_total == len(lookup.calls) == count
    assert result.checks.checked_count == result.checks.unverified_count == count
    assert result.checks.extraction_status == "complete"
    assert result.checks.not_checked_count == 0
    evidence = {item.evidence_id: item for item in result.evidence}
    for item, metadata, expected in zip(result.checks.items, lookup.calls, GOLD[paper]["expected"], strict=True):
        _assert_metadata(metadata.model_dump(), expected["metadata"])
        original_fragments = expected["fragments"]
        assert len(item.paper_evidence_ids) == len(original_fragments)
        for evidence_id, original in zip(item.paper_evidence_ids, original_fragments, strict=True):
            span = evidence[evidence_id]
            assert span.paper_locator.chunk_id == original["chunk_id"]
            assert span.excerpt == original["text"]


def test_lora_previously_merged_records_are_separate_and_yearless_records_stay_yearless():
    entries, _, _ = extract_reference_entries(_blocks("lora"))
    assert len(entries) == 62
    for one_based_index, prefix in [
        (43, "Daniel Povey"), (45, "Alec Radford, Jeffrey Wu"),
        (46, "Pranav Rajpurkar"), (49, "Tara N Sainath"), (53, "Alex Wang, Amanpreet Singh"),
    ]:
        assert entries[one_based_index - 1].text.startswith(prefix)
    for one_based_index in [44, 45]:
        assert metadata_hints(entries[one_based_index - 1].text)["year"] is None
