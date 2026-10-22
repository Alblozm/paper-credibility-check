"""Conservative bibliographic identity matching, separate from retrieval.

PDF typography can change word boundaries without changing a title. Those
repairs still require compatible authors and dates. A different publication
year or a changed title requires a separately captured publication/version
link; a shared title, author or arXiv-looking URL alone is not such a link.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import unquote

from app.credibility.models import CredibilityBibliographicMetadata, CredibilityReferenceCandidate

REFERENCE_MATCHING_VERSION = "reference-matching-1.2-name-typos"

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_SNAPSHOT = re.compile(r"snapshot:v1:[0-9a-f]{64}")
_EDITION = re.compile(
    r"\s*[,;:(\[]?\s*(?P<edition>\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth)"
    r"\s+(?:edition|ed\.?)\s*[).\]]?\s*$",
    re.I,
)
_EDITION_NUMBERS = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5"}
_RELATIONSHIPS = {"is-preprint-of", "is-version-of", "official-publication-link", "dated-preprint-metadata"}
_AUTHOR_NOISE = {"references", "reference", "and", "et", "al", "jr", "sr"}


@dataclass(frozen=True)
class VersionLink:
    """Caller-verified identity link, backed by an immutable external snapshot.

    The caller is responsible for reading and validating the official source:
    this matcher never interprets arbitrary text as evidence of a relationship.
    ``cited_title`` is necessary when the publication has changed its title.
    ``cited_year`` can bind a cited version's date, including detecting an
    inconsistent date in the citation rather than silently overlooking it.
    """

    cited_arxiv_id: str
    candidate_doi: str
    evidence_ref: str
    relationship: str
    cited_title: str | None = None
    cited_year: int | None = None
    cited_authors: tuple[str, ...] = ()
    candidate_type: str | None = None
    publication_year_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class _Title:
    words: tuple[str, ...]
    joined: str
    edition: str | None


def _title(value: str | None) -> _Title:
    text = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    text = re.sub(r"<[^>]+>", " ", text).replace("\u00ad", "").replace("&", " and ")
    match = _EDITION.search(text)
    edition = None
    if match:
        label = match.group("edition")
        edition = _EDITION_NUMBERS.get(label, re.sub(r"\D", "", label))
        text = text[:match.start()]
    words = tuple(_WORDS.findall(text))
    return _Title(words=words, joined="".join(words), edition=edition)


def _title_agrees(left: _Title, right: _Title) -> tuple[bool, bool]:
    """Return (compatible, needs_author_confirmation) without deleting content."""
    if not left.joined or not right.joined:
        return False, False
    if left.edition and right.edition and left.edition != right.edition:
        return False, False
    if left.joined == right.joined:
        # Removing whitespace/hyphens is more permissive than punctuation-only
        # normalization, so author confirmation is mandatory for those repairs.
        return True, left.words != right.words or left.edition != right.edition
    # Preserve a small typo tolerance, but never accept an added subtitle or
    # companion-paper suffix merely because the remaining words overlap.
    a, b = set(left.words), set(right.words)
    overlap = len(a & b) / len(a | b) if a and b else 0.0
    compatible = (
        len(left.joined) >= 30
        and len(left.words) == len(right.words)
        and overlap >= 0.85
        and SequenceMatcher(None, left.joined, right.joined).ratio() >= 0.96
    )
    return compatible, compatible


def _name(value: str) -> tuple[str, str, str] | None:
    value = unicodedata.normalize("NFKD", value)
    # A PDF may separate the capital first glyph of a word: "V aliant".
    # Restrict this repair to a capital singleton followed by lowercase text;
    # ordinary initials before a capital surname remain separate initials.
    value = re.sub(r"\b([A-Z])\s+([a-z]{2,})\b", r"\1\2", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"(?<=\w)[-‐‑](?=\w)", "", value)
    words = [word for word in _WORDS.findall(value) if word not in _AUTHOR_NOISE]
    if not words:
        return None
    joined = "".join(words)
    if "," in value:
        family_words = [word for word in _WORDS.findall(value.split(",", 1)[0]) if word not in _AUTHOR_NOISE]
        if family_words:
            family = family_words[-1]
            given = [word for word in _WORDS.findall(value.split(",", 1)[1]) if word not in _AUTHOR_NOISE]
            return family, given[0] if given else "", joined
    return words[-1], words[0] if len(words) > 1 else "", joined


def _one_edit(a: str, b: str) -> bool:
    """True for surnames of at least five letters differing by one edit or one adjacent swap."""
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _authors_agree(left: Sequence[str], right: Sequence[str], *, complete: bool = False) -> bool | None:
    """Compare surnames and given-name initials, allowing order/initial variants."""
    a = [name for value in left if (name := _name(value))]
    b = [name for value in right if (name := _name(value))]
    if not a or not b:
        return None
    if complete and len(a) != len(b):
        return False

    def agrees(x: tuple[str, str, str], y: tuple[str, str, str]) -> bool:
        # The entire ordered name must have identical letters. This handles
        # "Ben-Zaken"/"Ben Zaken" and "Kart hik"/"Karthik" without declaring
        # genuinely different names (e.g. Sophie/Yiding) equivalent.
        if x[2] == y[2]:
            return True
        given = (not x[1] or not y[1]) or (
            x[1] == y[1] if len(x[1]) > 1 and len(y[1]) > 1 else x[1][0] == y[1][0]
        )
        if x[0] != y[0]:
            # A one-letter citation typo ("Schimdt"/"Schmidt") in a longer surname,
            # only when the given name is present and agrees.
            return bool(x[1] and y[1]) and given and _one_edit(x[0], y[0])
        return given

    # First authors must appear on the other record even when a provider changes
    # order. Requiring both directions avoids matching a different work merely
    # because it happens to share one author.
    if not any(agrees(a[0], other) for other in b) or not any(agrees(b[0], other) for other in a):
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    available = list(longer)
    matches = 0
    for author in shorter:
        for index, other in enumerate(available):
            if agrees(author, other):
                matches += 1
                available.pop(index)
                break
    return matches == len(shorter) if complete else matches >= (len(shorter) + 1) // 2


def _doi(value: str | None) -> str:
    text = unquote(value or "").strip().casefold()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", text)
    return text.rstrip(".,;")


def _arxiv(value: str | None) -> str:
    text = (value or "").strip().casefold()
    text = re.sub(r"^(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)", "", text)
    text = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", text)
    if re.fullmatch(r"\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7}", text):
        return text
    return ""


def _version_link_agrees(
    parsed: CredibilityBibliographicMetadata,
    other: CredibilityBibliographicMetadata,
    link: VersionLink | None,
) -> bool:
    if not isinstance(link, VersionLink) or link.relationship not in _RELATIONSHIPS:
        return False
    if not isinstance(link.evidence_ref, str) or not _SNAPSHOT.fullmatch(link.evidence_ref):
        return False
    cited_id = _arxiv(parsed.arxiv_id)
    linked_id = _arxiv(link.cited_arxiv_id)
    if not linked_id or (cited_id and cited_id != linked_id):
        return False
    if not _doi(other.doi) or _doi(other.doi) != _doi(link.candidate_doi):
        return False
    if parsed.year is not None and link.cited_year is not None and parsed.year != link.cited_year:
        return False
    if link.cited_title and not _title_agrees(_title(parsed.title), _title(link.cited_title))[0]:
        return False
    if not cited_id and not (
        link.cited_title
        and _authors_agree(parsed.authors, link.cited_authors) is True
        and parsed.year is not None
        and parsed.year == link.cited_year
    ):
        # A publication page may supply a previously missing preprint ID. In
        # that case independently recorded title, authors and cited date must
        # identify the cited version rather than just the later publication.
        return False
    if link.relationship == "dated-preprint-metadata":
        # This is explicitly an inference from independently captured metadata,
        # not a DOI relationship claimed by the provider. The caller derives
        # the date window from the official preprint's actual version history.
        bounds = link.publication_year_range
        if (
            link.candidate_type not in {"journal-article", "proceedings-article"}
            or not bounds or len(bounds) != 2
            or other.year is None or not bounds[0] <= other.year <= bounds[1]
            or parsed.year is None or parsed.year != link.cited_year
            or not link.cited_title
            or _title(link.cited_title).joined != _title(other.title).joined
            or _title(link.cited_title).joined != _title(parsed.title).joined
            or _authors_agree(link.cited_authors, other.authors, complete=True) is not True
            or _authors_agree(parsed.authors, link.cited_authors) is not True
        ):
            return False
    return True


def classify_reference(
    parsed: CredibilityBibliographicMetadata,
    candidates: Sequence[CredibilityReferenceCandidate],
    *,
    raw_text: str = "",
    version_links: Mapping[str, VersionLink] | None = None,
) -> tuple[str, str | None, str]:
    """Select a confirmed identity, preserving unresolved dates and identities.

    ``raw_text`` is accepted for callers that retain original PDF evidence. It
    never overrides structured authors, dates, or identifiers implicitly.
    """
    del raw_text
    versions = version_links or {}
    for candidate in candidates:
        other = candidate.metadata
        cited_title, found_title = _title(parsed.title), _title(other.title)
        titles_agree, repaired = _title_agrees(cited_title, found_title)
        authors_agree = _authors_agree(parsed.authors, other.authors)
        same_doi = bool(_doi(parsed.doi)) and _doi(parsed.doi) == _doi(other.doi)
        if same_doi:
            a, b = set(cited_title.words), set(found_title.words)
            overlap = len(a & b) / len(a | b) if a and b else 0.0
            if parsed.title and other.title and not titles_agree and overlap < 0.25:
                return (
                    "metadata_conflict", candidate.candidate_id,
                    "The DOI resolves, but its returned title strongly conflicts with the cited title.",
                )
            return "matched", candidate.candidate_id, "DOI matches the public metadata record."
        link = versions.get(candidate.candidate_id)
        linked = authors_agree is True and _version_link_agrees(parsed, other, link)
        if linked and (titles_agree or (link is not None and link.cited_title)):
            if link.relationship == "dated-preprint-metadata":
                return (
                    "matched", candidate.candidate_id,
                    "Date-supported preprint/publication identity inference: normalized title, "
                    "complete external author lists and source-backed publication dates agree "
                    f"({link.evidence_ref}).",
                )
            return (
                "matched", candidate.candidate_id,
                "Compatible authors and an externally captured preprint/publication identity link "
                f"confirm this record ({link.evidence_ref}).",
            )
        if not titles_agree or authors_agree is False or (repaired and authors_agree is not True):
            continue
        if parsed.year is not None and other.year is not None and parsed.year != other.year:
            continue
        if repaired:
            reason = "Normalized title typography, compatible authors and available publication year match."
        else:
            reason = (
                "Title, compatible available authors and available publication year match the public metadata record."
            )
        return "matched", candidate.candidate_id, reason
    if not candidates:
        return (
            "unverified", None,
            "The public metadata query completed but returned no candidate that could be confirmed.",
        )
    return (
        "unverified", None,
        "The public metadata query completed, but no candidate met the conservative title, author and version rule.",
    )
