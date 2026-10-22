"""arXiv source normalization and bounded PDF retrieval."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from pypdf import PdfReader


class ArxivSourceError(ValueError):
    """A user supplied an invalid arXiv source."""


class ArxivDownloadError(RuntimeError):
    """A valid arXiv source could not be downloaded or validated.

    ``code`` and ``retryable`` are the API error fields: only transient network
    failures are retryable; a missing, oversized or unreadable paper is not.
    """

    def __init__(self, message: str, *, code: str = "ARXIV_FETCH_FAILED", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


# Modern IDs are YYMM.number; the legacy form is archive/YYMMnnn.
_MODERN_ID = re.compile(r"(?P<id>\d{4}\.\d{4,5}(?:v[1-9]\d*)?)", re.IGNORECASE)
_LEGACY_ID = re.compile(
    r"(?P<id>[a-z][a-z0-9.\-]+/\d{7}(?:v[1-9]\d*)?)",
    re.IGNORECASE,
)
_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}


def normalize_arxiv_id(value: str) -> str:
    """Return a canonical arXiv identifier from an ID, abs URL, or PDF URL."""
    candidate = unquote((value or "").strip())
    if not candidate:
        raise ArxivSourceError("Enter a valid arXiv ID or link.")

    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname not in _ARXIV_HOSTS:
            raise ArxivSourceError("Enter a paper link on arxiv.org.")
        candidate = parsed.path.lstrip("/")
        if candidate.startswith(("abs/", "pdf/")):
            candidate = candidate.split("/", 1)[1]
        if candidate.lower().endswith(".pdf"):
            candidate = candidate[:-4]
    else:
        candidate = re.sub(r"^arxiv:\s*", "", candidate, flags=re.IGNORECASE)

    candidate = candidate.strip().strip("/")
    match = _MODERN_ID.fullmatch(candidate) or _LEGACY_ID.fullmatch(candidate)
    if not match:
        raise ArxivSourceError("Enter a valid arXiv ID, abs link or PDF link.")
    identifier = match.group("id")
    # Archive names are case-insensitive; canonical arXiv identifiers are lowercase.
    return identifier.lower()


def _too_large(max_bytes: int) -> str:
    return f"The arXiv PDF exceeds the {max_bytes // (1024 * 1024)} MB file size limit."


def arxiv_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{normalize_arxiv_id(arxiv_id)}"


@dataclass(frozen=True)
class ArxivMetadata:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]


@dataclass(frozen=True)
class RetrievedArxiv:
    arxiv_id: str
    pdf_path: Path
    pdf_url: str
    sha256: str
    page_count: int
    metadata: ArxivMetadata | None = None


class ArxivRetriever:
    def __init__(
        self,
        cache_dir: str | Path,
        *,
        max_bytes: int,
        max_pages: int,
        timeout_seconds: float = 45.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.max_bytes = max_bytes
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.client = client

    def retrieve(self, source: str) -> RetrievedArxiv:
        arxiv_id = normalize_arxiv_id(source)
        target = self.cache_dir / f"{arxiv_id.replace('/', '__')}.pdf"
        explicit_version = re.search(r"v[1-9]\d*$", arxiv_id) is not None
        if target.is_file() and explicit_version:
            try:
                return self._with_metadata(self._inspect(target, arxiv_id))
            except ArxivDownloadError:
                target.unlink(missing_ok=True)
        elif target.is_file():
            # A versionless URL means "latest". Reusing it after arXiv publishes
            # a revision would pair old bytes with new metadata, so refresh it.
            target.unlink()

        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        url = arxiv_pdf_url(arxiv_id)
        own_client = self.client is None
        client = self.client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": "paper-credibility-check/0.2 (research tool)"},
        )
        temp_path: Path | None = None
        try:
            with client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise ArxivDownloadError("This paper or version was not found on arXiv.", code="ARXIV_NOT_FOUND")
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared and int(declared) > self.max_bytes:
                    raise ArxivDownloadError(_too_large(self.max_bytes), code="FILE_TOO_LARGE")
                fd, name = tempfile.mkstemp(prefix=".arxiv-", suffix=".pdf", dir=self.cache_dir)
                temp_path = Path(name)
                total = 0
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise ArxivDownloadError(_too_large(self.max_bytes), code="FILE_TOO_LARGE")
                        handle.write(chunk)
            if not temp_path.read_bytes()[:5] == b"%PDF-":
                raise ArxivDownloadError("arXiv did not return a valid PDF. Try again later.", retryable=True)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, target)
            temp_path = None
            return self._with_metadata(self._inspect(target, arxiv_id), client=client)
        except ArxivDownloadError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise ArxivDownloadError(
                "The paper could not be downloaded from arXiv. Try again later.", retryable=True,
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if own_client:
                client.close()

    def _inspect(self, path: Path, arxiv_id: str) -> RetrievedArxiv:
        if path.stat().st_size > self.max_bytes:
            raise ArxivDownloadError(_too_large(self.max_bytes), code="FILE_TOO_LARGE")
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ArxivDownloadError(
                    "The arXiv PDF is encrypted and cannot be parsed.", code="PDF_PASSWORD_PROTECTED",
                )
            page_count = len(reader.pages)
        except ArxivDownloadError:
            raise
        except Exception as exc:
            raise ArxivDownloadError(
                "The downloaded arXiv PDF is damaged or unreadable.", code="PDF_PARSE_FAILED",
            ) from exc
        if page_count < 1:
            raise ArxivDownloadError("The downloaded arXiv PDF has no readable pages.", code="PDF_PARSE_FAILED")
        if page_count > self.max_pages:
            raise ArxivDownloadError(
                f"The paper has {page_count} pages; the limit is {self.max_pages}.", code="PAGE_LIMIT_EXCEEDED",
            )
        return RetrievedArxiv(
            arxiv_id=arxiv_id,
            pdf_path=path,
            pdf_url=arxiv_pdf_url(arxiv_id),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            page_count=page_count,
        )

    def _with_metadata(
        self,
        retrieved: RetrievedArxiv,
        *,
        client: httpx.Client | None = None,
    ) -> RetrievedArxiv:
        """Fetch paper identity from Atom, without making PDF retrieval depend on it."""
        own_client = client is None
        active = client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": "paper-credibility-check/0.2 (research tool)"},
        )
        metadata = None
        try:
            response = active.get(
                "https://export.arxiv.org/api/query",
                params={"id_list": retrieved.arxiv_id, "max_results": "1"},
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            namespace = {"atom": "http://www.w3.org/2005/Atom"}
            entry = root.find("atom:entry", namespace)
            if entry is not None:
                entry_url = entry.findtext("atom:id", default="", namespaces=namespace)
                try:
                    resolved_id = normalize_arxiv_id(entry_url)
                except ArxivSourceError:
                    resolved_id = retrieved.arxiv_id
                title = " ".join(entry.findtext("atom:title", default="", namespaces=namespace).split())
                abstract = " ".join(entry.findtext("atom:summary", default="", namespaces=namespace).split())
                authors = [
                    " ".join((name.text or "").split())
                    for name in entry.findall("atom:author/atom:name", namespace)
                    if (name.text or "").strip()
                ]
                categories = [
                    category.attrib["term"]
                    for category in entry.findall("atom:category", namespace)
                    if category.attrib.get("term")
                ]
                if title:
                    metadata = ArxivMetadata(resolved_id, title, authors, abstract, categories)
        except (httpx.HTTPError, ET.ParseError):
            metadata = None
        finally:
            if own_client:
                active.close()
        return RetrievedArxiv(
            arxiv_id=metadata.arxiv_id if metadata else retrieved.arxiv_id,
            pdf_path=retrieved.pdf_path,
            pdf_url=arxiv_pdf_url(metadata.arxiv_id) if metadata else retrieved.pdf_url,
            sha256=retrieved.sha256,
            page_count=retrieved.page_count,
            metadata=metadata,
        )
