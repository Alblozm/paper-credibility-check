"""Source-backed preprint/publication matching evidence; no model calls.

The resolver only investigates already-retrieved formal publication candidates
with compatible authors. It never turns an arXiv search result into a new match
by itself. Crossref remains the candidate source; immutable official metadata
explains a date or title difference. Dated joins are labelled as inference.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from app.credibility.models import CredibilityBibliographicMetadata, CredibilityReferenceCandidate
from app.credibility.reference_matching import VersionLink, _authors_agree, _doi, _title
from app.credibility.reference_version_transport import VersionFetch, fetch_arxiv
from app.credibility.snapshots import SnapshotError, capture_snapshot, read_snapshot

REFERENCE_VERSION_RESOLVER_VERSION = "reference-versions-1.1"
_FORMAL = {"journal-article", "proceedings-article"}


@dataclass
class VersionResolution:
    links: dict[str, VersionLink]
    observations: list[VersionFetch]


def _id(value: str) -> str:
    return re.sub(r"v\d+$", "", value.rsplit("/abs/", 1)[-1])


def _years(record: dict) -> set[int]:
    return {int(value[:4]) for key in ("published", "updated")
            if isinstance(value := record.get(key), str) and re.match(r"\d{4}-", value)}


def _rows(snapshot_ref: str | None) -> dict[str, dict]:
    if not snapshot_ref:
        return {}
    try:
        source = read_snapshot(snapshot_ref)
    except (SnapshotError, OSError):
        return {}
    if source.manifest.get("fetch_status") != "ok":
        return {}
    payload = source.normalized_json
    if not isinstance(payload, dict) or not isinstance(message := payload.get("message"), dict):
        return {}
    records = message.get("items", [message])
    if not isinstance(records, list):
        return {}
    return {_doi(row["DOI"]): row for row in records
            if isinstance(row, dict) and isinstance(row.get("DOI"), str) and row["DOI"]}


def _archived(observation: VersionFetch) -> bool:
    try:
        source = read_snapshot(observation.snapshot_ref)
    except (SnapshotError, OSError):
        return False
    return (observation.fetch_status == "ok" and source.manifest.get("fetch_status") == "ok"
            and isinstance(source.normalized_json, dict)
            and source.normalized_json.get("entries") == observation.entries)


def _cited_authors(left: list[str], right: list[str], *, abbreviated: bool) -> bool:
    if _authors_agree(left, right, complete=True) is True:
        return True
    if not abbreviated or not left or len(left) >= len(right):
        return False
    remaining = list(right)
    for author in left:
        match = next((i for i, value in enumerate(remaining)
                      if _authors_agree([author], [value], complete=True) is True), None)
        if match is None:
            return False
        remaining.pop(match)
    return True


def _title_valid_years(cited: dict, versions: list[tuple[dict, VersionFetch]]) -> tuple[int, int] | None:
    """An old title remains active until the next captured version changes it.

    Year-only citations can refer to the part of that transition year before
    the change. This requires adjacent official versions, not a blanket date
    tolerance or the latest record's unrelated update date.
    """
    version = re.search(r"v(\d+)$", cited["id"])
    if not version or not _years(cited):
        return None
    successor_id = f'{_id(cited["id"])}v{int(version[1]) + 1}'
    for successor, _ in versions:
        if (successor["id"] == successor_id
                and _title(successor.get("title")).joined != _title(cited.get("title")).joined
                and _authors_agree(cited.get("authors", []), successor.get("authors", []), complete=True) is True
                and isinstance(successor.get("updated"), str) and _years(successor)):
            start = int(cited["updated"][:4])
            end = int(successor["updated"][:4])
            if start <= end:
                return start, end
    return None


def resolve_versions(
    parsed: CredibilityBibliographicMetadata,
    candidates: list[CredibilityReferenceCandidate],
    crossref_snapshot_ref: str | None,
    *, raw_text: str = "",
) -> VersionResolution:
    result = VersionResolution({}, [])
    if not parsed.title or parsed.year is None:
        return result
    abbreviated = bool(re.search(r"\bet\s+al\b", raw_text, re.I))
    original_rows = _rows(crossref_snapshot_ref)
    for candidate in candidates:
        other = candidate.metadata
        row = original_rows.get(_doi(other.doi), {})
        if row.get("type") not in _FORMAL or not other.title or not other.doi:
            continue
        if not _cited_authors(parsed.authors, other.authors, abbreviated=abbreviated):
            continue
        same_title = _title(parsed.title).joined == _title(other.title).joined
        same_date = other.year is None or parsed.year == other.year
        if same_title and same_date:
            continue
        left, right = set(_title(parsed.title).words), set(_title(other.title).words)
        if not same_title and (not left or not right or len(left & right) / len(left | right) < 0.3):
            continue
        if parsed.arxiv_id:
            params = {"id_list": _id(parsed.arxiv_id), "max_results": "1"}
        else:
            # A candidate title identifies the latest record; an old title must
            # subsequently be confirmed in a pinned version of that same ID.
            title = re.sub(r'["\\]', " ", other.title)
            family = unicodedata.normalize("NFKD", other.authors[0].split()[-1])
            family = re.sub(r'[^\w-]', "", "".join(c for c in family if not unicodedata.combining(c)))
            params = {"search_query": f'ti:"{title}" AND au:"{family}"', "max_results": "3"}
        latest = fetch_arxiv(params)
        result.observations.append(latest)
        if not _archived(latest):
            return result
        for current in latest.entries:
            if parsed.arxiv_id and _id(current["id"]) != _id(parsed.arxiv_id):
                continue
            if _authors_agree(current.get("authors", []), other.authors, complete=True) is not True:
                continue
            if _title(current.get("title")).joined != _title(other.title).joined:
                continue
            explicit = _doi(current.get("doi")) == _doi(other.doi)
            versions = [(current, latest)]
            if not same_title and explicit:
                version = re.search(r"v(\d+)$", current["id"])
                count = min(3, int(version[1]) - 1) if version else 0
                pinned = re.search(r"v(\d+)$", parsed.arxiv_id or "")
                wanted = set(range(1, count + 1))
                if pinned and version and int(pinned[1]) < int(version[1]):
                    wanted.add(int(pinned[1]))
                if wanted:
                    older = fetch_arxiv({"id_list": ",".join(
                        f'{_id(current["id"])}v{n}' for n in sorted(wanted)
                    ), "max_results": str(len(wanted))})
                    result.observations.append(older)
                    if _archived(older):
                        versions.extend((record, older) for record in older.entries)
            for cited, observation in versions:
                if _id(cited["id"]) != _id(current["id"]):
                    continue
                if _title(cited.get("title")).joined != _title(parsed.title).joined:
                    continue
                title_year_range = _title_valid_years(cited, versions) if explicit and not same_title else None
                if parsed.year not in _years(cited) and not (
                    title_year_range and title_year_range[0] <= parsed.year <= title_year_range[1]
                ):
                    continue
                if not _cited_authors(parsed.authors, cited.get("authors", []), abbreviated=abbreviated):
                    continue
                relationship = "official-publication-link" if explicit else "dated-preprint-metadata"
                years = _years(current)
                if not years:
                    continue
                bounds = (min(years), max(years) + 1)
                if not explicit and (not same_title or other.year is None or not bounds[0] <= other.year <= bounds[1]):
                    continue
                evidence = {
                    "relationship": relationship, "cited_record": cited, "latest_record": current,
                    "candidate_doi": other.doi, "candidate_type": row.get("type"),
                    "publication_year_range": list(bounds), "crossref_snapshot_ref": crossref_snapshot_ref,
                    "latest_snapshot_ref": latest.snapshot_ref, "cited_snapshot_ref": observation.snapshot_ref,
                    "inference": not explicit,
                    "cited_title_valid_year_range": list(title_year_range) if title_year_range else None,
                    "date_inference": parsed.year not in _years(cited),
                    "title_transition_records": [record for record, _ in versions
                        if title_year_range and _id(record["id"]) == _id(cited["id"])],
                }
                try:
                    source = read_snapshot(observation.snapshot_ref)
                except (SnapshotError, OSError):
                    continue
                if source.manifest.get("fetch_status") != "ok":
                    continue
                proof_ref = capture_snapshot(
                    kind="reference_version_link", raw=source.raw, normalized_json=evidence,
                    requested_url=observation.url, final_url=observation.url,
                    retrieved_at=observation.retrieved_at, fetch_status="ok", http_status=200,
                    is_cached=observation.is_cached, parent_ref=observation.snapshot_ref,
                    metadata={"provider": "arxiv", "inference": not explicit},
                )
                result.links[candidate.candidate_id] = VersionLink(
                    cited_arxiv_id=_id(current["id"]), candidate_doi=other.doi,
                    evidence_ref=proof_ref, relationship=relationship, cited_title=cited["title"],
                    cited_year=parsed.year, cited_authors=tuple(cited["authors"]),
                    candidate_type=row.get("type"), publication_year_range=bounds,
                )
                result.observations.append(replace(observation, snapshot_ref=proof_ref))
                return result
        # One compatible formal publication candidate is the bounded scope.
        break
    return result
