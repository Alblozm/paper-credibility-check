"""Deterministic reference boundaries and metadata hints from parsed text.

No model or external lookup runs here. Source fragments remain unchanged so a
reference spanning chunks can cite each part without inventing a merged locator.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.parsing.base import TextBlock

REFERENCE_HINTS_VERSION = "reference-hints-1.4-back-references"

SPACE = re.compile(r"\s+")
YEAR = re.compile(r"(?<![\w.])((?:18|19|20)\d{2})[a-z]?(?!\w)")
DOI = re.compile(r"(?:doi\s*[:=]\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.I)
ACM_DOI_CONTINUATION = re.compile(
    r"(?<![\w.])(10\.1145/[0-9]{7})\."
    r"(?:[^\S\r\n]+|[^\S\r\n]*\r?\n[^\S\r\n]*)"
    r"([0-9]{7})(?=[.,;:)\]}]*(?:\s|$))",
    re.I,
)
DOI_ONLY_TITLE = re.compile(
    r"(?:doi\s*[:=]\s*|https?://(?:dx\.)?doi\.org/)"
    r"(?:10\.\d{4,9}/[-._;()/:a-z0-9]+)?\s*[.,;:)\]}]*",
    re.I,
)
ARXIV = re.compile(r"(?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/|abs/)(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
ARXIV_CONTINUATION = re.compile(
    r"((?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/|abs/)\d{4}\.)"
    r"[ \t]*\r?\n[ \t]*(\d{4,5}(?:v\d+)?)(?!\d)", re.I,
)
NUMBER = re.compile(r"(?<!\S)\[(\d{1,4})\]\s+|(?m:^\s*(\d{1,3})\.\s+)")
HEADING = re.compile(r"^(?:references?|bibliography)(?:\s*\(continued\))?\s*$", re.I)
PUBLICATION_HEADER = re.compile(
    r"^published as (?:an? )?(?:conference|journal) (?:paper|article)"
    r"(?: (?:at|in) [^\n]{1,140})?\s*$", re.I,
)
AFTER_REFERENCES = re.compile(
    r"^(?:supplementary (?:material|materials)|appendix(?:\s+[A-Z0-9]+)?|appendices|"
    r"acknowledg(?:e)?ments?|[A-Z]\.\s+(?:appendix|supplementary material))\s*$", re.I
)
BACK_REFERENCES = re.compile(r"(?<=[.!?])[ \t]*\d{1,4}(?:[ \t]*,[ \t]*\d{1,4})*[ \t]*$")
PARTICLES = {"and", "et", "al", "de", "del", "der", "den", "van", "von", "da", "di", "la", "le"}


@dataclass(frozen=True)
class ReferenceFragment:
    chunk_id: str
    text: str


@dataclass(frozen=True)
class ReferenceEntry:
    text: str
    fragments: tuple[ReferenceFragment, ...]

    @property
    def chunk_id(self) -> str:
        return self.fragments[0].chunk_id


def _author_like(value: str) -> bool:
    value = re.sub(r"[´¨ˇ¯˙`]\s*", "", value)
    # PDF character positioning can detach the first capital from its name
    # (Y untian, L iwei). This recognition-only view does not change the name
    # archived in metadata or the evidence fragment.
    value = re.sub(r"\b([A-Z])\s+([a-z]+)\b", r"\1\2", value)
    words = re.findall(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", value, re.UNICODE)
    names = [w for w in words if w.lower() not in PARTICLES]
    lower = sum(not w[0].isupper() for w in names)
    # PDF accent extraction sometimes splits a name (e.g. "Cl´ ement").
    broken_accents = lower <= max(1, len(names) // 6) and bool(re.search(r"[,´¨ˇ¯˙]|\band\b", value))
    return bool(names) and (lower == 0 or broken_accents) and (
        len(names) >= 2 or len(names[0]) >= 3
    )


def _author_split(text: str) -> tuple[str, str] | None:
    """Find an author boundary, skipping initials inside an author list."""
    parenthesised = re.search(r"\(((?:18|19|20)\d{2})[a-z]?\)\s*[.,]?\s+", text)
    prefix = text[:parenthesised.start()] if parenthesised else ""
    has_sentence = any(
        len(re.findall(r"\w+", prefix[:m.start()])[-1]) > 1
        and re.findall(r"\w+", prefix[:m.start()])[-1].lower() not in {"al", "jr", "sr"}
        for m in re.finditer(r"\.\s+", prefix) if re.findall(r"\w+", prefix[:m.start()])
    )
    if parenthesised and not has_sentence and not re.search('["“”]', prefix) and _author_like(prefix):
        return text[:parenthesised.start()].strip(" .,"), text[parenthesised.start():].strip()
    for match in re.finditer(r"\.\s+", text):
        before, after = text[:match.start()], text[match.end():]
        if not _author_like(before):
            continue
        last = re.findall(r"\w+", before)[-1]
        if len(last) == 1:
            # An initial followed by a surname/comma or 'and' is still authors.
            first = after.split(maxsplit=1)[0] if after else ""
            if first.lower() == "and" or first.endswith(",") or len(before.split()) < 2:
                continue
            person = re.split(r",|\band\b", before)[-1].strip().split()
            # Full given name + middle initial + surname: the surname may be
            # followed by a title containing lowercase words. Looking at that
            # whole sentence used to truncate e.g. "William B. Dolan" at B.
            if len(person) >= 2 and len(person[-2].strip(".")) > 1 and first[:1].isupper():
                continue
            last_author = re.split(r"\band\b", before)[-1]
            if (person and "," not in last_author
                    and all(len(word.strip(".")) == 1 for word in person) and first[:1].isupper()):
                continue
        return before.strip(), after.strip()
    return None


def _starts_reference(text: str) -> bool:
    split = _author_split(SPACE.sub(" ", text).strip())
    if split is None:
        return False
    authors, tail = split
    if re.search(r"[\d:/;]|\b(?:pp|doi|isbn|arxiv|proceedings)\b", authors, re.I):
        return False
    names = [w for w in re.findall(r"[^\W\d_]+", authors) if w.casefold() not in PARTICLES]
    # A publication tail such as "Linguistics. doi: ..." is not an author.
    # Single-token organisations remain possible when their capitalisation
    # identifies an acronym/brand (OpenAI, WHO), rather than a venue word.
    author_prefix = len(names) >= 2 or any(sum(c.isupper() for c in w) >= 2 for w in names)
    return author_prefix and len(tail) >= 8 and bool(re.search(r"[a-zA-Z]{3}", tail))


def extract_reference_entries(blocks: list[TextBlock]) -> tuple[list[ReferenceEntry], int | None, list[str]]:
    """Split numbered lists or author-led paragraphs within reference sections.

    Totals refer to reliable boundaries in supplied parsed reference text, never
    to an independently counted original PDF. Ambiguous gaps leave totals unset.
    """
    ranges: list[tuple[int, int, TextBlock]] = []
    chunks: list[str] = []
    ignored_prefixes: dict[int, int] = {}
    offset = 0
    for block in blocks:
        ignored = 0
        for line in block.text.splitlines(keepends=True):
            if not line.strip() or HEADING.fullmatch(line.strip()) or PUBLICATION_HEADER.fullmatch(line.strip()):
                ignored += len(line)
            else:
                break
        ignored_prefixes[offset] = ignored
        # Mask only recognised block-leading headings, retaining every offset
        # into the original block for immutable evidence slices.
        prefix = re.sub(r"[^\r\n]", " ", block.text[:ignored])
        chunks.append(prefix + block.text[ignored:])
        ranges.append((offset, offset + len(block.text), block))
        offset += len(block.text) + 2
    text = "\n\n".join(chunks)
    # Misclassified trailing appendix headings must not become a citation.
    stop = next((m.start() for m in re.finditer(r"(?m)^.*$", text)
                 if AFTER_REFERENCES.fullmatch(m.group().strip())), len(text))
    text = text[:stop]
    gaps: list[str] = []

    def entry(start: int, end: int) -> ReferenceEntry | None:
        fragments_list: list[ReferenceFragment] = []
        for lo, hi, block in ranges:
            fragment_start = max(start, lo + ignored_prefixes[lo])
            if fragment_start >= min(end, hi):
                continue
            fragment = block.text[fragment_start - lo:min(end, hi) - lo].strip()
            if min(end, hi) == hi and block.page_end is not None:
                # Drop only a page's own standalone footer, not a year or an
                # arbitrary numeric line in a reference. The remaining text is
                # still one unchanged substring of its source block.
                fragment = re.sub(r"\n\s*" + re.escape(str(block.page_end)) + r"\s*$", "", fragment).strip()
            if fragment:
                fragments_list.append(ReferenceFragment(block.chunk_id, fragment))
        fragments = tuple(fragments_list)
        if not fragments:
            return None
        return ReferenceEntry(SPACE.sub(" ", "\n".join(f.text for f in fragments)), fragments)

    markers = list(NUMBER.finditer(text))
    spans: list[tuple[int, int]] = []
    if markers:
        labels = [int(m.group(1) or m.group(2)) for m in markers]
        if labels != list(range(1, len(labels) + 1)):
            gaps.append("Reference numbering has missing, repeated, or out-of-order labels; original total is unknown.")
        prefix = text[:markers[0].start()].strip()
        if prefix and not HEADING.fullmatch(prefix):
            gaps.append("Text before the first reference marker could not be safely split.")
        spans = [(m.start(), markers[i + 1].start() if i + 1 < len(markers) else len(text))
                 for i, m in enumerate(markers)]
    else:
        # Paragraphs carry boundaries in the benchmark; single line boundaries
        # are also accepted when a complete author prefix identifies a new item.
        units: list[tuple[int, int]] = []
        for paragraph in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.S):
            start, end = paragraph.span()
            heading = re.match(r"[^\n]+\n", text[start:end])
            if heading and HEADING.fullmatch(heading.group().strip()):
                start += heading.end()
            paragraph_text = text[start:end]
            line_starts = []
            last_start = 0
            # A page-based chunk may open with the tail of a reference begun on the
            # previous page. Once that tail ends with its year, the next author-led
            # line can start a new item instead of the whole chunk becoming one.
            opens_with_tail = not _starts_reference(paragraph_text)
            for newline in re.finditer(r"\n", paragraph_text):
                # Back-reference page lists ("... 2019. 10, 20") follow a finished entry.
                preceding = BACK_REFERENCES.sub("", paragraph_text[last_start:newline.start()])
                following = paragraph_text[newline.end():]
                hints = metadata_hints(preceding)
                # Inspect the complete following author list, which may wrap
                # across several physical lines. An already completed title
                # prevents an interior author-list line or wrapped title from
                # becoming another reference. A missing publication year does
                # not suppress the next independently identifiable citation.
                completed = hints["title"] is not None and (
                    hints["year"] is not None or bool(re.search(r"[.!?]\s*$", preceding))
                ) or (
                    opens_with_tail and last_start == 0
                    and bool(re.search(r"(?:18|19|20)\d{2}[a-z]?\.\s*$", preceding))
                )
                if completed and _starts_reference(following):
                    line_starts.append(start + newline.end())
                    last_start = newline.end()
            starts = [start, *line_starts]
            units.extend((s, starts[i + 1] if i + 1 < len(starts) else end) for i, s in enumerate(starts))
        for start, end in units:
            value = text[start:end].strip()
            if HEADING.fullmatch(value):
                continue
            if _starts_reference(value):
                spans.append((start, end))
            elif spans and (
                not re.search(r"[.!?]\s*$", text[spans[-1][0]:spans[-1][1]])
                or (
                    metadata_hints(text[spans[-1][0]:spans[-1][1]])["year"] is None
                    and any(lo <= start < hi and spans[-1][1] <= lo for lo, hi, _ in ranges)
                    and re.match(r"(?:in\b|proceedings\b|journal\b|arxiv\b|https?://|(?:18|19|20)\d{2})", value, re.I)
                )
            ):
                # A wrapped reference may continue into the next parsed chunk.
                if any(_starts_reference(value[m.end():]) for m in re.finditer(r"\n", value)):
                    gaps.append("Reference continuation contains an unresolved author boundary near: "
                                + SPACE.sub(" ", value)[:100])
                spans[-1] = (spans[-1][0], end)
            else:
                gaps.append("Reference text could not be safely split near: " + SPACE.sub(" ", value)[:100])
    entries: list[ReferenceEntry] = []
    for start, end in spans:
        extracted = entry(start, end)
        if extracted is None or len(extracted.text) < 8:
            gaps.append("A reference boundary had no usable entry text.")
        else:
            entries.append(extracted)
    return entries, len(spans) if spans and not gaps else None, gaps


def metadata_hints(raw: str) -> dict:
    """Conservative query hints; uncertain metadata remains empty."""
    # Line-end hyphens inside words are PDF wrapping, while ordinary inline
    # hyphens (low-rank, Pre-Training) remain untouched. This query-only view
    # never replaces archived fragments or the reference's original text.
    query_text = re.sub(r"(?<=[^\W\d_])-[ \t]*\r?\n[ \t]*(?=[^\W\d_])", "", raw)
    text = SPACE.sub(" ", query_text).strip()
    text = re.sub(r"^(?:\[\d+\]|\d+\.)\s*", "", text)
    # Normalize only the DOI lookup view: source fragments and title spelling
    # retain their original ligatures. Join only ACM's two seven-digit numeric
    # components, across spaces or one wrapped line, never a paragraph boundary.
    doi_text = unicodedata.normalize("NFKC", raw)
    doi_text = ACM_DOI_CONTINUATION.sub(r"\1.\2", doi_text)
    doi_text = re.sub(
        r"(10\.\d{4,9}/(?:[-._;()/:a-z0-9]*[/\-])?)[ \t]*\r?\n[ \t]*([-._;()/:a-z0-9]+)",
        r"\1\2", doi_text, flags=re.I,
    )
    # A numeric arXiv suffix may wrap after the decimal point. Require an arXiv
    # marker and a complete 4/5-digit suffix; do not join unrelated numbers.
    arxiv_text = ARXIV_CONTINUATION.sub(r"\1\2", query_text)
    doi_match, arxiv_match = DOI.search(doi_text), ARXIV.search(SPACE.sub(" ", arxiv_text))
    split = _author_split(text)
    authors_text, tail = split if split else ("", text)
    author_year = re.match(r"^\(?((?:18|19|20)\d{2})[a-z]?\)?(?:[.,]\s*|\s+)", tail)
    title_text = tail[author_year.end():] if author_year else tail
    if author_year:
        year = int(author_year.group(1))
    else:
        cleaned = re.sub(r"https?://\S+", " ", SPACE.sub(" ", ARXIV_CONTINUATION.sub(r"\1\2", doi_text)))
        cleaned = re.sub(r"\b(?:accessed|retrieved|visited|last\s+access)\b.*$", "", cleaned, flags=re.I)
        cleaned = DOI.sub(" ", ARXIV.sub(" ", cleaned))
        cleaned = re.sub(r"\b\d+\s*[-–—]\s*\d+\b", " ", cleaned)
        years = list(YEAR.finditer(cleaned))
        year = int(years[-1].group(1)) if years else None
    title: str | None = None
    quoted = re.search(r'''(?:^|\s)["“‘'](.+?)["”’'](?:[.,]|\s|$)''', text)
    if quoted:
        title = quoted.group(1)
    if split:
        if not quoted:
            end = next((m.start() if m.group().startswith(".") else m.start() + 1
                        for m in re.finditer(r"[.!?](?=\s|$)", title_text)
                        if not re.search(r"\b[A-Z]$", title_text[:m.start()])), len(title_text))
            candidate = title_text[:end].strip(" .,;")
            candidate = re.sub(r",\s*(?:18|19|20)\d{2}[a-z]?$", "", candidate)
            # A bare numeric year is not a title; ambiguous DOI-only records
            # must not trigger a false title-conflict decision.
            if len(candidate) >= 4 and re.search(r"[a-zA-Z]{2}", candidate):
                title = candidate
    if title and DOI_ONLY_TITLE.fullmatch(unicodedata.normalize("NFKC", title)):
        title = None
    if re.search(r"\b[^\W\d_]+,\s+[A-Z](?:\.|$)", authors_text):
        authors = re.split(
            r"\s+and\s+|;\s*|,\s*(?=[^\W\d_]+(?:[-’'][^\W\d_]+)*,\s*[A-Z](?:\.|$))",
            authors_text,
        )
    elif "," in authors_text:
        authors = re.split(r",\s*(?:and\s+)?|\s+and\s+", authors_text)
    else:
        authors = re.split(r"\s+and\s+", authors_text) if authors_text else []
    return {
        "title": title,
        "authors": [a.strip(" ,.") for a in authors
                    if a.strip(" ,.") and not re.fullmatch(r"et\s+al\.?", a.strip(), re.I)],
        "year": year,
        "doi": doi_match.group(1).rstrip(".,;:)]}").casefold() if doi_match else None,
        "arxiv_id": arxiv_match.group(1) if arxiv_match else None,
    }
