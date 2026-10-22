"""Bounded official arXiv fallback for otherwise unconfirmed references.

This is bibliographic verification, not an LLM search. A cited identifier is
never replaced with another record; title-only searches require the complete
available author list and an exact title after PDF typography normalization.
Actual source dates, including pinned old versions, explain cited years.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from xml.etree import ElementTree

from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_matching import _authors_agree, _doi, _name, _title
from app.credibility.reference_sources import (
    ReferenceConfirmation,
    ReferenceFallback,
    ReferenceObservation,
)
from app.credibility.reference_version_transport import VersionFetch, _parse_atom, fetch_arxiv
from app.credibility.snapshots import SnapshotError, capture_snapshot, read_snapshot

REFERENCE_ARXIV_VERSION = "reference-arxiv-1.2"
_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v[1-9]\d*)?", re.I | re.ASCII)
_ET_AL = re.compile(r"\bet\s+al\b", re.I)
_STOP = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of", "on", "the", "to", "with"}


def _identifier(value: str | None) -> str:
    text = (value or "").strip()
    text = re.sub(r"^(?:https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)", "", text, flags=re.I)
    text = re.sub(r"\.pdf$", "", text, flags=re.I)
    return text.casefold() if _ID.fullmatch(text) else ""


def _base(identifier: str) -> str:
    return re.sub(r"v\d+$", "", identifier)


def _authors(cited: list[str], official: list[str], *, abbreviated: bool) -> bool:
    if not cited or not official:
        return False
    if _authors_agree(cited, official, complete=True) is True:
        return True
    if not abbreviated or len(cited) >= len(official):
        return False
    available = list(official)
    for author in cited:
        index = next((i for i, value in enumerate(available)
                      if _authors_agree([author], [value], complete=True) is True), None)
        if index is None:
            return False
        available.pop(index)
    return True


def _observation(fetch: VersionFetch) -> ReferenceObservation:
    return ReferenceObservation(fetch.url, fetch.fetch_status, fetch.http_status, fetch.retrieved_at,
                                fetch.is_cached, fetch.snapshot_ref, "arxiv")


def _archived(fetch: VersionFetch) -> bool:
    """A successful in-memory record must still agree with its immutable XML."""
    if fetch.fetch_status != "ok":
        return False
    try:
        snapshot = read_snapshot(fetch.snapshot_ref)
        return (snapshot.manifest.get("fetch_status") == "ok" and snapshot.raw is not None
                and _parse_atom(snapshot.raw) == fetch.entries
                and snapshot.normalized_json == {"entries": fetch.entries})
    except (SnapshotError, OSError, ValueError, UnicodeError, ElementTree.ParseError):
        return False


def _years(record: dict) -> set[int]:
    return {int(value[:4]) for key in ("published", "updated")
            if isinstance(value := record.get(key), str) and re.match(r"\d{4}-", value)}


def _queries(parsed: CredibilityBibliographicMetadata) -> list[str]:
    name = _name(parsed.authors[0]) if parsed.authors else None
    if not parsed.title or not name:
        return []
    family = re.sub(r'[^\w-]', "", name[0])
    title = re.sub(r'["\\\x00-\x1f\x7f]', " ", parsed.title)
    exact = f'ti:"{title}" AND au:"{family}"'
    # One alternate query recovers line-break/PDF word spacing without changing
    # the identity rule. All returned titles are checked against the full title.
    words = list(dict.fromkeys(word for word in _title(title).words if len(word) >= 4 and word not in _STOP))
    selected = sorted(words, key=lambda word: (-len(word), words.index(word)))[:6]
    if len(selected) < 2:
        return [exact]
    broad = " AND ".join(f'ti:"{word}"' for word in selected) + f' AND au:"{family}"'
    return [exact, broad] if broad != exact else [exact]


class _HistoryHTML(HTMLParser):
    """Read dates only from the explicitly named official history container."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if self.depth:
            if tag not in {"br", "meta", "link", "img", "input", "hr"}:
                self.depth += 1
        elif "submission-history" in (values.get("class") or "").split():
            self.depth = 1
        if self.depth and tag == "a":
            match = re.fullmatch(r"/abs/(.+)", values.get("href") or "")
            if match and (identifier := _identifier(match[1])):
                self.ids.append(identifier)

    def handle_endtag(self, tag):
        if self.depth and tag not in {"br", "meta", "link", "img", "input", "hr"}:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


@dataclass(frozen=True)
class _History:
    dates: dict[int, str]
    snapshot_ref: str


def _history(identifier: str, result: ReferenceFallback) -> _History | None:
    # The transport enforces an allowlist, shared pacing/cache and bounded body;
    # no URL extracted from HTML is ever fetched.
    from app.credibility.reference_publisher_http import fetch_official

    fetch = fetch_official(f"https://arxiv.org/abs/{_base(identifier)}")
    result.observations.append(ReferenceObservation(
        fetch.url, fetch.fetch_status, fetch.http_status, fetch.retrieved_at,
        fetch.is_cached, fetch.snapshot_ref, "arxiv",
    ))
    if fetch.fetch_status != "ok":
        return None
    try:
        snapshot = read_snapshot(fetch.snapshot_ref)
        if snapshot.manifest.get("fetch_status") != "ok" or snapshot.raw != fetch.raw or not snapshot.raw:
            return None
        parser = _HistoryHTML()
        parser.feed(snapshot.raw.decode("utf-8", errors="strict"))
        if any(_base(value) != _base(identifier) for value in parser.ids):
            return None
        text = " ".join(parser.parts)
        dates = {}
        for match in re.finditer(
            r"\[v([1-9]\d*)\]\s*(?:[A-Za-z]{3},?\s+)?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
            r"\s+(\d{2}:\d{2}:\d{2})\s+UTC", text,
        ):
            stamp = datetime.strptime(f"{match[2]} {match[3]}", "%d %b %Y %H:%M:%S").isoformat() + "Z"
            version = int(match[1])
            if version in dates and dates[version] != stamp:
                return None
            dates[version] = stamp
        return _History(dates, fetch.snapshot_ref) if dates else None
    except (SnapshotError, OSError, UnicodeError, ValueError):
        return None


def _older_ids(identifier: str, year: int | None, history: _History | None, *, pinned: str) -> list[str]:
    """Prefer actual cited-year versions; at most four pinned metadata records."""
    latest = re.search(r"v(\d+)$", identifier)
    highest = int(latest[1]) if latest else 1
    wanted = []
    if pinned and pinned != identifier and re.search(r"v\d+$", pinned):
        wanted.append(int(pinned.rsplit("v", 1)[1]))
    if history and year is not None:
        same_year = sorted(version for version, stamp in history.dates.items() if int(stamp[:4]) == year)
        wanted.extend(reversed(same_year))
        before = [version for version, stamp in history.dates.items() if int(stamp[:4]) < year]
        if before:
            wanted.append(max(before))
    wanted.extend(range(1, min(highest, 5)))
    numbers = list(dict.fromkeys(number for number in wanted if number > 0 and number != highest))[:4]
    return [f"{_base(identifier)}v{number}" for number in numbers]


def _confirmation(parsed, record, observation, all_records, observations, *, history=None, note=""):
    metadata = CredibilityBibliographicMetadata(
        title=record["title"], authors=record["authors"], year=parsed.year,
        doi=record.get("doi"), arxiv_id=record["id"],
    )
    source = read_snapshot(observation.snapshot_ref)
    normalized = {
        "relationship": "official-arxiv-record", "parsed_metadata": parsed.model_dump(mode="json"),
        "confirmed_metadata": metadata.model_dump(mode="json"), "selected_record": record,
        "selected_snapshot_ref": observation.snapshot_ref,
        "source_snapshot_refs": [entry.snapshot_ref for entry in observations],
        "version_records": [{"record": entry, "snapshot_ref": item.snapshot_ref} for entry, item in all_records],
        "submission_history_snapshot_ref": history.snapshot_ref if history else None,
        "submission_history_dates": history.dates if history else None,
        "cited_year": parsed.year, "source_years": sorted(_years(record)), "note": note,
        "year_basis": ("citation_year_missing" if parsed.year is None else
                       "atom_published_or_updated" if parsed.year in _years(record) else
                       "official_submission_history"),
    }
    proof = capture_snapshot(
        kind="reference_arxiv_confirmation", raw=source.raw, normalized_json=normalized,
        requested_url=observation.url, final_url=observation.url, retrieved_at=observation.retrieved_at,
        fetch_status="ok", http_status=200, is_cached=observation.is_cached, parent_ref=observation.snapshot_ref,
        metadata={"provider": "arxiv", "resolver_version": REFERENCE_ARXIV_VERSION},
    )
    reason = "Official arXiv record confirms normalized title, cited authors and source-backed dates."
    if parsed.year is None:
        reason += " The citation contains no year; its missing year is retained."
    if note:
        reason += " " + note
    return ReferenceConfirmation(metadata, proof, observation.url, reason, "arxiv")


def resolve_arxiv(parsed: CredibilityBibliographicMetadata, *, raw_text: str) -> ReferenceFallback:
    """Confirm a reference using a cited ID or at most two official searches."""
    result = ReferenceFallback()
    if not parsed.title or not parsed.authors:
        result.reason_code, result.reason = "insufficient_metadata", "A title and cited author list are required."
        return result
    cited_id = _identifier(parsed.arxiv_id)
    if parsed.arxiv_id and not cited_id:
        result.reason_code, result.reason = "invalid_arxiv_id", "The cited arXiv identifier is not valid."
        return result
    abbreviated = bool(_ET_AL.search(raw_text))
    pinned = bool(re.search(r"v\d+$", cited_id))
    attempts = ([{"id_list": cited_id, "max_results": "1"}] if cited_id else [
        {"search_query": query, "max_results": "5"} for query in _queries(parsed)
    ])
    last_reason = ("target_not_found_in_scope", "No target was returned within the bounded official arXiv search.")
    investigated_versions = False
    for params in attempts:
        fetched = fetch_arxiv(params)
        result.observations.append(_observation(fetched))
        if not _archived(fetched):
            last_reason = ("source_fetch_failed",
                           "Official arXiv metadata could not be retrieved and archived reliably.")
            # Search retries do not bypass a provider's rate limit or outage.
            break
        eligible = []
        for record in fetched.entries:
            if cited_id and _base(record["id"]) != _base(cited_id):
                last_reason = ("identifier_mismatch", "The returned arXiv ID does not equal the cited identifier.")
                continue
            if pinned and record["id"] != cited_id:
                # An upstream response may contain the latest record despite a
                # pinned request. Only the exact cited version can bind it; its
                # authors/title must be checked after retrieving that version.
                eligible.append(record)
                last_reason = ("version_evidence_missing",
                               "Official metadata did not return the explicitly cited arXiv version.")
                continue
            same_title = _title(parsed.title).joined == _title(record.get("title")).joined
            if not _authors(parsed.authors, record.get("authors", []), abbreviated=abbreviated):
                if cited_id or same_title:
                    last_reason = ("author_mismatch",
                                   "Official arXiv author metadata does not confirm the cited author list.")
                continue
            if parsed.doi and record.get("doi") and _doi(parsed.doi) != _doi(record["doi"]):
                last_reason = ("identifier_mismatch",
                               "The cited DOI conflicts with the arXiv record's publication DOI.")
                continue
            if cited_id and same_title:
                result.identity_snapshot_ref = fetched.snapshot_ref
            if same_title and (parsed.year is None or parsed.year in _years(record)):
                note = ("The cited year is the first-submission year of this same arXiv record."
                        if parsed.year is not None and parsed.year == int(record["published"][:4])
                        and parsed.year != int(record["updated"][:4]) else "")
                result.confirmations.append(_confirmation(parsed, record, fetched, [(record, fetched)],
                                                           result.observations, note=note))
                result.reason_code, result.reason = "confirmed", result.confirmations[-1].reason
                return result
            if cited_id or same_title:
                eligible.append(record)
                last_reason = (
                    ("year_mismatch", "The cited year is absent from the retrieved arXiv version dates.") if same_title
                    else ("version_evidence_missing", "The cited title requires old-version evidence.")
                )
        if not eligible or investigated_versions:
            continue
        # One identity candidate per attempt is the bounded old-version scope.
        current = eligible[0]
        investigated_versions = True
        history = _history(current["id"], result)
        older_ids = _older_ids(current["id"], parsed.year, history, pinned=cited_id)
        records = [(current, fetched)]
        if older_ids:
            older = fetch_arxiv({"id_list": ",".join(older_ids), "max_results": str(len(older_ids))})
            result.observations.append(_observation(older))
            if _archived(older):
                records.extend((record, older) for record in older.entries
                               if _base(record["id"]) == _base(current["id"]))
            else:
                last_reason = ("source_fetch_failed", "Old arXiv version metadata could not be retrieved reliably.")
        for record, observation in records:
            if cited_id and (_base(record["id"]) != _base(cited_id)
                             or (pinned and record["id"] != cited_id)):
                continue
            if (_title(parsed.title).joined != _title(record.get("title")).joined
                    or not _authors(parsed.authors, record.get("authors", []), abbreviated=abbreviated)):
                continue
            if parsed.doi and record.get("doi") and _doi(parsed.doi) != _doi(record["doi"]):
                last_reason = ("identifier_mismatch",
                               "The cited DOI conflicts with the cited arXiv version's publication DOI.")
                continue
            if cited_id:
                result.identity_snapshot_ref = observation.snapshot_ref
            version = re.search(r"v(\d+)$", record["id"])
            history_date = history.dates.get(int(version[1])) if history and version else None
            if (parsed.year is not None and parsed.year not in _years(record)
                    and (not history_date or int(history_date[:4]) != parsed.year)):
                last_reason = ("year_mismatch", "Official source dates do not establish the cited year.")
                continue
            note = ("An explicitly captured older version of the same arXiv ID establishes the cited title."
                    if record["title"] != current["title"] else "")
            result.confirmations.append(_confirmation(parsed, record, observation, records,
                                                       result.observations, history=history, note=note))
            result.reason_code, result.reason = "confirmed", result.confirmations[-1].reason
            return result
        # Never follow a failed explicit ID into another title search.
        if cited_id:
            break
    result.reason_code, result.reason = last_reason
    return result
