"""可信度核查（任务一）：独立入口、轮询、主张对照、证据定位、条件守卫与计分。"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api import routes_credibility
from app.config import get_settings
from app.credibility.claims import ClaimCandidateSet, SpanInput, build_claim_checks, check_claims
from app.credibility.code_checks import check_code
from app.credibility.context import CheckContext, build_context
from app.credibility.models import (
    CredibilityDimension,
    CredibilityEvidence,
    CredibilityExternalSource,
    ReferenceCheckResult,
)
from app.credibility.references import check_references
from app.credibility.rubric import DRAFT_RUBRIC, CredibilityRubric
from app.credibility.scoring import score_dimensions
from app.credibility.shell import CheckSet, assemble_report, build_completed_assessment
from app.main import app

client = TestClient(app)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_ROOT = next(
    (path for path in (_REPO_ROOT / "data/data", _REPO_ROOT / "benchmark/data") if path.is_dir()),
    _REPO_ROOT / "benchmark/data",
)
_PAPER_002 = _DATA_ROOT / "parsed/paper_002/v1"
_RECORD_002 = _DATA_ROOT / "source_snapshots/paper_002/source_record.json"
# 用户要求：对照只用 Table 3 表注里 GPT-3.5 / GSM8K 的数字，不写入前端原型的另一组数字。
_BANNED = ("9" + "5.5", "9" + "1.5", "8" + "9.0")
needs_benchmark = pytest.mark.skipif(not _PAPER_002.is_dir(), reason="缺少兄弟目录 data/")


def _request(source: dict, **overrides: object) -> dict:
    return {
        "schema_version": "credibility-0.1.0-draft",
        "analysis_mode": "credibility",
        "source": source,
        "model_id": "not_called",
        "rubric_version": "0.1-draft",
        **overrides,
    }


def _arxiv(arxiv_id: str) -> dict:
    return {"type": "arxiv", "arxiv_id": arxiv_id, "upload_id": None}


def _post_and_get(source: dict) -> tuple[dict, dict]:
    created = client.post("/v1/credibility/assessments", json=_request(source))
    assert created.status_code == 202
    queued = created.json()
    fetched = client.get(f"/v1/credibility/assessments/{queued['assessment_id']}")
    assert fetched.status_code == 200
    return queued, fetched.json()


def _jsonl(path: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["chunk_id"]: row for row in rows}


# --- 真实论文 paper_002 -------------------------------------------------------


@needs_benchmark
def test_paper_002_is_queued_then_shows_both_located_ends():
    queued, body = _post_and_get(_arxiv("2310.01798v1"))
    assert queued["status"] == "queued" and queued["stage"] == "queued"
    assert queued["progress_percent"] == 0
    assert queued["report"] is None and queued["paper"] is None and queued["error"] is None

    assert body["status"] == "completed" and body["stage"] == "done"
    assert body["analysis_mode"] == "credibility"
    assert body["data_origin"] == "live"
    assert body["error"] is None

    report = body["report"]
    item = report["checks"]["claims"]["items"][0]
    assert item["outcome"] == "partially_supported"
    assert report["checks"]["claims"]["scope_complete"] is False
    evidence = {e["evidence_id"]: e for e in report["evidence"]}
    claim_end = evidence[item["claim_evidence_ids"][0]]
    table_end = evidence[item["comparison_evidence_ids"][0]]

    chunks = _jsonl(_PAPER_002 / "chunks.jsonl")
    elements = {e["element_id"]: e for e in json.loads((_PAPER_002 / "elements.json").read_text())}
    chunk = chunks[claim_end["paper_locator"]["chunk_id"]]
    assert claim_end["origin"] == "paper" and claim_end["mode"] == "quote"
    assert claim_end["paper_locator"]["page_start"] == chunk["page_start"] == 1
    assert claim_end["excerpt"] in chunk["text_content"]

    element = elements[table_end["paper_locator"]["chunk_id"]]
    assert table_end["paper_locator"]["element_label"] == element["label"] == "Table 3"
    assert table_end["paper_locator"]["page_start"] == element["page_number"] == 5
    for value in ("75.9", "75.1", "74.7"):
        assert value in element["caption"] and value in table_end["excerpt"]
    assert table_end["location_confidence"] == "medium"

    finding = report["findings"][0]
    assert finding["item_ids"] == [item["claim_id"]]
    assert set(finding["evidence_ids"]) == {claim_end["evidence_id"], table_end["evidence_id"]}
    assert finding["certainty"] == "unresolved"
    assert "human_review_pending" in {w["code"] for w in body["warnings"]}

    text = json.dumps(body)
    assert not any(token in text for token in _BANNED)


@needs_benchmark
def test_paper_002_metadata_comes_from_data_group_files():
    _, body = _post_and_get(_arxiv("2310.01798v1"))
    meta = json.loads((_PAPER_002 / "metadata.json").read_text())
    record = json.loads(_RECORD_002.read_text())
    paper = body["paper"]
    assert paper["source_sha256"] == meta["pdf_sha256"]
    assert paper["page_count"] == meta["page_count"]
    assert paper["title"] == record["title"]
    assert paper["language"] == record["language"]
    assert paper["arxiv_id"] == "2310.01798v1"
    assert body["report"]["versions"]["parser_version"] == f"benchmark:{meta['parser_name']}@{meta['parser_version']}"


@needs_benchmark
@pytest.mark.parametrize("value", ["2310.01798", "2310.01798V1", "https://arxiv.org/abs/2310.01798v1"])
def test_arxiv_input_forms_resolve_to_the_parsed_version(value: str):
    _, body = _post_and_get(_arxiv(value))
    assert body["paper"]["paper_id"] == "paper_002"
    assert body["report"]["checks"]["claims"]["items"]


@needs_benchmark
def test_other_parsed_paper_is_live_without_claim_candidates():
    _, body = _post_and_get(_arxiv("2307.08122"))
    assert body["status"] == "completed" and body["data_origin"] == "live"
    assert body["paper"]["paper_id"] == "paper_001"
    claims = body["report"]["dimensions"][0]
    assert claims["check_status"] == "not_run" and claims["applicability"] == "undetermined"
    assert body["report"]["checks"]["claims"]["items"] == []
    assert "claims_not_prepared" in {w["code"] for w in body["warnings"]}


@pytest.mark.parametrize(
    "source",
    [
        _arxiv("2310.01798v2"),
        _arxiv("2501.01234"),
        {"type": "pdf", "arxiv_id": None, "upload_id": "upl_000000000000"},
        {"type": "pdf", "arxiv_id": None, "upload_id": "../../etc/passwd"},
    ],
)
def test_unparsed_inputs_complete_as_empty_shell(source: dict):
    _, body = _post_and_get(source)
    assert body["status"] == "completed" and body["error"] is None
    assert body["paper"] is None and body["data_origin"] == "synthetic"
    report = body["report"]
    assert [d["dimension_id"] for d in report["dimensions"]] == [
        "claim_evidence",
        "reference_verification",
        "code_reproduction",
    ]
    assert all(d["check_status"] == "not_run" for d in report["dimensions"])
    assert report["checks"]["claims"]["items"] == []
    assert report["overall"]["score"] is None and report["overall"]["category"] is None
    assert body["warnings"][0]["code"] == "paper_not_parsed"


def test_unknown_assessment_is_404_and_bad_mode_is_422():
    assert client.get("/v1/credibility/assessments/cred_missing").status_code == 404
    bad = client.post("/v1/credibility/assessments", json=_request(_arxiv("2310.01798v1"), analysis_mode="score"))
    assert bad.status_code == 422


def test_background_failure_marks_task_failed(monkeypatch: pytest.MonkeyPatch):
    def boom(**_kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr(routes_credibility, "build_completed_assessment", boom)
    _, body = _post_and_get(_arxiv("2310.01798v1"))
    assert body["status"] == "failed" and body["stage"] == "failed"
    assert body["report"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR" and body["error"]["retryable"] is True


# --- 临时 benchmark：定位核对与条件守卫 ---------------------------------------

_TABLE_CAPTION = "Table 2: Accuracy on DatasetA. Baseline 80.0 Ours 78.0"


def _fake_benchmark(root: Path, *, pdf_sha: str = "0" * 64) -> Path:
    data = root / "benchmark/data"
    parsed = data / "parsed/paper_900/v1"
    parsed.mkdir(parents=True)
    (parsed / "metadata.json").write_text(
        json.dumps(
            {
                "paper_id": "paper_900",
                "paper_version_id": "paper_900_v1",
                "pdf_sha256": pdf_sha,
                "page_count": 6,
                "parse_quality": "good",
                "parser_name": "fake",
                "parser_version": "0",
            }
        )
    )
    sections = [
        ("paper_900_v1_sec_abstract", "Abstract", "abstract", 1),
        ("paper_900_v1_sec_1", "Introduction", "introduction", 1),
        ("paper_900_v1_sec_4", "Results", "results", 4),
    ]
    (parsed / "sections.json").write_text(
        json.dumps(
            [
                {
                    "section_id": sid,
                    "title": title,
                    "section_role": role,
                    "level": 1,
                    "page_start": page,
                    "page_end": page,
                }
                for sid, title, role, page in sections
            ]
        )
    )
    chunks = [
        (
            "paper_900_v1_chunk_0000",
            "paper_900_v1_sec_abstract",
            1,
            "Our method improves accuracy on DatasetA from 80.0 to 84.0.",
        ),
        ("paper_900_v1_chunk_0001", "paper_900_v1_sec_1", 1, "We introduce a new method."),
        ("paper_900_v1_chunk_0002", "paper_900_v1_sec_4", 4, "Table 2 shows the results under the same protocol."),
    ]
    (parsed / "chunks.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "chunk_id": cid,
                    "section_id": sid,
                    "page_start": page,
                    "page_end": page,
                    "text_content": text,
                    "location_confidence": "high",
                }
            )
            for cid, sid, page, text in chunks
        )
        + "\n"
    )
    (parsed / "elements.json").write_text(
        json.dumps(
            [
                {
                    "element_id": "paper_900_v1_table_2",
                    "section_id": "paper_900_v1_sec_4",
                    "element_type": "table",
                    "label": "Table 2",
                    "caption": _TABLE_CAPTION,
                    "page_number": 4,
                    "extracted_text": None,
                    "parse_quality": "good",
                }
            ]
        )
    )
    record = data / "source_snapshots/paper_900"
    record.mkdir(parents=True)
    (record / "source_record.json").write_text(
        json.dumps(
            {
                "title": "Fake Paper",
                "language": "en",
                "arxiv_id": "2401.00001",
                "arxiv_version": "v1",
            }
        )
    )
    return root


def _claim_set(**claim_overrides: object) -> ClaimCandidateSet:
    claim = {
        "claim_id": "c01",
        "statement": "The method improves DatasetA accuracy.",
        "importance": "core",
        "claim_conditions": {"metric": "accuracy", "dataset": "DatasetA", "baseline": "Baseline", "setting": "main"},
        "claim_evidence": [
            {
                "evidence_id": "ev_claim",
                "chunk_id": "paper_900_v1_chunk_0000",
                "mode": "quote",
                "excerpt": "improves accuracy on DatasetA from 80.0 to 84.0",
            }
        ],
        "comparison_evidence": [
            {
                "evidence_id": "ev_table",
                "element_id": "paper_900_v1_table_2",
                "mode": "paraphrase",
                "excerpt": "Table 2: Baseline 80.0, Ours 78.0.",
                "values": ["80.0", "78.0"],
                "conditions": {"metric": "accuracy", "dataset": "DatasetA", "baseline": "Baseline", "setting": "main"},
            }
        ],
        "search_scope": "Abstract and Table 2.",
        "proposed_outcome": "contradicted",
        "reason": "Table 2 reports 78.0 for the method against 80.0 for the baseline.",
        "review": {"status": "verified", "reviewer": "tester", "reviewed_at": "2026-10-06"},
    }
    claim.update(claim_overrides)
    return ClaimCandidateSet.model_validate(
        {
            "paper_version_id": "paper_900_v1",
            "origin": "prepared",
            "selection_scope": "One claim.",
            "scope_complete": True,
            "scope_limitations": [],
            "claims": [claim],
        }
    )


@pytest.fixture
def fake_ctx(tmp_path: Path) -> CheckContext:
    root = _fake_benchmark(tmp_path)
    return build_context(_arxiv("2401.00001v1"), generated_at="2026-10-06T00:00:00Z", repo_root=root)


def test_pages_and_labels_come_from_the_parse(fake_ctx: CheckContext):
    result = build_claim_checks(fake_ctx, _claim_set())
    by_id = {e.evidence_id: e for e in result.evidence}
    assert by_id["ev_claim"].paper_locator.page_start == 1
    assert by_id["ev_claim"].paper_locator.section_title == "Abstract"
    assert by_id["ev_table"].paper_locator.page_start == 4
    assert by_id["ev_table"].paper_locator.element_label == "Table 2"
    with pytest.raises(ValueError):
        SpanInput.model_validate(
            {"evidence_id": "x", "chunk_id": "c", "mode": "quote", "excerpt": "y", "page_start": 9}
        )


@pytest.mark.parametrize(
    "override,problem",
    [
        (
            {
                "claim_evidence": [
                    {
                        "evidence_id": "ev_claim",
                        "chunk_id": "paper_900_v1_chunk_0000",
                        "mode": "quote",
                        "excerpt": "improves accuracy by 12 points",
                    }
                ]
            },
            "quote for ev_claim was not found",
        ),
        (
            {
                "comparison_evidence": [
                    {
                        "evidence_id": "ev_table",
                        "element_id": "paper_900_v1_table_9",
                        "mode": "paraphrase",
                        "excerpt": "Table 9.",
                        "values": [],
                    }
                ]
            },
            "element_id paper_900_v1_table_9 was not found",
        ),
        (
            {
                "comparison_evidence": [
                    {
                        "evidence_id": "ev_table",
                        "element_id": "paper_900_v1_table_2",
                        "mode": "paraphrase",
                        "excerpt": "Ours 77.5.",
                        "values": ["77.5"],
                    }
                ]
            },
            "value 77.5 does not appear",
        ),
        (
            {
                "claim_evidence": [
                    {
                        "evidence_id": "ev_claim",
                        "chunk_id": "paper_900_v1_chunk_0002",
                        "mode": "quote",
                        "excerpt": "Table 2 shows the results",
                    }
                ]
            },
            "outside ['abstract', 'introduction']",
        ),
        (
            {
                "comparison_evidence": [
                    {
                        "evidence_id": "ev_table",
                        "chunk_id": "paper_900_v1_chunk_0001",
                        "mode": "quote",
                        "excerpt": "We introduce a new method.",
                    }
                ]
            },
            "'introduction' section",
        ),
    ],
)
def test_unlocatable_evidence_is_not_checked(fake_ctx: CheckContext, override: dict, problem: str):
    result = build_claim_checks(fake_ctx, _claim_set(**override))
    item = result.checks.items[0]
    assert item.outcome == "not_checked"
    assert problem in item.reason
    assert result.evidence == [] and result.findings == []
    assert result.dimension.check_status == "failed"


def test_contradiction_with_matching_conditions_is_kept(fake_ctx: CheckContext):
    report, _ = assemble_report(
        fake_ctx,
        report_id="r",
        checks=CheckSet(claims=lambda ctx: build_claim_checks(ctx, _claim_set())),
    )
    assert report.checks.claims.items[0].outcome == "contradicted"
    assert report.findings[0].severity == "major" and report.findings[0].certainty == "confirmed"
    assert [a.type for a in report.alerts if a.type == "material_finding"] == ["material_finding"]


@pytest.mark.parametrize("key,value", [("dataset", "DatasetB"), ("baseline", None), ("setting", "oracle feedback")])
def test_difference_under_other_conditions_is_not_a_contradiction(fake_ctx: CheckContext, key: str, value: str | None):
    conditions = {"metric": "accuracy", "dataset": "DatasetA", "baseline": "Baseline", "setting": "main", key: value}
    claim_set = _claim_set(
        comparison_evidence=[
            {
                "evidence_id": "ev_table",
                "element_id": "paper_900_v1_table_2",
                "mode": "paraphrase",
                "excerpt": "Table 2: Baseline 80.0, Ours 78.0.",
                "values": ["80.0", "78.0"],
                "conditions": conditions,
            }
        ]
    )
    item = build_claim_checks(fake_ctx, claim_set).checks.items[0]
    assert item.outcome == "unable_to_determine"
    assert "not marked as a contradiction" in item.reason


def test_prepared_claims_are_looked_up_by_paper_version(fake_ctx: CheckContext, tmp_path: Path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "paper_900_v1.json").write_text(_claim_set().model_dump_json())
    result = check_claims(fake_ctx, prepared_dir=prepared)
    assert result.checks.items[0].claim_id == "c01"
    assert check_claims(fake_ctx, prepared_dir=tmp_path / "empty").checks.items == []


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_upload_resolves_by_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pdf = _blank_pdf()
    root = _fake_benchmark(tmp_path / "repo", pdf_sha=hashlib.sha256(pdf).hexdigest())
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path / "uploads"))
    upload = client.post("/v1/uploads", files={"file": ("paper.pdf", pdf, "application/pdf")})
    assert upload.status_code == 201
    source = {"type": "pdf", "arxiv_id": None, "upload_id": upload.json()["upload_id"]}
    body = build_completed_assessment(
        assessment_id="cred_pdf",
        model_id="not_called",
        source=source,
        created_at="t",
        started_at="t",
        completed_at="t",
        repo_root=root,
    )
    assert body["paper"]["paper_id"] == "paper_900"
    assert body["paper"]["arxiv_id"] == "2401.00001v1"
    assert body["data_origin"] == "live"


def test_unlisted_paper_uses_the_configured_live_parser(fake_ctx: CheckContext, monkeypatch: pytest.MonkeyPatch):
    class FakeParser:
        def parse_arxiv(self, arxiv_id: str):
            assert arxiv_id == "2502.00002v1"
            return fake_ctx.doc

    monkeypatch.setattr(get_settings(), "parser_provider", "generic")
    monkeypatch.setattr("app.pipeline.orchestrator.build_parser", lambda: FakeParser())
    body = build_completed_assessment(
        assessment_id="cred_live",
        model_id="not_called",
        source=_arxiv("2502.00002v1"),
        created_at="t",
        started_at="t",
        completed_at="t",
        repo_root=fake_ctx.repo_root,
    )
    assert body["data_origin"] == "live"
    assert body["paper"]["paper_id"] == "paper_900"
    assert body["report"]["checks"]["claims"]["items"] == []
    assert "claims_not_prepared" in {w["code"] for w in body["warnings"]}


def test_stub_parser_is_never_used_for_credibility(fake_ctx: CheckContext):
    ctx = build_context(_arxiv("2502.00002v1"), generated_at="t", repo_root=fake_ctx.repo_root)
    assert ctx.doc is None
    assert "parser_provider=stub" in (ctx.unresolved_reason or "")


# --- 与任务二、任务三的接缝 ----------------------------------------------------


def test_reference_and_code_checks_take_the_shared_context():
    for fn in (check_references, check_code):
        params = list(inspect.signature(fn).parameters.values())
        assert [p.name for p in params] == ["ctx"]


def test_external_evidence_from_a_check_is_merged_and_must_link(fake_ctx: CheckContext):
    base = check_references(fake_ctx)
    source = CredibilityExternalSource(
        source_id="src_meta",
        kind="metadata",
        url="https://api.crossref.org/works/10.1/x",
        fetch_status="ok",
        http_status=200,
        retrieved_at="t",
        observed_at="t",
        is_cached=False,
        snapshot_ref=None,
        association_reason="Lookup for ref_01.",
    )
    external = CredibilityEvidence(
        evidence_id="ev_meta",
        origin="external",
        mode="paraphrase",
        excerpt="Crossref record.",
        paper_locator=None,
        source_id="src_meta",
        location_confidence="high",
    )
    good = ReferenceCheckResult(dimension=base.dimension, checks=base.checks, evidence=[external], sources=[source])
    report, _ = assemble_report(fake_ctx, report_id="r", checks=CheckSet(references=lambda ctx: good))
    assert [e.evidence_id for e in report.evidence] == ["ev_meta"]
    assert [s.source_id for s in report.sources] == ["src_meta"]

    orphan = ReferenceCheckResult(dimension=base.dimension, checks=base.checks, evidence=[external], sources=[])
    with pytest.raises(ValueError, match="external evidence ev_meta"):
        assemble_report(fake_ctx, report_id="r", checks=CheckSet(references=lambda ctx: orphan))


def test_reference_extraction_failure_is_distinct_from_completed_bounded_code_search(fake_ctx: CheckContext):
    report, _ = assemble_report(fake_ctx, report_id="r")
    references = report.checks.references
    assert report.dimensions[1].check_status == "failed"
    assert report.dimensions[2].check_status == "completed"
    assert report.checks.code.scope_complete is False
    assert report.execution["checks"]["code_check"]["status"] == "completed"
    assert report.execution["checks"]["reference_check"]["status"] == "failed"
    assert references.extraction_status == "failed"
    assert "No blocks" in references.extraction_reason
    assert report.checks.code.searches[0].outcome == "checked"
    assert report.checks.code.code_availability == "not_found_in_scope"


# --- 计分 ---------------------------------------------------------------------


def _dim(dimension_id: str, applicability: str, score: float | None) -> CredibilityDimension:
    return CredibilityDimension(
        dimension_id=dimension_id,
        applicability=applicability,
        applicability_reason="test",
        check_status="completed",
        score_status="scored" if score is not None else "unable_to_assess",
        score=score,
        weight=1,
        score_rule_ids=["rule"] if score is not None else [],
        summary="test",
        unassessed_reason=None,
        evidence_ids=[],
        finding_ids=[],
    )


_APPROVED = CredibilityRubric(status="approved")


def test_draft_rubric_clears_every_score():
    dims = [
        _dim("claim_evidence", "applicable", 1),
        _dim("reference_verification", "applicable", 2),
        _dim("code_reproduction", "applicable", 3),
    ]
    draft = score_dimensions(dims, rubric=DRAFT_RUBRIC)
    assert [d.score for d in draft.dimensions] == [None, None, None]
    assert draft.overall.score is None and draft.overall.category is None
    assert dims[0].score == 1


def test_approved_rubric_averages_applicable_dimensions_only():
    dims = [
        _dim("claim_evidence", "applicable", 0),
        _dim("reference_verification", "applicable", 10),
        _dim("code_reproduction", "not_applicable", None),
    ]
    result = score_dimensions(dims, rubric=_APPROVED)
    assert result.overall.score == 5
    assert result.overall.calculation.excluded_dimension_ids == ["code_reproduction"]
    assert result.overall.category is None


@pytest.mark.parametrize(
    "blocker", [("code_reproduction", "applicable", None), ("code_reproduction", "undetermined", 9)]
)
def test_any_unscored_applicable_dimension_blanks_the_total(blocker: tuple):
    dims = [_dim("claim_evidence", "applicable", 7), _dim("reference_verification", "applicable", 8), _dim(*blocker)]
    result = score_dimensions(dims, rubric=_APPROVED)
    assert result.overall.score is None and result.overall.category is None
    assert result.overall.calculation.blocking_dimension_ids == ["code_reproduction"]


def test_category_needs_confirmed_thresholds():
    rubric = CredibilityRubric(
        status="approved", category_thresholds=((8, "no_significant_concerns"), (0, "some_concerns"))
    )
    dims = [
        _dim("claim_evidence", "applicable", 9),
        _dim("reference_verification", "applicable", 9),
        _dim("code_reproduction", "applicable", 9),
    ]
    assert score_dimensions(dims, rubric=rubric).overall.category == "no_significant_concerns"


def test_health_keeps_five_dimension_schema():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "0.4"
