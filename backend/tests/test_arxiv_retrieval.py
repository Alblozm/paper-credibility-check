from __future__ import annotations

import io

import httpx
import pytest
from pypdf import PdfWriter

from app.parsing.arxiv import (
    ArxivDownloadError,
    ArxivRetriever,
    ArxivSourceError,
    normalize_arxiv_id,
)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("2501.01234", "2501.01234"),
        ("arXiv:2501.01234v2", "2501.01234v2"),
        ("https://arxiv.org/abs/2501.01234v2", "2501.01234v2"),
        ("https://arxiv.org/pdf/2501.01234v2.pdf?download=1", "2501.01234v2"),
        ("hep-th/9901001v3", "hep-th/9901001v3"),
    ],
)
def test_normalizes_arxiv_ids_and_urls(source, expected):
    assert normalize_arxiv_id(source) == expected


@pytest.mark.parametrize(
    "source",
    ["", "not-an-id", "https://example.com/abs/2501.01234", "2501.01234v0"],
)
def test_rejects_invalid_arxiv_sources(source):
    with pytest.raises(ArxivSourceError):
        normalize_arxiv_id(source)


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_retriever_streams_valid_pdf_to_private_cache(tmp_path):
    content = _blank_pdf()
    atom = b"""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry>
    <id>http://arxiv.org/abs/2501.01234v3</id>
    <title>A Test Paper</title><summary>Test abstract.</summary>
    <author><name>Test Author</name></author><category term='cs.LG'/></entry></feed>"""

    def handler(request):
        return httpx.Response(200, content=atom if "api/query" in str(request.url) else content)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = ArxivRetriever(
            tmp_path,
            max_bytes=1024 * 1024,
            max_pages=5,
            client=client,
        ).retrieve("https://arxiv.org/pdf/2501.01234.pdf")
    assert result.arxiv_id == "2501.01234v3"
    assert result.pdf_path.read_bytes() == content
    assert result.pdf_path.stat().st_mode & 0o777 == 0o600
    assert result.page_count == 1
    assert result.metadata is not None
    assert result.metadata.title == "A Test Paper"
    assert result.metadata.arxiv_id == "2501.01234v3"
    assert result.metadata.authors == ["Test Author"]


def test_retriever_rejects_non_pdf_response(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"rate limited"))
    with httpx.Client(transport=transport) as client:
        retriever = ArxivRetriever(tmp_path, max_bytes=1024, max_pages=5, client=client)
        with pytest.raises(ArxivDownloadError, match="did not return a valid PDF"):
            retriever.retrieve("2501.01234")


def test_versionless_source_refreshes_pdf_instead_of_using_stale_cache(tmp_path):
    content = _blank_pdf()
    pdf_requests = 0

    def handler(request):
        nonlocal pdf_requests
        if "api/query" in str(request.url):
            return httpx.Response(200, content=b"<feed xmlns='http://www.w3.org/2005/Atom'/>")
        pdf_requests += 1
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        retriever = ArxivRetriever(tmp_path, max_bytes=1024 * 1024, max_pages=5, client=client)
        retriever.retrieve("2501.01234")
        retriever.retrieve("2501.01234")
    assert pdf_requests == 2
