from __future__ import annotations

from pathlib import Path

import pytest

from app.credibility.claim_model import _body_scope_caveats
from app.credibility.context import CheckContext
from app.parsing.generic import GenericPdfParser, PdfParseError


class _FakePage:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self) -> str:
        return self.text


class _FakeReader:
    is_encrypted = False
    metadata = {"/Title": "A General Machine Learning Paper"}

    def __init__(self, _: str):
        repeated = "We train a neural network on benchmark datasets and report controlled experiments. " * 6
        self.pages = [
            _FakePage(
                "ABSTRACT\nWe present a machine learning method with empirical evaluation.\n"
                "1 INTRODUCTION\n" + repeated + "\nhttps://github.com/example/project"
            ),
            _FakePage("2 EXPERIMENTS\n" + repeated + "\nTable 1: Main results"),
        ]


def _parser(tmp_path: Path) -> GenericPdfParser:
    return GenericPdfParser(
        arxiv_cache_dir=tmp_path / "arxiv",
        max_download_bytes=1024 * 1024,
        max_pages=10,
    )


def test_generic_parser_produces_current_contract(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-fake-for-reader")
    monkeypatch.setattr("app.parsing.generic.PdfReader", _FakeReader)

    doc = _parser(tmp_path).parse_pdf(str(pdf), arxiv_id="2501.01234v2")

    assert doc.paper.paper_id == "arxiv_2501_01234v2"
    assert doc.paper.arxiv_id == "2501.01234v2"
    assert doc.paper.title == "A General Machine Learning Paper"
    assert doc.paper.page_count == 2
    assert len(doc.blocks) >= 2
    assert max(len(block.text) for block in doc.blocks) <= 3600
    assert len({block.chunk_id for block in doc.blocks}) == len(doc.blocks)
    assert {block.page_start for block in doc.blocks} == {1, 2}
    assert any(element.label == "Table 1" for element in doc.elements)
    assert any(link.link_type == "code" for link in doc.links)
    assert doc.source_sha256
    assert doc.parser_name == "generic-pypdf-v4"


def test_generic_parser_rejects_insufficient_body(tmp_path, monkeypatch):
    class EmptyReader(_FakeReader):
        def __init__(self, _: str):
            self.pages = [_FakePage("short")]

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-fake-for-reader")
    monkeypatch.setattr("app.parsing.generic.PdfReader", EmptyReader)
    with pytest.raises(PdfParseError, match="too little extractable text"):
        _parser(tmp_path).parse_pdf(str(pdf))


BODY = "We train a neural network on benchmark datasets and protect privacy in controlled experiments. " * 6


def _parse_pages(tmp_path, monkeypatch, pages: list[str], metadata=None):
    class Reader(_FakeReader):
        def __init__(self, _: str):
            self.pages = [_FakePage(text) for text in pages]
            self.metadata = metadata or {}

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-fake-for-reader")
    monkeypatch.setattr("app.parsing.generic.PdfReader", Reader)
    return _parser(tmp_path).parse_pdf(str(pdf))


def test_venue_header_is_skipped_and_wrapped_title_word_is_rejoined(tmp_path, monkeypatch):
    header = "Published as a conference paper at ICLR 2025"
    doc = _parse_pages(tmp_path, monkeypatch, [
        f"{header}\nDOES TRAINING WITH SYNTHETIC DATA TRULY PRO-\nTECT PRIVACY?\nAlice Author\nABSTRACT\n{BODY}",
        f"{header}\n1 INTRODUCTION\n{BODY}\nTable 1: Main results",
    ])
    assert doc.paper.title == "DOES TRAINING WITH SYNTHETIC DATA TRULY PROTECT PRIVACY?"
    # A table caption alone keeps the body usable; the table element stays partial.
    assert doc.parse_quality.value == "good" and "TABLE_STRUCTURE_UNVERIFIED" in doc.warnings
    assert all(e.parse_quality.value == "partial" for e in doc.elements if e.element_type == "table")


def test_small_cap_title_letters_rejoin_only_into_words_the_paper_uses(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        "OPEN ESTIMATE : E VALUATING LLM S ON REASONING\nAlice Author\nABSTRACT\n"
        f"We are evaluating LLMs on reasoning. {BODY}",
        f"1 INTRODUCTION\n{BODY}",
    ])
    assert doc.paper.title == "OPEN ESTIMATE: EVALUATING LLM S ON REASONING"


def test_affiliations_years_table_headers_and_restarted_lists_are_not_sections(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        "A PAPER TITLE\n1 University of Science and Technology\n2 Shanghai Jiao Tong University\n"
        f"ABSTRACT\n{BODY}\n1 INTRODUCTION\n{BODY}\n2024 Summer Olympics and the French Revolution\n"
        f"3090 GPU. Code is available online\nMethods LR Iteration Outer Inner\n"
        f"Experimental Setup We conduct all experiments on CIFAR-10 (Krizhevsky, 2009),\n2 RELATEDWORK\n{BODY}",
        f"7 CONCLUSION\n{BODY}\n1. Reasoning first provide details\n{BODY}",
    ])
    titles = [section.title for section in doc.paper.sections]
    assert titles == ["Front Matter", "Abstract", "Introduction", "Relatedwork", "Conclusion"]
    assert next(b.section_role for b in doc.blocks if b.section_title == "Relatedwork") == "background"


def test_mixed_case_letter_appendix_headings_open_appendix_sections(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        f"ABSTRACT\n{BODY}\n1 INTRODUCTION\n{BODY}",
        "References\nAlice Author and Bob Writer. A paper title. In Proceedings, 2020.\n"
        f"A Discussion: Selection Mechanism\n{BODY}\nA detailed analysis of the results shows that the\n"
        f"E.2.1 Scaling Law Details\n{BODY}",
    ])
    appendix = [s for s in doc.paper.sections if s.title in {"Discussion: Selection Mechanism", "Scaling Law Details"}]
    assert [s.level for s in appendix] == [1, 3]
    assert {b.section_role for b in doc.blocks if b.section_title in {s.title for s in appendix}} == {"appendix"}
    assert all("detailed analysis" not in s.title for s in doc.paper.sections)


def test_long_page_without_blank_lines_keeps_its_line_breaks(tmp_path, monkeypatch):
    lines = [f"Author{i} Name and Other Person. Reference title number {i}. In Proceedings, 2020." for i in range(60)]
    doc = _parse_pages(tmp_path, monkeypatch, [f"ABSTRACT\n{BODY}", "References\n" + "\n".join(lines)])
    reference_blocks = [b for b in doc.blocks if b.section_role == "references"]
    assert len(reference_blocks) >= 2 and all(len(b.text) <= 3600 for b in reference_blocks)
    assert all("\n\n" not in b.text for b in reference_blocks)
    assert "\n".join(b.text for b in reference_blocks).count("\n") >= len(lines) - len(reference_blocks)


def test_nonstandard_body_sections_and_their_children_have_other_role(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        f"ABSTRACT\n{BODY}\n1 INTRODUCTION\n{BODY}\n1.1 Motivation\n{BODY}",
        f"2 State Space Models\n{BODY}\n2.1 Discretization\n{BODY}\n"
        f"3 Learningreversal At Theconceptlevel\n{BODY}\nReferences\nAlice Author, 2020.",
    ])
    roles = {block.section_title: block.section_role for block in doc.blocks}
    assert roles["Introduction"] == roles["Motivation"] == "introduction"
    assert roles["State Space Models"] == roles["Discretization"] == "other"
    assert roles["Learningreversal At Theconceptlevel"] == "other"
    assert roles["References"] == "references"
    ctx = CheckContext(
        source={}, repo_root=tmp_path, generated_at="2026-10-20T00:00:00Z",
        ref=None, doc=doc, unresolved_reason=None,
    )
    assert not any("unclassified" in caveat for caveat in _body_scope_caveats(ctx))


def test_nonstandard_sections_after_references_have_appendix_role(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        f"1 INTRODUCTION\n{BODY}\n2 State Space Models\n{BODY}",
        "References\nAlice Author, 2020.\n"
        f"3 Additional Analysis\n{BODY}\n3.1 Supporting Derivations\n{BODY}",
    ])
    roles = {block.section_title: block.section_role for block in doc.blocks}
    assert roles["References"] == "references"
    assert roles["Additional Analysis"] == roles["Supporting Derivations"] == "appendix"


def test_unclassified_sections_before_introduction_keep_their_original_roles(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        f"A PAPER TITLE\n1 University of Science\n{BODY}\nABSTRACT\n{BODY}\n"
        f"1 Author Details\n{BODY}\n2 INTRODUCTION\n{BODY}\n3 State Space Models\n{BODY}",
    ])
    roles = {block.section_title: block.section_role for block in doc.blocks}
    assert roles["Front Matter"] == "front_matter"
    assert roles["Abstract"] == "abstract"
    assert roles["Author Details"] == ""
    assert roles["State Space Models"] == "other"


def test_unclassified_sections_without_introduction_are_not_assumed_to_be_body(tmp_path, monkeypatch):
    doc = _parse_pages(tmp_path, monkeypatch, [
        f"ABSTRACT\n{BODY}\n1 METHODS\n{BODY}\n2 State Space Models\n{BODY}",
    ])
    roles = {block.section_title: block.section_role for block in doc.blocks}
    assert roles["Methods"] == "methods"
    assert roles["State Space Models"] == ""
