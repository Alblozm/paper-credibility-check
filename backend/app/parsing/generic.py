"""Dependency-light, evidence-preserving PDF structurer.

This is the generic fallback for environments without GROBID. It deliberately
favours reliable page/chunk anchors over ambitious layout reconstruction.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from app.parsing.arxiv import ArxivMetadata, ArxivRetriever
from app.parsing.base import PaperElement, PaperLink, ParsedDocument, TextBlock
from app.schemas.enums import Confidence, ParseQuality
from app.schemas.paper import Paper, Section


class PdfParseError(ValueError):
    """The PDF cannot provide a trustworthy structured document."""

    def __init__(self, message: str, *, code: str = "PDF_PARSE_FAILED") -> None:
        super().__init__(message)
        self.code = code


_HEADING = re.compile(r"^(?:(\d+(?:\.\d+)*)\.?\s+)?([A-Z][^.!]{1,160})$")
_ROMAN_HEADING = re.compile(r"^[IVX]+(?:\.\s+|\s+)([A-Z][^.!]{1,160})$")
_APPENDIX_HEADING = re.compile(r"^([A-Z])\s+([A-Z][^.!]{1,160})$")
# Mixed-case and dotted appendix headings ("A Discussion: ...", "E.2.1 Scaling Law Details").
_LETTER_HEADING = re.compile(r"^([A-H])((?:\.\d{1,2}){0,3})\s+([A-Z0-9][^.!,;]{1,120})$")
_TITLE_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "via", "with",
}
_SPECIAL_HEADINGS = {
    "abstract", "introduction", "related work", "background", "method", "methods",
    "experiments", "evaluation", "results", "discussion", "limitations", "conclusion",
    "references", "bibliography", "appendix", "conclusions",
    "acknowledgments", "acknowledgements", "limitations and broader impact",
}
_ELEMENT_CAPTION = re.compile(
    r"^\s*(?P<kind>figure|fig\.|table|theorem|lemma|proposition)\s*"
    r"(?P<number>\d+(?:\.\d+)*)(?:\s*[:.]\s*|\s+[—–-]\s+)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s<>{}\[\]\"']+", re.IGNORECASE)


def _section_role(title: str) -> str:
    """Recognise section metadata without altering the extracted quote text.

    Related work deliberately uses the existing ``background`` role, which
    claim checks exclude from both new claims and supporting evidence.
    """
    title = re.sub(r"\s+", " ", title.casefold()).strip()
    patterns = (
        ("references", r"^(?:references|bibliography|literature cited)\b"),
        ("appendix", r"^(?:appendix|appendices|supplementary material)\b"),
        ("abstract", r"^abstract$"),
        ("introduction", r"^introduction(?:\s+(?:and|&)\b.*)?$"),
        ("background", r"^(?:related work|background|preliminaries|prior work)(?:\s.*)?$"),
        ("methods", r"^(?:method(?:s|ology)?|approach|our approach|proposed (?:method|approach))\b"),
        ("experiments", r"^(?:experiments?|experimental (?:setup|evaluation|study)|empirical evaluation|evaluation)\b"),
        ("results", r"^(?:results|experimental results|empirical results|main results)\b"),
        ("discussion", r"^discussion\b"),
        ("conclusion", r"^conclusions?\b"),
        ("limitations", r"^limitations?\b"),
        ("acknowledgments", r"^acknowledg(?:e)?ments?\b"),
    )
    role = next((role for role, pattern in patterns if re.match(pattern, title)), "")
    if role:
        return role
    # pypdf can drop the spaces of small-cap headings ("RELATEDWORK").
    compact = re.sub(r"[^a-z&]", "", title)
    return next((role for prefix, role in _COMPACT_ROLES if compact.startswith(prefix)), "")


_ROLE_PHRASE = re.compile(
    r"(?:references|bibliography|literature cited|appendix|appendices|supplementary material|abstract|"
    r"introduction|related work|background|preliminaries|prior work|method|methods|methodology|approach|"
    r"our approach|proposed (?:method|approach)|experiments?|experimental (?:setup|evaluation|study|results)|"
    r"empirical (?:evaluation|results)|evaluation|results|main results|discussion|conclusions?|limitations?|"
    r"acknowledg(?:e)?ments?)(?:\s*(?:and|&)\s+[a-z ]{3,40})?"
)


def _is_role_phrase(title: str) -> bool:
    low = re.sub(r"\s+", " ", title.casefold()).strip()
    compact = re.sub(r"[^a-z&]", "", low)
    return bool(_ROLE_PHRASE.fullmatch(low)) or any(compact == prefix for prefix, _ in _COMPACT_ROLES)


_COMPACT_ROLES = (
    ("references", "references"), ("bibliography", "references"), ("appendix", "appendix"),
    ("relatedwork", "background"), ("priorwork", "background"), ("preliminaries", "background"),
    ("experimentalsetup", "experiments"), ("experimentalresults", "results"), ("experiments", "experiments"),
    ("methodology", "methods"), ("discussion", "discussion"), ("conclusion", "conclusion"),
    ("limitation", "limitations"), ("acknowledg", "acknowledgments"),
)


def _clean(text: str | None) -> str:
    text = (text or "").replace("\x00", " ").replace("\u00ad", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _paper_id(arxiv_id: str | None, digest: str) -> str:
    if arxiv_id:
        return "arxiv_" + re.sub(r"[^a-z0-9]+", "_", arxiv_id.lower()).strip("_")
    return f"pdf_{digest[:16]}"


def _heading_text(line: str) -> str:
    compact = _clean(line).strip().rstrip(":")
    # Small-cap PDF fonts can yield "I NTRODUCTION". Normalize heading
    # metadata only; keep the extracted source text unchanged for quote checks.
    compact = re.sub(r"\b([B-Z])\s+([A-Z]{2,})\b", r"\1\2", compact)
    compact = re.sub(
        r"\bA\s+(BSTRACT|DDITIONAL|PPENDIX|NALYSIS|TTENTION|BLATION|PPROACH)\b",
        r"A\1", compact,
    )
    compact = re.sub(r"\s*-\s*", "-", compact)
    return compact


def _heading(line: str, *, allow_appendix: bool = False) -> tuple[int, str] | None:
    raw = _clean(line).strip().rstrip(":")
    if len(raw.split()) > 14:
        return None
    # Protect an actual Roman section number before small-cap recovery can
    # merge "I INTRODUCTION" into "IINTRODUCTION". A bare I followed by a
    # title-like phrase is ambiguous, so undotted numbering needs a known role.
    # "I NTRODUCTION" instead takes the normal small-cap recovery below.
    compact = _heading_text(line)
    for candidate in (raw, compact):
        roman = _ROMAN_HEADING.fullmatch(candidate)
        if roman and (re.match(r"^[IVX]+\.", candidate) or _section_role(roman.group(1))):
            title = roman.group(1)
            return 1, title.title() if title.isupper() else title
    if allow_appendix:
        letter = _letter_heading(compact)
        if letter:
            return letter
    match = _HEADING.fullmatch(compact)
    if not match:
        return None
    number, title = match.groups()
    low = title.lower()
    # Years, GPU models and grant numbers ("2024. URL", "3090 GPU") are not section numbers.
    if number and any(len(part) > 2 for part in number.split(".")):
        return None
    if number is None and low not in _SPECIAL_HEADINGS and allow_appendix:
        appendix = _APPENDIX_HEADING.fullmatch(compact)
        if appendix and appendix.group(2).isupper():
            number, title = appendix.groups()
            low = title.lower()
    # All-caps model names and table headers are not section headings.
    looks_prominent = low in _SPECIAL_HEADINGS or number is not None or bool(_section_role(title))
    if not looks_prominent:
        return None
    # An unnumbered role-like line must be the role phrase itself, not a table
    # header ("Methods LR Iteration Outer Inner") or a run-in paragraph
    # ("Experimental Setup We conduct ...,").
    if number is None and low not in _SPECIAL_HEADINGS and not _is_role_phrase(title):
        return None
    return ((number.count(".") + 1) if number else 1, title.title() if title.isupper() else title)


_RUNNING_HEADER = re.compile(
    r"^(?:published as a (?:conference|workshop) paper at|under review as a (?:conference|workshop) paper at|"
    r"preprint\.?\s|arxiv:\d{4}\.\d{4,5})",
    re.IGNORECASE,
)


def _running_headers(page_texts: list[str]) -> set[str]:
    """First lines repeated on most pages are venue headers, never the paper title."""
    firsts = [next((line.strip() for line in text.splitlines() if line.strip()), "") for text in page_texts]
    counts = Counter(line for line in firsts if line)
    return {line for line, count in counts.items() if count >= max(2, len(page_texts) // 2)}


def _join_title_lines(lines: list[str], vocabulary: set[str]) -> str:
    title = lines[0]
    for line in lines[1:]:
        wrapped = re.search(r"(\w+)-$", title)
        joined = (wrapped.group(1) + line.split()[0]).lower() if wrapped and line.split() else ""
        # A line-end hyphen is a wrap only when the joined word occurs in the paper.
        if joined and joined in vocabulary:
            title = title[:-1] + line
        else:
            title = f"{title} {line}"
    # Small-cap fonts split words after their first capital ("E VALUATING", "TITLE :");
    # rejoin only when the paper itself uses the joined word ("LLM S ON" stays).
    title = re.sub(
        r"\b([B-HJ-Z])\s+([A-Z]{2,})\b",
        lambda m: m.group(1) + m.group(2) if (m.group(1) + m.group(2)).lower() in vocabulary else m.group(0),
        title,
    )
    title = re.sub(r"\s+([:?!,])", r"\1", title)
    return re.sub(r"\s*-\s*", "-", title)


def _letter_heading(compact: str) -> tuple[int, str] | None:
    """Appendix heading with a letter label; its words must be title-cased, not a sentence."""
    match = _LETTER_HEADING.fullmatch(compact)
    if not match:
        return None
    title = match.group(3)
    words = [w for w in re.findall(r"[A-Za-z][\w-]*", title) if w.lower() not in _TITLE_STOPWORDS]
    if not words or len(title.split()) > 12 or sum(w[0].isupper() for w in words) < 0.75 * len(words):
        return None
    return match.group(2).count(".") + 1, title.title() if title.isupper() else title


def _title_from_first_page(
    lines: list[str], *, headers: set[str] = frozenset(), vocabulary: set[str] = frozenset(),
) -> str | None:
    """Keep contiguous all-caps title lines before the author/abstract area."""
    candidates: list[str] = []
    for line in lines:
        if line in headers or _RUNNING_HEADER.match(line):
            continue
        heading = _heading(line)
        if (heading and heading[1].lower() in _SPECIAL_HEADINGS) or not 3 <= len(line) <= 240:
            break
        if line.isupper():
            candidates.append(line)
        elif candidates:
            break
        else:
            return line if len(line) >= 8 else None
    return _join_title_lines(candidates, vocabulary) if candidates else None


def _chunks(text: str, target_chars: int = 3600) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    # Each unit keeps the separator it had in the page text. Long paragraphs split
    # at their own line breaks first: pypdf pages rarely contain blank lines, and
    # re-joining sentences with blank lines destroyed reference-list line structure.
    units: list[tuple[str, str]] = []
    for paragraph in paragraphs:
        if len(paragraph) <= target_chars:
            units.append(("\n\n", paragraph))
            continue
        separator = "\n\n"
        for line in (part.strip() for part in paragraph.splitlines()):
            if not line:
                continue
            if len(line) <= target_chars:
                units.append((separator, line))
                separator = "\n"
                continue
            sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]
            for sentence in sentences:
                pieces = [sentence[start:start + target_chars] for start in range(0, len(sentence), target_chars)]
                for piece in pieces:
                    units.append((separator, piece))
                    separator = " "
            separator = "\n"
    result: list[str] = []
    buffer = ""
    for separator, unit in units:
        candidate = f"{buffer}{separator}{unit}" if buffer else unit
        if buffer and len(candidate) > target_chars:
            result.append(buffer)
            buffer = unit
        else:
            buffer = candidate
    if buffer:
        result.append(buffer)
    return result


class GenericPdfParser:
    name = "generic-pypdf-v4"

    def __init__(
        self,
        *,
        arxiv_cache_dir: str | Path,
        max_download_bytes: int,
        max_pages: int,
        arxiv_timeout_seconds: float = 45.0,
        retriever: ArxivRetriever | None = None,
    ) -> None:
        self.retriever = retriever or ArxivRetriever(
            arxiv_cache_dir,
            max_bytes=max_download_bytes,
            max_pages=max_pages,
            timeout_seconds=arxiv_timeout_seconds,
        )
        self.max_pages = max_pages

    def parse_arxiv(self, arxiv_id: str) -> ParsedDocument:
        retrieved = self.retriever.retrieve(arxiv_id)
        return self.parse_pdf(
            str(retrieved.pdf_path),
            arxiv_id=retrieved.arxiv_id,
            arxiv_metadata=retrieved.metadata,
        )

    def parse_pdf(
        self,
        path: str,
        *,
        arxiv_id: str | None = None,
        arxiv_metadata: ArxivMetadata | None = None,
    ) -> ParsedDocument:
        pdf_path = Path(path)
        try:
            raw = pdf_path.read_bytes()
            reader = PdfReader(str(pdf_path))
            if reader.is_encrypted:
                raise PdfParseError("The PDF is encrypted and cannot be parsed.", code="PDF_PASSWORD_PROTECTED")
            page_count = len(reader.pages)
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError("The PDF is damaged or unreadable.") from exc
        if page_count < 1:
            raise PdfParseError("The PDF has no readable pages.")
        if page_count > self.max_pages:
            raise PdfParseError(
                f"The PDF has {page_count} pages; the limit is {self.max_pages}.", code="PAGE_LIMIT_EXCEEDED",
            )

        digest = hashlib.sha256(raw).hexdigest()
        paper_id = _paper_id(arxiv_id, digest)
        page_texts = [_clean(page.extract_text()) for page in reader.pages]
        nonempty = [text for text in page_texts if text]
        total_chars = sum(len(text) for text in nonempty)
        if total_chars < 500:
            raise PdfParseError(
                "The PDF has too little extractable text. It may be a scan or not a paper.", code="EMPTY_TEXT",
            )

        warnings: list[str] = []
        if len(nonempty) < max(1, page_count // 2):
            warnings.append("TEXT_EXTRACTION_LOW_COVERAGE")
        # A section can start halfway down a page. Segment in reading order,
        # inheriting the previous section across page boundaries.
        sections = [Section(
            section_id=f"{paper_id}_sec_000", title="Front Matter", level=1,
            page_start=1, page_end=1,
        )]
        current_section = sections[0]
        section_roles = {current_section.section_id: "front_matter"}
        section_stack: list[tuple[int, str]] = []
        seen_titles: set[tuple[int, str]] = set()
        appendix_allowed = False
        introduction_seen = False
        last_top_number = 0
        blocks: list[TextBlock] = []
        element_candidates: list[tuple[int, Section, str, re.Match[str]]] = []

        def flush(lines: list[str], page_no: int, section: Section) -> None:
            for chunk in _chunks("\n".join(lines)):
                blocks.append(TextBlock(
                    text=chunk, page_start=page_no, page_end=page_no,
                    chunk_id=f"{paper_id}_chunk_{len(blocks):04d}",
                    section_id=section.section_id,
                    section_title=section.title,
                    section_role=section_roles[section.section_id],
                    location_confidence=Confidence.HIGH,
                ))
            lines.clear()

        for page_no, text in enumerate(page_texts, 1):
            buffer: list[str] = []

            for line in text.splitlines():
                detected = _heading(line, allow_appendix=appendix_allowed)
                # Before the first known section, numbered lines are usually affiliations
                # ("1 University of ..."); only role-bearing headings open a section there.
                if detected and len(sections) == 1 and not _section_role(detected[1]):
                    detected = None
                # Section numbers never restart; "1. Reasoning: ..." after section 7 is a list item.
                top_number = re.match(r"(\d+)(?:\.\d+)*\.?\s", _clean(line))
                if detected and top_number and int(top_number.group(1)) < last_top_number:
                    detected = None
                if detected and detected not in seen_titles:
                    if top_number:
                        last_top_number = int(top_number.group(1))
                    flush(buffer, page_no, current_section)
                    seen_titles.add(detected)
                    level, title = detected
                    current_section = Section(
                        section_id=f"{paper_id}_sec_{len(sections):03d}",
                        title=title, level=level, page_start=page_no, page_end=page_no,
                    )
                    sections.append(current_section)
                    while section_stack and section_stack[-1][0] >= level:
                        section_stack.pop()
                    letter_heading = (
                        appendix_allowed and _APPENDIX_HEADING.fullmatch(_heading_text(line)) is not None
                        and _APPENDIX_HEADING.fullmatch(_heading_text(line)).group(2).isupper()
                    ) or (appendix_allowed and _letter_heading(_heading_text(line)) is not None)
                    role = "appendix" if letter_heading else _section_role(title)
                    if not role:
                        if section_stack:
                            role = section_stack[-1][1]
                        elif level == 1 and appendix_allowed:
                            role = "appendix"
                        elif level == 1 and introduction_seen:
                            role = "other"
                        else:
                            role = ""
                    section_roles[current_section.section_id] = role
                    section_stack.append((level, role))
                    if role == "introduction":
                        introduction_seen = True
                    if role in {"references", "appendix"}:
                        appendix_allowed = True
                current_section.page_end = page_no
                buffer.append(line)
                caption_match = _ELEMENT_CAPTION.match(line)
                if caption_match:
                    element_candidates.append((page_no, current_section, line, caption_match))
            flush(buffer, page_no, current_section)

        elements: list[PaperElement] = []
        seen_elements: set[tuple[str, str]] = set()
        links: list[PaperLink] = []
        seen_links: set[str] = set()
        for page_no, section, line, match in element_candidates:
            raw_kind = match.group("kind").lower()
            kind = "figure" if raw_kind.startswith("fig") else "theorem" if raw_kind in {
                "theorem", "lemma", "proposition"
            } else "table"
            number = match.group("number")
            key = (kind, number)
            if key in seen_elements:
                continue
            seen_elements.add(key)
            elements.append(
                PaperElement(
                    element_id=f"{paper_id}_{kind}_{number.replace('.', '_')}",
                    element_type=kind,
                    label=f"{kind.title()} {number}",
                    caption=_clean(line)[:500],
                    page_number=page_no,
                    section_id=section.section_id,
                    section_role=section_roles[section.section_id],
                    parse_quality=ParseQuality.PARTIAL,
                )
            )
        for text in page_texts:
            for match in _URL.finditer(text):
                url = match.group(0).rstrip(".,;)")
                if url in seen_links:
                    continue
                seen_links.add(url)
                links.append(PaperLink(url=url, link_type="code" if "github.com" in url.lower() else "other"))

        if any(element.element_type == "table" for element in elements):
            warnings.append("TABLE_STRUCTURE_UNVERIFIED")
        # Unverified table structure is carried by each table element's own
        # PARTIAL quality; it does not make the extracted body text unusable.
        quality = ParseQuality.PARTIAL if set(warnings) - {"TABLE_STRUCTURE_UNVERIFIED"} else ParseQuality.GOOD

        first_page_lines = [line.strip() for line in page_texts[0].splitlines() if line.strip()]
        vocabulary = {word.lower() for text in page_texts for word in re.findall(r"[A-Za-z]{2,}", text)}
        metadata = reader.metadata or {}
        metadata_title = _clean(getattr(metadata, "title", None) or metadata.get("/Title"))
        title = (
            arxiv_metadata.title if arxiv_metadata else metadata_title
        ) or _title_from_first_page(
            first_page_lines, headers=_running_headers(page_texts), vocabulary=vocabulary,
        )
        abstract = arxiv_metadata.abstract if arxiv_metadata else None
        if not abstract:
            abstract_blocks = [block.text for block in blocks if block.section_title.lower() == "abstract"]
            if abstract_blocks:
                abstract_lines = "\n".join(abstract_blocks).splitlines()
                if abstract_lines and _heading(abstract_lines[0]) == (1, "Abstract"):
                    abstract_lines = abstract_lines[1:]
                abstract = _clean("\n".join(abstract_lines))

        paper = Paper(
            paper_id=paper_id,
            title=title,
            authors=arxiv_metadata.authors if arxiv_metadata else [],
            abstract=abstract,
            language="en",
            page_count=page_count,
            arxiv_id=arxiv_id,
            doi=None,
            sections=sections,
            parse_quality=quality,
            parse_warnings=warnings,
        )
        return ParsedDocument(
            paper=paper,
            blocks=blocks,
            elements=elements,
            links=links,
            parse_quality=quality,
            warnings=warnings,
            source_sha256=digest,
            source_path=str(pdf_path),
            parser_name=self.name,
            source_categories=arxiv_metadata.categories if arxiv_metadata else [],
        )
