"""Offline guard and same-PDF fresh-parse acceptance for the live verifier."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.credibility.context import CheckContext, ParsedPaperRef
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper
from scripts import verify_crossref_live as verifier


def _context(pdf: Path, *, benchmark: bool):
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return CheckContext(
        source={"type": "arxiv", "arxiv_id": "2310.07923v5"}, repo_root=pdf.parent,
        generated_at="2026-10-19T00:00:00Z", unresolved_reason=None,
        ref=ParsedPaperRef(
            paper_id="paper_005", paper_version_id="paper_005_v5", version_label="v5", parsed_dir=pdf.parent,
            metadata={"pdf_sha256": digest, "page_count": 2, "parse_quality": "good"},
            source_record={"arxiv_id": "2310.07923", "arxiv_version": "v5", "title": "An original title",
                           "authors": ["A. Author"], "abstract": "Original abstract", "subjects": ["cs.CL"]},
        ) if benchmark else None,
        doc=ParsedDocument(
            paper=Paper(paper_id="old", arxiv_id=None if benchmark else "2310.07923v5",
                        title=None if benchmark else "An original title", authors=[] if benchmark else ["A. Author"],
                        abstract=None if benchmark else "Original abstract", parse_quality=ParseQuality.GOOD),
            blocks=[TextBlock(text="STALE BLOCK MUST NOT BE REUSED", chunk_id="old", page_start=1)],
            parser_name="old-parser", source_sha256=None if benchmark else digest,
        ),
    )


@pytest.mark.parametrize("benchmark", [False, True])
def test_fresh_parse_uses_identical_pdf_and_saved_metadata_with_live_identity(tmp_path, monkeypatch, benchmark):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-fixture-source-identity")
    ctx = _context(pdf, benchmark=benchmark)
    old_doc = ctx.doc
    body = "A current PDF parser must extract this fresh body with reliable evidence locations. " * 10

    class Reader:
        is_encrypted = False
        metadata = {}

        def __init__(self, path):
            assert Path(path) == pdf
            self.pages = [SimpleNamespace(extract_text=lambda: "ABSTRACT\n" + body),
                          SimpleNamespace(extract_text=lambda: "REFERENCES\nJane Doe. A real title. 2024.")]

    monkeypatch.setattr("app.parsing.generic.PdfReader", Reader)
    logger = logging.getLogger("fontTools")
    monkeypatch.setattr(logger, "level", logging.WARNING)
    provenance = verifier._fresh_parse_context(ctx, pdf)
    assert ctx.doc is not old_doc and ctx.ref is None
    assert ctx.doc.paper.arxiv_id == "2310.07923v5"
    assert ctx.doc.paper.title == "An original title" and ctx.doc.paper.authors == ["A. Author"]
    assert ctx.doc.paper.abstract == "Original abstract"
    if benchmark:
        assert ctx.doc.source_categories == ["cs.CL"]
    assert body.strip() in ctx.doc.blocks[0].text
    assert not any("STALE BLOCK" in block.text for block in ctx.doc.blocks)
    assert "sha256:" + hashlib.sha256(pdf.read_bytes()).hexdigest() in ctx.paper_version_id
    assert provenance["pdf_sha256"] == ctx.doc.source_sha256
    assert provenance["pdf_path"] == str(pdf)
    assert provenance["parser_name"] == "generic-pypdf-v4"
    assert provenance["reused_parsed_blocks"] is False
    assert provenance["origin_parser_name"] == "old-parser"
    assert logger.level == logging.WARNING


def test_fresh_parse_rejects_different_pdf_or_missing_lineage_before_parsing(tmp_path, monkeypatch):
    original, different = tmp_path / "original.pdf", tmp_path / "different.pdf"
    original.write_bytes(b"%PDF-original")
    different.write_bytes(b"%PDF-different")
    ctx = _context(original, benchmark=False)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A different or unidentified PDF must not reach the parser")

    monkeypatch.setattr("app.parsing.generic.GenericPdfParser.parse_pdf", forbidden)
    with pytest.raises(verifier.VerificationError, match="SHA-256"):
        verifier._fresh_parse_context(ctx, different)
    ctx.doc.source_sha256 = None
    with pytest.raises(verifier.VerificationError, match="SHA-256"):
        verifier._fresh_parse_context(ctx, original)


@pytest.mark.parametrize(("method", "url", "expected"), [
    ("GET", "https://api.crossref.org/works?query=a", "crossref"),
    ("GET", "https://api.crossref.org/works/10.1000/example", "crossref"),
    ("GET", "https://export.arxiv.org/api/query?id_list=2106.09685", "arxiv"),
    ("POST", "https://export.arxiv.org/api/query", None),
    ("GET", "http://export.arxiv.org/api/query", None),
    ("GET", "https://export.arxiv.org/abs/2106.09685", None),
    ("GET", "https://arxiv.org/api/query", None),
    ("GET", "https://api.crossref.org/members", None),
    ("GET", "https://api.deepseek.com/chat/completions", None),
    ("GET", "https://secret@api.crossref.org/works", None),
    ("GET", "https://api.crossref.org:8443/works", None),
    ("GET", "https://export.arxiv.org/api/query#secret", None),
])
def test_network_guard_allows_only_metadata_gets(method, url, expected):
    assert verifier._allowed_provider(httpx.Request(method, url)) == expected


def _args(tmp_path, *, offline=False):
    return SimpleNamespace(state_path=tmp_path / "state.sqlite3", min_interval_seconds=0,
                           timeout_seconds=None, retry_wait_budget_seconds=0, offline_cache=offline)


def _audit():
    return {"http_get_attempt_count": 0, "http_call_count": 0, "blocked_http_count": 0,
            "lookup_call_count": 0, "cache_hit_count": 0, "http_calls": [], "lookups": []}


def test_live_guard_counts_each_provider_without_real_network(tmp_path, monkeypatch):
    def run(_context):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"fixture"))) as client:
            client.get("https://api.crossref.org/works?query=test")
            client.get("https://export.arxiv.org/api/query?id_list=2106.09685")
        return "completed"

    monkeypatch.setattr("app.credibility.references.check_references", run)
    audit = _audit()
    assert verifier._execute_references(None, _args(tmp_path), audit) == "completed"
    assert audit["http_call_count"] == 2 and audit["blocked_http_count"] == 0
    assert audit["crossref_http_call_count"] == audit["arxiv_http_call_count"] == 1
    assert audit["provider_http_call_counts"] == {"crossref": 1, "arxiv": 1}
    assert [event["provider"] for event in audit["http_calls"]] == ["crossref", "arxiv"]


def test_offline_guard_blocks_even_allowed_arxiv_and_legacy_get(tmp_path, monkeypatch):
    def run(_context):
        with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Offline socket attempted"))) as client:
            client.get("https://export.arxiv.org/api/query?id_list=2106.09685")

    monkeypatch.setattr("app.credibility.references.check_references", run)
    audit = _audit()
    with pytest.raises(verifier.NetworkForbidden):
        verifier._execute_references(None, _args(tmp_path, offline=True), audit)
    assert audit["blocked_http_count"] == 1 and audit["http_call_count"] == 0

    monkeypatch.setattr("app.credibility.references.check_references",
                        lambda _: httpx.get("https://api.crossref.org/works"))
    audit = _audit()
    with pytest.raises(verifier.NetworkForbidden):
        verifier._execute_references(None, _args(tmp_path, offline=True), audit)
    assert audit["http_get_attempt_count"] == audit["blocked_http_count"] == 1
