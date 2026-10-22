from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.credibility.context import CheckContext, build_context
from app.credibility.locate import PaperSpanCandidate, locate_paper_span
from app.parsing.base import ParsedDocument, TextBlock
from app.parsing.generic import GenericPdfParser, _heading
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper


def _document(*, digest: str | None = "a" * 64, arxiv_id: str | None = None) -> ParsedDocument:
    return ParsedDocument(
        paper=Paper(paper_id="new_live_paper", arxiv_id=arxiv_id, parse_quality=ParseQuality.GOOD),
        blocks=[TextBlock(text="A newly parsed claim.", page_start=1, chunk_id="chunk_1")],
        source_sha256=digest,
        parser_name="offline-test-parser",
    )


def _context(doc: ParsedDocument, **kwargs) -> CheckContext:
    return CheckContext(
        source={"type": "pdf", "upload_id": "upl_test"},
        repo_root=Path("."),
        generated_at="2026-10-19T00:00:00Z",
        ref=None,
        doc=doc,
        unresolved_reason=None,
        **kwargs,
    )


def test_live_version_is_stable_and_cannot_be_mistaken_for_prepared_benchmark():
    ctx = _context(_document(), model_id="deepseek-flash")
    version = ctx.paper_version_id
    assert version == "live:new_live_paper:sha256:" + "a" * 64
    assert ctx.model_id == "deepseek-flash"
    ctx.generated_at = "another invocation"
    ctx.doc.source_path = "/another/upload/path.pdf"
    ctx.source["upload_id"] = "upl_reuploaded"
    assert ctx.paper_version_id == version
    assert _context(_document(digest="b" * 64)).paper_version_id != version
    assert _context(_document()).model_id is None


def test_live_version_keeps_explicit_arxiv_revision_and_pdf_digest():
    v1 = _context(_document(arxiv_id="2501.01234v1"))
    v2 = _context(_document(arxiv_id="2501.01234v2"))
    assert v1.paper_version_id.startswith("live:arxiv:2501.01234v1:sha256:")
    assert v2.paper_version_id != v1.paper_version_id
    v1.doc.source_sha256 = "b" * 64
    assert v1.paper_version_id != _context(_document(arxiv_id="2501.01234v1")).paper_version_id
    source_version = _context(_document(arxiv_id="2501.01234"))
    source_version.source = {"type": "arxiv", "arxiv_id": "https://arxiv.org/pdf/2501.01234V3.PDF"}
    assert source_version.paper_version_id.startswith("live:arxiv:2501.01234v3:sha256:")


def test_live_version_without_source_digest_hashes_actual_content():
    ctx = _context(_document(digest=None))
    version = ctx.paper_version_id
    assert version and version.startswith("live:")
    assert _context(_document(digest=None)).paper_version_id == version
    ctx.doc.blocks[0].text = "A different paper despite the same parsed paper id."
    assert ctx.paper_version_id != version
    ctx.doc = None
    assert ctx.paper_version_id is None


class _Page:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self) -> str:
        return self.text


def _parse_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *pages: str) -> ParsedDocument:
    class Reader:
        is_encrypted = False
        metadata = {"/Title": "An Offline Sectioned Paper"}

        def __init__(self, _path):
            self.pages = [_Page(page) for page in pages]

    monkeypatch.setattr("app.parsing.generic.PdfReader", Reader)
    pdf = tmp_path / "new.pdf"
    pdf.write_bytes(b"%PDF-offline-new-paper")
    return GenericPdfParser(
        arxiv_cache_dir=tmp_path / "cache", max_download_bytes=1024, max_pages=10
    ).parse_pdf(str(pdf))


@pytest.mark.parametrize("heading", ["I INTRODUCTION", "II INTRODUCTION", "I. INTRODUCTION", "I NTRODUCTION"])
def test_roman_section_numbers_and_small_cap_intro_both_keep_claim_sections(tmp_path, monkeypatch, heading):
    assert _heading(heading) == (1, "Introduction")
    body = "We introduce a machine learning method and evaluate it under controlled conditions. " * 8
    doc = _parse_text(tmp_path, monkeypatch, "AN ORDINARY PAPER\nAuthor One\n" + heading + "\n" + body)
    intro = next(block for block in doc.blocks if block.section_role == "introduction")
    assert intro.text.startswith(heading + "\n")
    assert intro.section_title == "Introduction"
    assert doc.blocks[0].section_role == "front_matter"


@pytest.mark.parametrize("title", ["I UNDERSTAND LANGUAGE MODELS", "I Learn Robust Models", "IMAGE CLASSIFICATION"])
def test_i_prefixed_paper_titles_are_not_mistaken_for_roman_sections(title):
    assert _heading(title) is None


def test_live_parse_assigns_section_and_element_roles_without_changing_quotes(tmp_path, monkeypatch):
    text = (
        "AN OFFLINE SECTIONED PAPER\nAuthor One, University Two\n"
        "ABSTRACT\nOur model improves accuracy on DatasetA from 80 to 84.\n"
        "1 I NTRODUCTION\nWe introduce a method with a controlled evaluation.\n"
        "1.1 Contributions\nOur contribution is a validated model.\n"
        "2 RELATED WORK\nOther researchers report a 99 percent result.\n"
        "2.1 Prior Models\nTable 9: Previously reported performance.\n"
        "3 METHODS\nWe define the learning procedure and its objective.\n"
        "3.1 Architecture\nThe architecture has two layers with shared weights.\n"
        "4 EXPERIMENTS\nOur experiments use the same benchmark and budget.\n"
        "4.1 Evaluation Protocol\nTable 1: Our method reaches 84 accuracy.\n"
        "5 RESULTS\nThe measured result exceeds the baseline.\n"
        "6 DISCUSSION\nWe discuss the observed effects and tradeoffs.\n"
        "7 CONCLUSIONS\nWe conclude that the method is effective.\n"
        "REFERENCES\nA. Author. A study of machine learning. 2024.\n"
        "A ADDITIONAL ANALYSIS\nAdditional runs show similar measured effects.\n"
    )
    doc = _parse_text(tmp_path, monkeypatch, text)
    roles = {block.section_title: block.section_role for block in doc.blocks}
    assert roles == {
        "Front Matter": "front_matter", "Abstract": "abstract", "Introduction": "introduction",
        "Contributions": "introduction", "Related Work": "background", "Prior Models": "background",
        "Methods": "methods", "Architecture": "methods", "Experiments": "experiments",
        "Evaluation Protocol": "experiments", "Results": "results", "Discussion": "discussion",
        "Conclusions": "conclusion", "References": "references", "Additional Analysis": "appendix",
    }
    assert "Author One, University Two" not in (doc.paper.abstract or "")
    intro = next(block for block in doc.blocks if block.section_title == "Introduction")
    assert intro.text.startswith("1 I NTRODUCTION\n")
    assert "Our model improves accuracy on DatasetA from 80 to 84." in doc.paper.abstract
    assert {element.label: element.section_role for element in doc.elements} == {
        "Table 9": "background", "Table 1": "experiments"
    }
    assert doc.elements[1].caption == "Table 1: Our method reaches 84 accuracy."
    appendix = next(block for block in doc.blocks if block.section_role == "appendix")
    assert appendix.section_title == "Additional Analysis"
    assert appendix.text.startswith("A ADDITIONAL ANALYSIS\n")


def test_nested_roles_continue_across_pages_and_bibliography_is_isolated(tmp_path, monkeypatch):
    body = "We train and evaluate a model on a controlled benchmark with fixed settings. " * 8
    doc = _parse_text(
        tmp_path, monkeypatch,
        "I. INTRODUCTION\nA claim about a new model.\nII. METHODS\n2.1 Training\n" + body,
        "The same training continues on this page.\nIII. RESULTS\n" + body
        + "\nBIBLIOGRAPHY\n3.1 Index\nTable 8: A cited paper's result.",
    )
    assert next(block for block in doc.blocks if block.page_start == 2).section_role == "methods"
    bibliography = [block for block in doc.blocks if block.section_title == "Index"]
    assert bibliography and bibliography[0].section_role == "references"
    assert doc.elements[0].section_role == "references"
    ctx = _context(doc)
    for block in doc.blocks:
        if block.section_role in {"introduction", "references"}:
            span = PaperSpanCandidate(evidence_id="test", mode="quote", excerpt=block.text, chunk_id=block.chunk_id)
            located, problems = locate_paper_span(ctx, span, forbidden_roles={"introduction", "references"})
            assert located is None and problems
    span = PaperSpanCandidate(
        evidence_id="cited_table", mode="quote", excerpt=doc.elements[0].caption,
        element_id=doc.elements[0].element_id,
    )
    assert locate_paper_span(ctx, span, forbidden_roles={"references"})[0] is None


def _benchmark(root: Path, layout: str, *, paper_id: str = "paper_901", arxiv_id: str = "2501.01234") -> None:
    data = root / layout
    parsed = data / "parsed" / paper_id / "v1"
    parsed.mkdir(parents=True)
    (parsed / "metadata.json").write_text(json.dumps({
        "paper_id": paper_id, "paper_version_id": f"{paper_id}_v1", "page_count": 1, "parse_quality": "good",
    }))
    (parsed / "sections.json").write_text(json.dumps([
        {"section_id": "abstract", "title": "Abstract", "section_role": "abstract", "level": 1}
    ]))
    (parsed / "chunks.jsonl").write_text(json.dumps({
        "chunk_id": "chunk_0", "section_id": "abstract", "page_start": 1, "text_content": "A benchmark claim."
    }) + "\n")
    snapshot = data / "source_snapshots" / paper_id
    snapshot.mkdir(parents=True)
    (snapshot / "source_record.json").write_text(json.dumps({"arxiv_id": arxiv_id, "arxiv_version": "v1"}))


@pytest.mark.parametrize("layout", ["data/data", "benchmark/data"])
def test_context_discovers_current_and_historical_benchmark_layouts(tmp_path, layout):
    _benchmark(tmp_path, layout)
    ctx = build_context({"type": "arxiv", "arxiv_id": "2501.01234v1"}, generated_at="t", repo_root=tmp_path)
    assert ctx.ref is not None and ctx.ref.parsed_dir.is_relative_to(tmp_path / layout)
    assert ctx.paper_version_id == "paper_901_v1"
    assert ctx.doc.blocks[0].section_role == "abstract"


def test_explicit_repo_root_does_not_search_sibling_or_production_data(tmp_path, monkeypatch):
    _benchmark(tmp_path, "data/data")
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    monkeypatch.setattr(get_settings(), "parser_provider", "stub")
    ctx = build_context({"type": "arxiv", "arxiv_id": "2501.01234v1"}, generated_at="t", repo_root=isolated)
    assert ctx.ref is None and ctx.doc is None
    assert ctx.repo_root == isolated
    assert "not available" in ctx.unresolved_reason


def test_both_layouts_can_coexist_without_hiding_historical_papers(tmp_path):
    _benchmark(tmp_path, "data/data", arxiv_id="2501.01234")
    _benchmark(tmp_path, "benchmark/data", paper_id="paper_902", arxiv_id="2502.01234")
    ctx = build_context({"type": "arxiv", "arxiv_id": "2502.01234v1"}, generated_at="t", repo_root=tmp_path)
    assert ctx.paper_version_id == "paper_902_v1"


def test_default_live_parse_ignores_a_matching_benchmark_paper(tmp_path, monkeypatch):
    import app.credibility.context as context_module

    _benchmark(tmp_path, "data/data")
    monkeypatch.setattr(get_settings(), "credibility_parse_source", "live")
    reasons = []

    def live(source, reason):
        reasons.append(reason)
        return _document(arxiv_id="2501.01234v1"), None

    monkeypatch.setattr(context_module, "_parse_live", live)
    ctx = build_context({"type": "arxiv", "arxiv_id": "2501.01234v1"}, generated_at="t", repo_root=tmp_path)
    assert ctx.ref is None and ctx.doc.parser_name == "offline-test-parser"
    assert ctx.paper_version_id.startswith("live:arxiv:2501.01234v1:")
    assert reasons and "credibility_parse_source=live" in reasons[0]


def test_default_live_parse_never_falls_back_to_benchmark_when_live_parsing_is_off(tmp_path, monkeypatch):
    _benchmark(tmp_path, "data/data")
    monkeypatch.setattr(get_settings(), "credibility_parse_source", "live")
    monkeypatch.setattr(get_settings(), "parser_provider", "stub")
    ctx = build_context({"type": "arxiv", "arxiv_id": "2501.01234v1"}, generated_at="t", repo_root=tmp_path)
    assert ctx.ref is None and ctx.doc is None
    assert "Live parsing is not enabled" in ctx.unresolved_reason


def test_parse_source_is_part_of_the_request_fingerprint(monkeypatch):
    from app.credibility import runtime

    source = {"type": "arxiv", "arxiv_id": "2501.01234v1", "upload_id": None}
    fingerprints = {}
    for mode in ("live", "benchmark"):
        monkeypatch.setattr(get_settings(), "credibility_parse_source", mode)
        versions = runtime.request_versions()
        assert versions["parse_source"] == mode
        fingerprints[mode] = runtime.request_fingerprint(source, "model_standard_v1", "0.1-draft", versions)
    assert fingerprints["live"] != fingerprints["benchmark"]
