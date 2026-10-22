"""Reference boundary, source locator and query-hint regressions; all offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.credibility.context import CheckContext
from app.credibility.reference_extraction import extract_reference_entries, metadata_hints
from app.credibility.references import CrossrefLookup, _LookupResult, check_references
from app.parsing.base import ParsedDocument, TextBlock
from app.parsing.benchmark import load_parsed_document
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper


def block(text: str, chunk_id: str = "refs_1", page: int = 10) -> TextBlock:
    return TextBlock(
        text=text, chunk_id=chunk_id, page_start=page, section_role="references", section_title="References"
    )


def context(*blocks: TextBlock, doc: ParsedDocument | None = None) -> CheckContext:
    return CheckContext(
        source={}, repo_root=Path("."), generated_at="2026-10-19T00:00:00Z", ref=None,
        doc=doc or ParsedDocument(paper=Paper(paper_id="test", parse_quality=ParseQuality.GOOD), blocks=list(blocks)),
        unresolved_reason=None,
    )


class RecordingLookup:
    def __init__(self, status: str = "ok"):
        self.calls: list = []
        self.status = status

    def lookup(self, metadata):
        self.calls.append(metadata)
        return _LookupResult("https://api.crossref.org/works?query=test", self.status,
                             200 if self.status == "ok" else None, "2026-10-19T00:00:00Z")


@pytest.mark.parametrize(
    ("raw", "title", "year"),
    [
        ("Jane Doe. A useful method. In Proceedings of a Conference, 2024.", "A useful method", 2024),
        ("Jane Doe. 2024a. A useful method. Journal of Tests.", "A useful method", 2024),
        ("Doe, J. and Roe, R. A useful method. Journal of Tests, 2024.", "A useful method", 2024),
        ("Doe, J. (2005), ‘The 2004 election’, In Proceedings of a Conference.", "The 2004 election", 2005),
        ("Jane Doe. Gradients as features. arXiv preprint arXiv:2004.05529, 2020.", "Gradients as features", 2020),
        ("Jane Doe. A useful method. A Journal, 33:1877–1901, 2020.", "A useful method", 2020),
        ("Jane Doe. A useful method. In Proceedings, pp. 2038–2047, 2022.", "A useful method", 2022),
        ("Jane Doe and John Roe. A useful method. In A Conference.", "A useful method", None),
        ('Jane Doe. “A useful method”. In: A Journal (2024), pp. 12–20.', "A useful method", 2024),
        ("OpenAI. Gpt-4 technical report, 2023.", "Gpt-4 technical report", 2023),
        ("Jane Doe. Why do transformers work? In Proceedings of a Conference, 2024.",
         "Why do transformers work?", 2024),
        ("Jane Doe. A useful method. A Journal, 2024. Available at https://example.org (accessed 2026).",
         "A useful method", 2024),
    ],
)
def test_metadata_title_is_not_the_venue_or_an_identifier_year(raw, title, year):
    hints = metadata_hints(raw)
    assert hints["title"] == title
    assert hints["year"] == year


def test_unknown_title_is_not_invented_for_a_doi_record():
    hints = metadata_hints("doi:10.1000/example")
    assert hints["doi"] == "10.1000/example"
    assert hints["title"] is None


def test_author_paragraphs_ignore_a_trailing_supplement_heading():
    entries, total, gaps = extract_reference_entries([block(
        "References\n\nJane Doe. First useful method. A Journal, 2024.\n\n"
        "John Roe. Second useful method. A Conference, 2023.\n\nSupplementary Material\n\nNot a citation."
    )])
    assert len(entries) == total == 2 and not gaps
    assert "Supplementary" not in entries[-1].text


def test_inline_bracket_numbers_and_line_numbered_lists():
    for text in [
        "[1] Jane Doe. First method. 2024. [2] John Roe. Second method. 2023.",
        "1. Jane Doe. First method. 2024.\n2. John Roe. Second method. 2023.",
    ]:
        entries, total, gaps = extract_reference_entries([block(text)])
        assert len(entries) == total == 2 and not gaps
        assert "Second method" not in entries[0].text


def test_numbering_gaps_do_not_claim_complete_extraction():
    entries, total, gaps = extract_reference_entries([
        block("[1] Jane Doe. First method. 2024.\n[3] John Roe. Third method. 2023.")
    ])
    assert len(entries) == 2 and total is None
    assert any("numbering" in gap for gap in gaps)


@pytest.mark.parametrize("numbered", [False, True])
def test_cross_chunk_reference_preserves_each_source_fragment(monkeypatch, numbered):
    first = ("[1] " if numbered else "") + "Jane Doe. A useful method with a wrapped"
    second = "title. A Journal, 2024.\n\n" + ("[2] " if numbered else "") + "John Roe. Another method. 2023."
    ctx = context(block(first), block(second, "refs_2", 11))
    lookup = RecordingLookup()
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    result = check_references(ctx)
    assert result.checks.extracted_count == result.checks.checked_count == 2
    assert result.checks.extraction_status == "complete"
    item = result.checks.items[0]
    assert item.paper_evidence_ids == ["ev_ref_001_paper_1", "ev_ref_001_paper_2"]
    evidence = {e.evidence_id: e for e in result.evidence}
    for evidence_id, chunk_id, page in zip(item.paper_evidence_ids, ["refs_1", "refs_2"], [10, 11], strict=True):
        located = evidence[evidence_id]
        assert located.paper_locator.chunk_id == chunk_id and located.paper_locator.page_start == page
        assert located.excerpt in ctx.block(chunk_id).text
    assert len(lookup.calls) == 2


def test_ambiguous_unseparated_citations_remain_a_gap():
    entries, total, gaps = extract_reference_entries([
        block("Jane Doe, A Good Paper, 2024; John Roe, Another Paper, 2023.")
    ])
    assert not entries and total is None and gaps


def test_year_on_a_wrapped_line_is_not_another_reference():
    entries, total, gaps = extract_reference_entries([
        block("Jane Doe. A useful method.\nA Journal,\n2024.\n\nJohn Roe. Another useful method. 2023.")
    ])
    assert len(entries) == total == 2 and not gaps
    assert metadata_hints(entries[0].text)["year"] == 2024


@pytest.mark.parametrize("first", [
    "Jane Doe.\nA Useful Method. A Journal, 2024.",
    "Jane Doe. A useful method.\nNature. 2024, 10:100-110.",
])
def test_wrapped_title_and_venue_are_not_author_boundaries(first):
    entries, total, gaps = extract_reference_entries([
        block(first + "\n\nJohn Roe. Another useful method. 2023.")
    ])
    assert len(entries) == total == 2 and not gaps
    assert entries[0].text.startswith("Jane Doe.")
    assert metadata_hints(entries[0].text)["year"] == 2024


def test_venue_continues_in_next_chunk_after_a_title_period(monkeypatch):
    monkeypatch.setattr("app.credibility.references._LOOKUP", RecordingLookup())
    result = check_references(context(
        block("Jane Doe. A useful method."),
        block("In Proceedings of a Conference, 2024.\n\nJohn Roe. Another method. 2023.", "refs_2", 11),
    ))
    assert result.checks.extracted_count == 2 and result.checks.extraction_status == "complete"
    assert len(result.checks.items[0].paper_evidence_ids) == 2
    assert result.checks.items[0].parsed.year == 2024


def test_network_failure_does_not_change_extraction_completion(monkeypatch):
    monkeypatch.setattr("app.credibility.references._LOOKUP", RecordingLookup("timeout"))
    result = check_references(context(block("Jane Doe. A useful method. 2024.")))
    assert result.checks.extraction_status == "complete"
    assert result.dimension.check_status == "partial"
    assert result.checks.checked_count == 0 and result.checks.not_checked_count == 1
    assert result.checks.query_completion_ratio == 0
    assert result.checks.extraction_coverage_ratio == 1


def test_invalid_provider_json_is_a_query_failure_instead_of_crashing_the_check(monkeypatch):
    class Response:
        status_code = 200
        is_error = False

        def json(self):
            raise ValueError("Malformed metadata response")

    monkeypatch.setattr("app.credibility.references.httpx.get", lambda *a, **kw: Response())
    monkeypatch.setattr("app.credibility.references._LOOKUP", CrossrefLookup(min_interval_seconds=0))
    result = check_references(context(block("Jane Doe. A useful method. 2024.")))
    assert result.checks.extraction_status == "complete"
    assert result.checks.not_checked_count == 1 and result.sources[0].fetch_status == "error"


def test_unlocatable_reference_is_not_sent_to_metadata_lookup(monkeypatch):
    lookup = RecordingLookup()
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    monkeypatch.setattr("app.credibility.references.locate_paper_span", lambda *a: (None, ["missing source"]))
    result = check_references(context(block("Jane Doe. A useful method. 2024.")))
    assert not lookup.calls and result.checks.items[0].outcome == "not_checked"
    assert result.checks.extraction_status == "partial"


@pytest.mark.parametrize(("paper_id", "version", "count"), [
    ("paper_001", "v3", 49), ("paper_002", "v1", 49), ("paper_004", "v1", 110),
])
def test_real_benchmark_references_reach_lookup_with_located_evidence(monkeypatch, paper_id, version, count):
    parsed_dir = Path(__file__).resolve().parents[2] / "data/data/parsed" / paper_id / version
    if not parsed_dir.is_dir():
        pytest.skip("Sibling benchmark data is not installed")
    doc = load_parsed_document(parsed_dir)
    lookup = RecordingLookup()
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    result = check_references(context(doc=doc))
    assert result.checks.extracted_count == result.checks.checked_count == len(lookup.calls) == count
    assert result.checks.original_total == count and result.checks.extraction_status == "complete"
    assert not result.checks.gaps
    ids = {e.evidence_id for e in result.evidence}
    for item in result.checks.items:
        assert item.paper_evidence_ids and set(item.paper_evidence_ids) <= ids
    for evidence in result.evidence:
        assert evidence.excerpt in next(b.text for b in doc.blocks if b.chunk_id == evidence.paper_locator.chunk_id)


def test_page_chunk_opening_with_a_carried_reference_tail_still_splits_following_items():
    first = TextBlock(
        "REFERENCES\nMartin Abadi, Andy Chu, and Li Zhang. Deep learning with differential privacy. "
        "In Proceedings of the 2016 ACM SIGSAC", 10, chunk_id="c1", section_role="references",
    )
    second = TextBlock(
        "conference on computer and communications security, pp. 308–318, 2016.\n"
        "Michael Aerni, Jie Zhang, and Florian Tramer. Evaluations of machine learning privacy defenses\n"
        "are misleading. CCS 2024, 2024.\n"
        "Nicholas Carlini, Steve Chien, and Milad Nasr. Membership inference attacks from first principles.\n"
        "In IEEE Symposium on Security and Privacy, 2022.",
        11, chunk_id="c2", section_role="references",
    )
    entries, total, gaps = extract_reference_entries([first, second])
    assert [e.text.split(",")[0] for e in entries] == ["Martin Abadi", "Michael Aerni", "Nicholas Carlini"]
    assert "308–318, 2016." in entries[0].text and total == 3 and not gaps


def test_back_reference_page_lists_do_not_glue_entries():
    from app.credibility.reference_extraction import extract_reference_entries
    from app.parsing.base import TextBlock

    text = (
        "References\n"
        "Aida Amini, Saadia Gabriel, Shanchuan Lin, and Hannaneh Hajishirzi. Mathqa: Towards interpretable math\n"
        "word problem solving with operation-based formalisms. In NAACL, pp. 2357–2367, 2019. 10, 20\n"
        "Yonatan Bitton, Hritik Bansal, Jack Hessel, and Ludwig Schimdt. VisIT-Bench: A benchmark for\n"
        "vision-language instruction following inspired by real-world use. arXiv preprint arXiv:2308.06595, 2023. 10\n"
        "Anya Ji, Noriyuki Kojima, and Yoav Artzi. Abstract visual reasoning with tangram shapes. 2022. 20"
    )
    entries, total, gaps = extract_reference_entries(
        [TextBlock(text=text, chunk_id="refs", page_start=9, section_role="references")]
    )
    assert [entry.text.split(",")[0] for entry in entries] == ["Aida Amini", "Yonatan Bitton", "Anya Ji"]
    assert total == 3 and gaps == []
