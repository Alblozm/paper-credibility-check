"""任务二：引用存在性核查的独立测试。

测试用假的 Crossref 返回值，不访问真实网络；这样 CI 既免费也可重复。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.credibility.context import CheckContext
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.references import CrossrefLookup, _LookupResult, check_references
from app.credibility.shell import CheckSet, assemble_report
from app.parsing.base import ParsedDocument, TextBlock
from app.schemas.enums import ParseQuality
from app.schemas.paper import Paper


class FakeLookup:
    """用预先指定的查询结果替代真实 Crossref 网络调用。"""

    def __init__(self, *results: _LookupResult) -> None:
        self.results = list(results)
        self.calls = 0

    def lookup(self, _metadata: CredibilityBibliographicMetadata) -> _LookupResult:
        result = self.results[self.calls]
        self.calls += 1
        return result


def _ctx(*blocks: TextBlock) -> CheckContext:
    doc = ParsedDocument(
        paper=Paper(paper_id="paper_test", parse_quality=ParseQuality.GOOD),
        blocks=list(blocks),
        parse_quality=ParseQuality.GOOD,
    )
    return CheckContext(
        source={}, repo_root=Path("."), generated_at="2026-10-06T00:00:00Z", ref=None, doc=doc, unresolved_reason=None
    )


def _reference_block(text: str, *, role: str = "references", title: str = "References") -> TextBlock:
    return TextBlock(
        chunk_id="chunk_refs",
        text=text,
        page_start=10,
        page_end=10,
        section_id="sec_refs",
        section_title=title,
        section_role=role,
    )


def _candidate(
    *, title: str = "A Good Paper", doi: str = "10.1000/good", year: int = 2024
) -> CredibilityBibliographicMetadata:
    return CredibilityBibliographicMetadata(title=title, authors=["Jane Doe"], year=year, doi=doi, arxiv_id=None)


def _ok(*candidates: CredibilityBibliographicMetadata) -> _LookupResult:
    return _LookupResult("https://api.crossref.org/works/10.1000/good", "ok", 200, "2026-10-06T00:00:00Z", candidates)


def test_doi_matches_and_attaches_paper_and_external_evidence(monkeypatch: pytest.MonkeyPatch):
    lookup = FakeLookup(_ok(_candidate()))
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    result = check_references(_ctx(_reference_block("[1] Jane Doe. A Good Paper. 2024. doi:10.1000/good")))

    checks = result.checks
    assert checks.extraction_status == "complete"
    assert (checks.original_total, checks.extracted_count, checks.checked_count) == (1, 1, 1)
    assert (checks.matched_count, checks.conflict_count, checks.unverified_count, checks.not_checked_count) == (
        1,
        0,
        0,
        0,
    )
    item = checks.items[0]
    assert item.outcome == "matched" and item.selected_candidate_id == "cand_ref_001_1"
    assert item.paper_evidence_ids == ["ev_ref_001_paper"]
    assert [source.fetch_status for source in result.sources] == ["ok"]
    assert {e.origin for e in result.evidence} == {"paper", "external"}


def test_completed_query_without_confirmed_candidate_is_unverified(monkeypatch: pytest.MonkeyPatch):
    lookup = FakeLookup(_ok(_candidate(title="An Unrelated Work", doi="10.1000/other")))
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    result = check_references(_ctx(_reference_block("[1] Jane Doe. A Good Paper. 2024.")))

    assert result.checks.items[0].outcome == "unverified"
    assert result.checks.checked_count == 1 and result.checks.unverified_count == 1
    assert result.dimension.check_status == "completed"
    assert result.findings[0].kind == "verification_gap"


def test_doi_with_a_strong_title_conflict_is_reported(monkeypatch: pytest.MonkeyPatch):
    lookup = FakeLookup(_ok(_candidate(title="Completely Different Title")))
    monkeypatch.setattr("app.credibility.references._LOOKUP", lookup)
    result = check_references(_ctx(_reference_block("[1] Jane Doe. A Good Paper. 2024. doi:10.1000/good")))

    assert result.checks.items[0].outcome == "metadata_conflict"
    assert result.checks.conflict_count == 1


def test_crossref_lookup_reuses_the_in_process_cache(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    class Response:
        status_code = 200
        is_error = False

        @staticmethod
        def json() -> dict:
            return {
                "message": {
                    "title": ["A Good Paper"],
                    "DOI": "10.1000/good",
                    "issued": {"date-parts": [[2024]]},
                }
            }

    def fake_get(*_args, **_kwargs) -> Response:
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr("app.credibility.references.httpx.get", fake_get)
    lookup = CrossrefLookup(min_interval_seconds=0)
    metadata = _candidate()
    first = lookup.lookup(metadata)
    second = lookup.lookup(metadata)

    assert calls == 1
    assert first.is_cached is False and second.is_cached is True


def test_reference_result_can_be_merged_into_the_report(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.credibility.references._LOOKUP", FakeLookup(_ok(_candidate())))
    ctx = _ctx(_reference_block("[1] Jane Doe. A Good Paper. 2024. doi:10.1000/good"))

    report, _ = assemble_report(
        ctx,
        report_id="cred_report_test",
        checks=CheckSet(references=check_references),
    )

    assert report.checks.references.items[0].outcome == "matched"
    assert report.sources[0].source_id == "src_ref_001_metadata"


def test_timeout_is_not_checked_and_is_not_counted_as_query_completion(monkeypatch: pytest.MonkeyPatch):
    timeout = _LookupResult("https://api.crossref.org/works?query=x", "timeout", None, "2026-10-06T00:00:00Z")
    monkeypatch.setattr("app.credibility.references._LOOKUP", FakeLookup(timeout))
    result = check_references(_ctx(_reference_block("[1] Jane Doe. A Good Paper. 2024.")))

    assert result.checks.items[0].outcome == "not_checked"
    assert result.checks.checked_count == 0 and result.checks.not_checked_count == 1
    assert result.checks.query_completion_ratio == 0
    assert result.dimension.check_status == "partial"
    assert result.sources[0].fetch_status == "timeout"


def test_reference_section_misclassified_as_discussion_is_not_silently_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.credibility.references._LOOKUP", FakeLookup())
    result = check_references(
        _ctx(_reference_block("[1] Jane Doe. A Good Paper. 2024.", role="discussion", title="Discussion"))
    )

    assert result.checks.extraction_status == "failed"
    assert result.checks.original_total is None and result.checks.items == []
    assert result.dimension.check_status == "failed"
    assert "references" in result.checks.gaps[0].casefold()


def test_unreliably_split_reference_text_is_a_reported_gap(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.credibility.references._LOOKUP", FakeLookup())
    result = check_references(_ctx(_reference_block("Jane Doe, A Good Paper, 2024; John Roe, Another Paper, 2023.")))

    assert result.checks.extraction_status == "failed"
    assert result.checks.items == []
    assert "could not be safely split" in result.checks.gaps[0]
