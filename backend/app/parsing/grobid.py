"""Demo parser and GROBID-compatible facade.

The round-two generic path is implemented by ``GenericPdfParser``. GROBID is
kept as an optional future enrichment layer while sharing the same fallback and
``ParsedDocument`` contract.
"""
from __future__ import annotations

from app.parsing.base import ParsedDocument, Parser, TextBlock
from app.parsing.generic import GenericPdfParser
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper, Section


class StubParser(Parser):
    """占位解析器：返回一份结构正确的假文档，便于端到端联调。"""

    def parse_pdf(self, path: str) -> ParsedDocument:
        return self._fake(paper_id="paper_stub_pdf", title="PDF demo report (no real parsing performed)")

    def parse_arxiv(self, arxiv_id: str) -> ParsedDocument:
        return self._fake(
            paper_id=f"paper_{arxiv_id}",
            title=f"Stub parse of arXiv:{arxiv_id}",
            arxiv_id=arxiv_id,
        )

    @staticmethod
    def _fake(paper_id: str, title: str, arxiv_id: str | None = None) -> ParsedDocument:
        paper = Paper(
            paper_id=paper_id,
            title=title,
            authors=[],
            abstract="Stub abstract. Replace with a real parser.",
            language="en",
            page_count=2,
            arxiv_id=arxiv_id,
            doi=None,
            sections=[
                Section(section_id="sec_01", title="Introduction", level=1, page_start=1, page_end=1)
            ],
            parse_quality=ParseQuality.PARTIAL,
            parse_warnings=["SECTION_PARSE_PARTIAL"],
        )
        blocks = [
            TextBlock(
                text=(
                    "This machine-learning research paper studies representation learning with a "
                    "neural model. It defines a training objective, compares baselines on benchmark "
                    "datasets, and discusses limitations. " * 8
                ),
                page_start=1,
                chunk_id=f"{paper_id}_chunk_000",
                section_id="sec_01",
                section_title="Introduction",
            ),
            TextBlock(
                text=(
                    "The experiments report predictive performance, ablations, hyperparameters, and "
                    "repeated evaluations. The results support the main methodological claim while "
                    "leaving several robustness questions open. " * 8
                ),
                page_start=2,
                chunk_id=f"{paper_id}_chunk_001",
                section_id="sec_02",
                section_title="Experiments",
            ),
        ]
        return ParsedDocument(
            paper=paper,
            blocks=blocks,
            parse_quality=ParseQuality.PARTIAL,
            warnings=["SECTION_PARSE_PARTIAL"],
        )


class GrobidParser(Parser):
    """Compatibility facade.

    The evidence-preserving local parser is the reliable baseline. The class name
    remains available so deployments can add GROBID enrichment without changing
    the pipeline contract.
    """

    def __init__(self, grobid_url: str, fallback: GenericPdfParser):
        self.grobid_url = grobid_url
        self.fallback = fallback

    def parse_pdf(self, path: str) -> ParsedDocument:
        return self.fallback.parse_pdf(path)

    def parse_arxiv(self, arxiv_id: str) -> ParsedDocument:
        return self.fallback.parse_arxiv(arxiv_id)
