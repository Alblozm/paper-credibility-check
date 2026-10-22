"""PDF typography alignment never substitutes facts or arbitrary quote text."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.credibility.context import CheckContext
from app.credibility.locate import PaperSpanCandidate, find_source_quote, locate_paper_span
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.paper import Paper


@pytest.mark.parametrize("source,excerpt", [
    ("We investigate rank-deﬁciency in language adaptation.", "rank-\ndeﬁciency in language adaptation"),
    ("Our pre-\ntraining method is efﬁcient.", "Our pretraining method is efficient."),
    ("The method is efﬁcient and “well supported”.", 'The method is efficient and "well supported".'),
    ("First line\nsecond line", "First line second line"),
    ("The pre-\ntrained model has fewer pa-\nrameters.", "The pre-trained model has fewer parameters."),
    ("We study rank-deﬁciency and pre-\ntraining.", "We study rank-\ndeficiency and pretraining."),
])
def test_typographical_quote_is_returned_as_the_original_source_slice(source, excerpt):
    actual = find_source_quote(source, excerpt)
    assert actual is not None and actual in source


@pytest.mark.parametrize("source,excerpt", [
    ("Accuracy is 75.9 percent.", "Accuracy is 95.9 percent."),
    ("The result does not improve.", "The result does improve."),
    ("We re-cover the surface.", "We recover the surface."),
    ("The result is -10.", "The result is 10."),
    ("Our model uses 10⁴ parameters.", "Our model uses 104 parameters."),
    ("The exponent is 1⁰.", "The exponent is 10."),
    ("The coordinate is x₂.", "The coordinate is x2."),
    ("The loss is ½.", "The loss is 1⁄2."),
    ("A real source passage.", "\x00A real source passage."),
    ("loss = x-\ny", "loss = xy"),
    ("loss = score-\ny", "loss = scorey"),
    ("loss = x-\npenalty", "loss = xpenalty"),
    ("A real source passage.", "   \n  "),
])
def test_typography_matching_cannot_change_numbers_negation_or_inline_hyphens(source, excerpt):
    assert find_source_quote(source, excerpt) is None


# paper_041 chunk_0002 in the benchmark parse (pdfplumber x_tolerance=2) and
# the quote the model returned for it on 2026-10-20.
GLUED_SOURCE = (
    "Becausethefilteringcriteria(conditions)usedtoderivethesestatistics are drawn randomly from a large "
    "space of possible attribute combinations, the resulting quantities "
    "areempiricallyverifiableyetunlikelytoappearverbatiminpretrainingcorpora."
)


@pytest.mark.parametrize("source,excerpt", [
    (GLUED_SOURCE, "the resulting quantities "
                   "areempiricallyverifiableyetunlikelytoappearverbatim inpretrainingcorpora."),
    (GLUED_SOURCE, "the resulting quantities are empirically verifiable yet unlikely to appear verbatim in "
                   "pretraining corpora."),
    ("Ground truth values inOPEN ES-\nTIMATE were estimated.", "Ground truth values in OPEN ESTIMATE were estimated."),
    ("The title, “ Large Language Models Cannot Self-Correct Reasoning Yet ”, is",
     "The title, “Large Language Models Cannot Self-Correct Reasoning Yet”, is"),
])
def test_extraction_spacing_is_returned_as_the_original_source_slice(source, excerpt):
    actual = find_source_quote(source, excerpt)
    assert actual is not None and actual in source


@pytest.mark.parametrize("source,excerpt", [
    ("The batch sizes are 10 5 and 20 across all runs.", "The batch sizes are 105 and 20 across all runs."),
    ("The batch sizes are 105 and 20 across all runs.", "The batch sizes are 10 5 and 20 across all runs."),
    ("Memoryoverhead is 90 units in every setting.", "Memory overhead is 95 units in every setting."),
    ("Theresultdoesnotimprove on any benchmark.", "The result does improve on any benchmark."),
    ("Were-cover the surface on every benchmark.", "We recover the surface on every benchmark."),
    ("thecat sat here", "the cat sat here"),
])
def test_spacing_fallback_cannot_change_numbers_words_hyphens_or_short_quotes(source, excerpt):
    assert find_source_quote(source, excerpt) is None


def test_anchor_correction_can_disable_spacing_fallback():
    excerpt = "the resulting quantities are empirically verifiable yet unlikely to appear verbatim"
    assert find_source_quote(GLUED_SOURCE, excerpt) is not None
    assert find_source_quote(GLUED_SOURCE, excerpt, allow_spacing_variants=False) is None


def test_locator_returns_canonical_source_quote_and_parser_page():
    source = "We provide an investigation into rank-deﬁciency."
    doc = ParsedDocument(
        paper=Paper(paper_id="fresh", title="Fresh", authors=[], language="en", page_count=3, parse_quality="good"),
        blocks=[TextBlock(
            text=source, page_start=3, chunk_id="real_chunk", section_role="results", section_title="Results",
        )],
    )
    ctx = CheckContext(source={}, repo_root=Path("/test"), generated_at="now", ref=None, doc=doc,
                       unresolved_reason=None)
    located, issues = locate_paper_span(
        ctx, PaperSpanCandidate(evidence_id="e", mode="quote", chunk_id="real_chunk",
                                excerpt="investigation into rank-\ndeficiency"),
    )
    assert not issues and located is not None
    assert located.evidence.excerpt == "investigation into rank-deﬁciency"
    assert located.evidence.paper_locator.page_start == 3
