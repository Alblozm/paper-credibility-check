"""Regression cases for evidence locations in ordinary PDF extraction output."""
from __future__ import annotations

import re
from pathlib import Path

from app.parsing.anchors import build_anchored_text, collect_anchors
from app.parsing.generic import GenericPdfParser


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self, *args, **kwargs) -> str:
        return self.text


_BODY = (
    "We compare the initial answers with revised answers under controlled conditions. "
    "The observed results apply to the tested models and benchmarks. "
) * 5


def _parse_pages(tmp_path: Path, monkeypatch, page_texts: list[str]):
    class Reader:
        is_encrypted = False
        metadata = {}

        def __init__(self, path: str) -> None:
            self.pages = [_Page(text) for text in page_texts]

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-independent-parser-regression")
    monkeypatch.setattr("app.parsing.generic.PdfReader", Reader)
    parser = GenericPdfParser(
        arxiv_cache_dir=tmp_path / "arxiv",
        max_download_bytes=1024 * 1024,
        max_pages=20,
    )
    return parser.parse_pdf(str(pdf), arxiv_id="2310.01798v1")


def _block_with(doc, text: str):
    matches = [block for block in doc.blocks if text in block.text]
    assert len(matches) == 1, f"Expected one block containing {text!r}"
    return matches[0]


def test_multiline_title_and_small_caps_abstract_keep_separate_sections(tmp_path, monkeypatch):
    abstract = (
        "We investigate whether a model can revise its own reasoning without external feedback. "
        "Under the tested conditions its performance can decline after self-correction."
    )
    doc = _parse_pages(
        tmp_path,
        monkeypatch,
        [
            "LARGE LANGUAGE MODELS CANNOT SELF-CORRECT\n"
            "REASONING YET\n"
            "Jie Huang and collaborators\n"
            "ABSTRACT\n"
            f"{abstract}\n"
            "1 I NTRODUCTION\n"
            "Introduction-only discussion begins here.\n"
            f"{_BODY}",
        ],
    )

    assert re.sub(r"\s+", " ", doc.paper.title or "").upper() == (
        "LARGE LANGUAGE MODELS CANNOT SELF-CORRECT REASONING YET"
    )
    assert doc.paper.abstract is not None
    assert abstract in doc.paper.abstract
    assert "Introduction-only discussion" not in doc.paper.abstract

    abstract_block = _block_with(doc, abstract)
    introduction_block = _block_with(doc, "Introduction-only discussion begins here.")
    assert abstract_block.section_title.casefold() == "abstract"
    assert introduction_block.section_title.casefold() == "introduction"
    assert abstract_block.section_id != introduction_block.section_id
    assert abstract_block.pages == introduction_block.pages == {1}


def _table_case(tmp_path, monkeypatch):
    return _parse_pages(
        tmp_path,
        monkeypatch,
        [
            "SELF-CORRECTION STUDY\nABSTRACT\n" + _BODY + "\n1 INTRODUCTION\n" + _BODY,
            "2 BACKGROUND\n" + _BODY,
            "3.1 EXPERIMENTAL SETUP\n"
            "GPT-4 was accessed on 2023/08/29. We randomly sample 200 GSM8K questions.\n"
            + _BODY,
            "3.2 I NTRINSIC SELF-CORRECTION\n"
            "We remove the use of labels to determine when to stop.\n" + _BODY,
            "Table 3: Accuracy after intrinsic self-correction.\n"
            "# calls GSM8K CommonSenseQA HotpotQA\n"
            "GPT-4\n"
            "Standard Prompting 1 95.5 82.0 49.0\n"
            "Self-Correct (round 1) 3 91.5 79.5 49.0\n"
            "Self-Correct (round 2) 5 89.0 80.0 43.0\n"
            "GSM8K\n"
            "Results-only discussion concludes here.\n"
            "3.2.1 WHY DOES THE PERFORMANCE DECREASE?\n"
            "Analysis-only discussion begins here.\n" + _BODY,
        ],
    )


def test_table_labels_do_not_become_sections_and_page_continuation_keeps_owner(tmp_path, monkeypatch):
    doc = _table_case(tmp_path, monkeypatch)

    assert not any(section.title.casefold() in {"gpt-4", "gsm8k"} for section in doc.paper.sections)
    condition = _block_with(doc, "We remove the use of labels to determine when to stop.")
    results = _block_with(doc, "Results-only discussion concludes here.")
    analysis = _block_with(doc, "Analysis-only discussion begins here.")
    assert condition.section_id == results.section_id
    assert "intrinsic" in results.section_title.casefold()
    assert results.pages == analysis.pages == {5}
    assert results.section_id != analysis.section_id
    assert "decrease" in analysis.section_title.casefold()
    assert "Analysis-only discussion" not in results.text

    table = next(element for element in doc.elements if element.label == "Table 3")
    assert table.page_number == 5
    assert table.section_id == condition.section_id


def test_body_references_cannot_claim_caption_pages(tmp_path, monkeypatch):
    doc = _parse_pages(
        tmp_path,
        monkeypatch,
        [
            "EVIDENCE LOCATION STUDY\n1 INTRODUCTION\n"
            "Table 6 is discussed below, with two examples illustrated in Figure 2.\n"
            "We also discuss Figure 9, which has no caption in this document.\n" + _BODY,
            "2 ADDITIONAL RESULTS\n"
            "Table 6: Results with different feedback prompts.\n"
            "Standard Prompting 75.9\n"
            "Figure 2. Examples of successful and failed self-correction.\n" + _BODY,
        ],
    )

    by_label = {element.label: element for element in doc.elements}
    assert by_label["Table 6"].page_number == 2
    assert by_label["Figure 2"].page_number == 2
    assert "different feedback prompts" in (by_label["Table 6"].caption or "")
    assert "successful and failed" in (by_label["Figure 2"].caption or "")
    assert "Figure 9" not in by_label
    assert doc.build_index().element_pages[("table", 6)] == 2
    assert doc.build_index().element_pages[("figure", 2)] == 2


def test_numeric_rows_and_conditions_remain_in_actual_page_anchors(tmp_path, monkeypatch):
    doc = _table_case(tmp_path, monkeypatch)
    rows = (
        "Standard Prompting 1 95.5 82.0 49.0",
        "Self-Correct (round 1) 3 91.5 79.5 49.0",
        "Self-Correct (round 2) 5 89.0 80.0 43.0",
    )
    anchors = collect_anchors(build_anchored_text(doc))
    for row in rows:
        block = _block_with(doc, row)
        assert block.pages == {5}
        assert (5, block.chunk_id) in anchors
    assert _block_with(doc, "200 GSM8K questions").pages == {3}
    assert _block_with(doc, "remove the use of labels").pages == {4}
    assert len({block.chunk_id for block in doc.blocks}) == len(doc.blocks)
    assert doc.known_pages == {1, 2, 3, 4, 5}


def test_unnumbered_small_caps_abstract_and_introduction_keep_their_initial_letters(tmp_path, monkeypatch):
    abstract = (
        "We study intrinsic self-correction using controlled reasoning benchmarks. "
        "Performance may decline when no external feedback is available."
    )
    doc = _parse_pages(
        tmp_path,
        monkeypatch,
        [
            "REASONING WITH SELF-CORRECTION\nAuthors\n"
            "A BSTRACT\n"
            f"{abstract}\n"
            "I NTRODUCTION\n"
            "Introduction-only motivation appears here.\n" + _BODY,
        ],
    )

    assert doc.paper.abstract == abstract
    abstract_block = _block_with(doc, abstract)
    introduction_block = _block_with(doc, "Introduction-only motivation appears here.")
    assert abstract_block.section_title.casefold() == "abstract"
    assert introduction_block.section_title.casefold() == "introduction"
    assert abstract_block.section_id != introduction_block.section_id
    assert "Introduction-only motivation" not in abstract_block.text
    assert abstract_block.pages == introduction_block.pages == {1}


def test_article_a_in_title_is_not_an_appendix_but_post_references_letter_heading_is(tmp_path, monkeypatch):
    doc = _parse_pages(
        tmp_path,
        monkeypatch,
        [
            "A METHOD\nFOR ROBUST REASONING\nAuthors\n"
            "ABSTRACT\n" + _BODY + "\n1 INTRODUCTION\n" + _BODY,
            "REFERENCES\nReference-only entry appears here.\n"
            "A ADDITIONAL RESULTS\nAppendix-only result appears here.\n" + _BODY,
        ],
    )

    assert doc.paper.title == "A METHOD FOR ROBUST REASONING"
    assert not any(section.title.casefold() in {"method", "appendix"} for section in doc.paper.sections)
    title_block = _block_with(doc, "A METHOD\nFOR ROBUST REASONING")
    assert title_block.section_title == "Front Matter"
    references_block = _block_with(doc, "Reference-only entry appears here.")
    appendix_block = _block_with(doc, "Appendix-only result appears here.")
    assert references_block.section_title.casefold() == "references"
    assert appendix_block.section_title.casefold() == "additional results"
    assert references_block.section_id != appendix_block.section_id
    assert references_block.pages == appendix_block.pages == {2}
