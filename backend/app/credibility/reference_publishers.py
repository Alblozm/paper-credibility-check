"""Conservative automatic fallback to official proceedings and publisher pages.

Discovery uses URLs in the citation, publisher links in the captured Crossref
response and bounded conference/journal indexes. Index title hits only discover
a page: title, every cited author and the cited year must also agree with that
page's publication metadata and visible text before confirmation.
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_matching import _authors_agree, _doi, _title
from app.credibility.reference_publisher_http import PublisherFetch, canonical_url, fetch_official
from app.credibility.reference_sources import ReferenceConfirmation, ReferenceFallback, ReferenceObservation
from app.credibility.snapshots import SnapshotError, capture_snapshot, read_snapshot

REFERENCE_PUBLISHERS_VERSION = "official-publication-fallback-1.1"
_MAX_PAGES = 8
_MAX_PAPERS = 3


@dataclass
class PublicationHTML:
    metadata: dict[str, list[str]] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)
    text: str = ""
    records: list[dict] = field(default_factory=list)


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.result = PublicationHTML()
        self.parts, self.hidden, self.anchor = [], 0, None
        self.record = None
        self.record_title_tag = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.record is not None and tag == self.record["tag"]:
            self.record["depth"] += 1
        elif self.record is None and (tag in {"li", "dl"}
                                     or (tag == "div" and "paper" in attributes.get("class", "").split())):
            self.record = {"tag": tag, "depth": 1, "parts": [], "title": [], "links": []}
        if self.record is not None and (tag == "dt" or (tag == "p" and
                                          "title" in attributes.get("class", "").split())):
            self.record_title_tag = tag
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag == "meta":
            key = (attributes.get("name") or attributes.get("property") or "").casefold()
            content = " ".join((attributes.get("content") or "").split())
            if key and content:
                self.result.metadata.setdefault(key, []).append(content)
        if tag == "a" and attributes.get("href"):
            self.anchor = [attributes["href"], []]
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3", "section", "article"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == self.record_title_tag:
            self.record_title_tag = None
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1
        if tag == "a" and self.anchor:
            link = (self.anchor[0], " ".join(" ".join(self.anchor[1]).split()))
            self.result.links.append(link)
            if self.record is not None:
                self.record["links"].append(link)
            self.anchor = None
        if tag in {"p", "div", "li", "h1", "h2", "h3", "section", "article"} and not self.hidden:
            self.parts.append("\n")
        if self.record is not None and tag == self.record["tag"]:
            self.record["depth"] -= 1
            if self.record["depth"] == 0:
                self.result.records.append({"text": " ".join(self.record["parts"]),
                                            "title": " ".join(self.record["title"]),
                                            "links": self.record["links"]})
                self.record = None

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.record is not None:
                self.record["parts"].append(data)
                if self.record_title_tag:
                    self.record["title"].append(data)
            if self.anchor:
                self.anchor[1].append(data)


def parse_publication_html(raw: bytes) -> PublicationHTML:
    parser = _HTML()
    parser.feed(raw.decode("utf-8", "replace"))
    parser.result.text = "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines()
                                      if line.strip())
    return parser.result


def _observation(fetch: PublisherFetch) -> ReferenceObservation:
    return ReferenceObservation(fetch.url, fetch.fetch_status, fetch.http_status, fetch.retrieved_at,
                                fetch.is_cached, fetch.snapshot_ref, "official_publication")


def _all_authors(cited: list[str], actual: list[str], *, truncated: bool) -> bool:
    """Compare all listed authors one-to-one; no half-list agreement shortcut."""
    if not cited or not actual:
        return False
    if not truncated:
        return _authors_agree(cited, actual, complete=True) is True
    if len(cited) > len(actual):
        return False
    remaining = list(actual)
    for author in cited:
        for index, other in enumerate(remaining):
            if _authors_agree([author], [other], complete=True) is True:
                remaining.pop(index)
                break
        else:
            return False
    return True


def _metadata(page: PublicationHTML) -> CredibilityBibliographicMetadata | None:
    def first(*names):
        for name in names:
            if page.metadata.get(name):
                return page.metadata[name][0]
        return None

    title = first("citation_title", "dc.title", "dcterms.title")
    authors = page.metadata.get("citation_author") or page.metadata.get("dc.creator") or []
    authors = [name.strip() for value in authors for name in value.split(";") if name.strip()]
    authors = [" ".join(reversed(name.split(",", 1))).strip() if "," in name else name for name in authors]
    stamp = first("citation_publication_date", "citation_date", "dc.date", "dcterms.date", "citation_year")
    year = re.search(r"\b(?:19|20)\d{2}\b", stamp or "")
    doi = first("citation_doi", "dc.identifier.doi")
    if not title or not authors:
        return None
    return CredibilityBibliographicMetadata(title=title, authors=authors, year=int(year[0]) if year else None,
                                           doi=_doi(doi) or None, arxiv_id=None)


def _identity(parsed, actual, raw_text) -> tuple[bool, str]:
    cited_title, source_title = _title(parsed.title), _title(actual.title)
    if cited_title.edition and cited_title.edition != source_title.edition:
        return False, "version_evidence_missing"
    if cited_title.joined != source_title.joined:
        return False, "version_evidence_missing"
    if not _all_authors(parsed.authors, actual.authors, truncated=bool(re.search(r"\bet\s+al\b", raw_text, re.I))):
        return False, "author_mismatch"
    if parsed.year is not None and actual.year is None:
        return False, "insufficient_metadata"
    if parsed.year is not None and parsed.year != actual.year:
        return False, "year_mismatch"
    if parsed.doi and actual.doi and _doi(parsed.doi) != _doi(actual.doi):
        return False, "identifier_mismatch"
    return True, "official_metadata_confirmed"


def _urls(raw: str) -> list[str]:
    # Repair line-wrap whitespace only directly next to URL punctuation.
    text = html.unescape(raw)
    text = re.sub(r"(?<=[./])\s+(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[./])", "", text)
    values = []
    for found in re.findall(r"https?://[^\s<>\[\]]+", text):
        found = found.rstrip(".,;)")
        found = re.sub(r"^http://", "https://", found)
        try:
            value = canonical_url(found)
        except ValueError:
            continue
        # arXiv is verified by the arXiv provider, including version history.
        if urlsplit(value).hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            values.append(value)
    return list(dict.fromkeys(values))


def _crossref_urls(parsed, snapshot_ref) -> list[str]:
    if not snapshot_ref:
        return []
    try:
        payload = read_snapshot(snapshot_ref).normalized_json
        message = payload.get("message", {}) if isinstance(payload, dict) else {}
        rows = message.get("items", []) if isinstance(message, dict) else []
        if not isinstance(rows, list):
            return []
    except (SnapshotError, TypeError, ValueError):
        return []
    urls = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        titles = row.get("title") or []
        title = titles[0] if isinstance(titles, list) and titles else None
        authors = [" ".join(filter(None, [author.get("given"), author.get("family")]))
                   for author in row.get("author", []) if isinstance(author, dict)]
        if (_title(parsed.title).joined != _title(title).joined
                or _authors_agree(parsed.authors, authors, complete=True) is not True):
            continue
        resource = row.get("resource", {})
        primary = resource.get("primary", {}) if isinstance(resource, dict) else {}
        discovered = [primary.get("URL")] if isinstance(primary, dict) else []
        discovered += [link.get("URL") for link in row.get("link", []) if isinstance(link, dict)]
        for value in discovered:
            if isinstance(value, str):
                urls.extend(_urls(value))
    return list(dict.fromkeys(urls))


def _directory_links(page: PublicationHTML, base: str, title: str) -> list[str]:
    target = _title(title).joined
    output = []
    for href, label in page.links:
        if href.startswith("#"):
            continue
        if target and _title(label).joined == target:
            try:
                output.append(canonical_url(urljoin(base, href)))
            except ValueError:
                continue
    for record in page.records:
        if target and _title(record["title"]).joined == target:
            for href, label in record["links"]:
                if label.casefold() in {"abs", "abstract"} or urlsplit(href).path.endswith(".html"):
                    try:
                        output.append(canonical_url(urljoin(base, href)))
                    except ValueError:
                        continue
    return list(dict.fromkeys(output))[:_MAX_PAPERS]


def _pmlr_volumes(page: PublicationHTML, base: str, raw_text: str, year: int | None) -> list[str]:
    if year is None:
        return []
    lowered = raw_text.casefold()
    conference = ("icml" if re.search(r"\bicml\b|international conference on machine learning", lowered)
                  else "colt" if re.search(r"\bcolt\b|conference on learning theory", lowered) else None)
    if conference is None:
        return []
    full = {"icml": "internationalconferenceonmachinelearning", "colt": "conferenceonlearningtheory"}[conference]
    volumes = []
    # PMLR main-index entries put conference/year outside the volume anchor.
    # Treat the complete enclosing list entry as discovery text, never proof.
    for record in page.records:
        identity = _title(record["text"]).joined
        if str(year) not in record["text"] or not (conference in identity or full in identity):
            continue
        for href, _ in record["links"]:
            if re.search(r"(?:^|/)v\d+/?(?:index\.html)?$", href):
                try:
                    volumes.append(canonical_url(urljoin(base, href)))
                except ValueError:
                    continue
    # Some indexes include the complete event and year in the anchor.
    for href, label in page.links:
        identity = _title(label).joined
        if (str(year) in label and (conference in identity or full in identity)
                and re.search(r"(?:^|/)v\d+/?(?:index\.html)?$", href)):
            try:
                volumes.append(canonical_url(urljoin(base, href)))
            except ValueError:
                continue
    return list(dict.fromkeys(volumes))[:3]


def _index_urls(raw_text: str, year: int | None) -> list[str]:
    lowered = raw_text.casefold()
    indexes = []
    if re.search(r"\bpmlr\b|proceedings of machine learning research|\bicml\b|"
                 r"international conference on machine learning|conference on learning theory", lowered):
        match = re.search(r"(?:\bvolume\s*|\bvol\.?\s*|\bpmlr\s*[:,]?\s*)(\d{1,3})\b", lowered)
        indexes.append(f"https://proceedings.mlr.press/v{match[1]}/" if match else "https://proceedings.mlr.press/")
    if re.search(r"\bjmlr\b|journal of machine learning research", lowered):
        match = re.search(r"(?:\bjmlr\b|journal of machine learning research)\s*[,.:]?\s*(\d{1,3})\b", lowered)
        if match:
            indexes.append(f"https://jmlr.org/papers/v{match[1]}/")
    if year is not None and re.search(r"\bneurips\b|\bnips\b|neural information processing systems", lowered):
        indexes.append(f"https://papers.nips.cc/paper_files/paper/{year}")
    return indexes


def _pdf_metadata(fetch: PublisherFetch, parsed, raw_text) -> tuple[CredibilityBibliographicMetadata | None, str]:
    # PDF-only technical reports need independently visible title plus every
    # cited author on the first page; no synthetic author/year from the citation.
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(fetch.raw), strict=False)
        if not reader.pages or len(reader.pages) > 100:
            return None, "unsupported_official_document"
        text = reader.pages[0].extract_text() or ""
        document = reader.metadata
        # The cited title merely occurring somewhere on page one could be a
        # related-work mention in a different document by the same authors.
        # Require an independent PDF/XMP title and check visible text as well.
        titles = [str(document.title).strip()] if document and document.title else []
        xmp = reader.xmp_metadata
        if xmp is not None:
            xmp_titles = xmp.dc_title
            if isinstance(xmp_titles, dict):
                titles.extend(value.strip() for value in xmp_titles.values()
                              if isinstance(value, str) and value.strip())
        if not titles:
            return None, "insufficient_metadata"
        cited_title = _title(parsed.title)
        if any(_title(title).joined != cited_title.joined
               or (cited_title.edition and _title(title).edition != cited_title.edition) for title in titles):
            return None, "version_evidence_missing"
        title = titles[0]
        if _title(title).joined not in _title(text).joined:
            return None, "version_evidence_missing"
        # With no structured author metadata, avoid inferring a complete author
        # list from the cited list alone. PDF document-info author must
        # independently enumerate it; a first-page text check is also required.
        authors_text = str(document.author or "") if document else ""
        authors = [value.strip() for value in re.split(r"\s*;\s*|\s+and\s+", authors_text) if value.strip()]
        if not _all_authors(parsed.authors, authors, truncated=bool(re.search(r"\bet\s+al\b", raw_text, re.I))):
            return None, "insufficient_metadata"
        compact = _title(text).joined
        if any(_title(author).joined not in compact for author in authors):
            return None, "author_mismatch"
        # Dates are accepted only if explicitly visible on the first page;
        # PDF creation time is a file timestamp, not a publication date.
        dates = set(map(int, re.findall(r"\b(?:19|20)\d{2}\b", text)))
        if parsed.year is not None and parsed.year not in dates:
            return None, "year_mismatch"
        actual = CredibilityBibliographicMetadata(title=title, authors=authors,
                                                 year=parsed.year if parsed.year in dates else None,
                                                 doi=None, arxiv_id=None)
        return actual, "official_pdf_identity_confirmed"
    except Exception:
        return None, "unsupported_official_document"


def resolve_publishers(parsed: CredibilityBibliographicMetadata, *, raw_text: str,
                       crossref_snapshot_ref: str | None = None) -> ReferenceFallback:
    result = ReferenceFallback()
    if not parsed.title or not parsed.authors:
        result.reason_code, result.reason = "insufficient_metadata", "Citation title or authors are missing."
        return result
    queued = [(url, False) for url in _urls(raw_text) + _crossref_urls(parsed, crossref_snapshot_ref)]
    queued += [(url, True) for url in _index_urls(raw_text, parsed.year)]
    seen, inspected, failures, mismatches = set(), 0, [], []
    while queued and len(seen) < _MAX_PAGES and inspected < _MAX_PAPERS:
        url, index = queued.pop(0)
        if url in seen:
            continue
        seen.add(url)
        fetch = fetch_official(url)
        result.observations.append(_observation(fetch))
        if fetch.fetch_status != "ok" or fetch.raw is None:
            failures.append(fetch.fetch_status)
            continue
        if fetch.raw.startswith(b"%PDF-"):
            if index:
                continue
            inspected += 1
            actual, reason = _pdf_metadata(fetch, parsed, raw_text)
        else:
            page = parse_publication_html(fetch.raw)
            if index:
                if url == "https://proceedings.mlr.press/":
                    queued.extend((link, True) for link in _pmlr_volumes(page, fetch.final_url, raw_text, parsed.year))
                else:
                    queued.extend((link, False) for link in _directory_links(page, fetch.final_url, parsed.title))
                continue
            inspected += 1
            actual = _metadata(page)
            if actual is None:
                mismatches.append(("insufficient_metadata",
                                   "Official page lacks complete visible publication title/authors/date."))
                continue
            agrees, reason = _identity(parsed, actual, raw_text)
            if not agrees:
                detail = (f"Official publication year {actual.year!r} differs from cited year {parsed.year!r}."
                          if reason == "year_mismatch" else
                          f"Official authors {actual.authors!r} differ from cited authors {parsed.authors!r}."
                          if reason == "author_mismatch" else
                          f"Official title/identifier differs from the citation ({reason}).")
                mismatches.append((reason, detail))
                continue
            # Metadata-only pages, search results and snippets do not establish
            # the actual document: its title and authors must be in visible HTML.
            visible = _title(page.text).joined
            if (_title(actual.title).joined not in visible
                    or any(_title(author).joined not in visible for author in actual.authors)):
                mismatches.append(("insufficient_metadata",
                                   "Official page lacks complete visible publication title/authors/date."))
                continue
        if actual is None:
            mismatches.append((reason, "Official PDF does not establish complete title/authors/date identity."))
            continue
        proof = capture_snapshot(
            kind="reference_official_identity", raw=fetch.raw, normalized_json={
                "metadata": actual.model_dump(), "reason_code": reason,
                "identity_checks": {"title": "exact_normalized", "authors": "all_listed_one_to_one",
                                    "cited_year": parsed.year, "source_year": actual.year},
                "source_snapshot_ref": fetch.snapshot_ref,
            }, requested_url=fetch.url, final_url=fetch.final_url, retrieved_at=fetch.retrieved_at,
            fetch_status="ok", http_status=200, is_cached=fetch.is_cached,
            metadata={"provider": "official_publication", "version": REFERENCE_PUBLISHERS_VERSION},
            parent_ref=fetch.snapshot_ref,
        )
        why = "Official publication title, every listed author and cited date agree."
        if parsed.year is None:
            why = "Official publication title and every listed author agree; citation supplies no year."
        result.confirmations.append(ReferenceConfirmation(actual, proof, fetch.final_url, why, "official_publication"))
        result.reason_code, result.reason = reason, why
        return result
    if mismatches:
        result.reason_code, result.reason = mismatches[0]
    elif failures:
        result.reason_code, result.reason = "source_fetch_failed", "Official-source retrieval was incomplete."
    else:
        result.reason_code = "target_not_found_in_scope"
        result.reason = "No exact target page was found in the bounded official proceedings/publisher indexes."
    return result
