"""Reference-only acceptance CLI guards, persistence and unchanged scoring."""
from __future__ import annotations

import copy
import json
from argparse import Namespace
from collections import Counter
from unittest.mock import patch

import httpx
import pytest

from app.config import get_settings
from app.credibility.context import CheckContext
from app.credibility.models import CREDIBILITY_SCHEMA_VERSION, CredibilityExternalSource, CredibilityReferenceChecks
from app.credibility.references import _empty_failed
from app.parsing.grobid import StubParser
from scripts import verify_crossref_live as legacy
from scripts import verify_reference_auto as verifier


def _result(*, status="completed"):
    result = _empty_failed("Fixture", [])
    result.dimension.applicability = "applicable"
    result.dimension.check_status = status
    result.checks = CredibilityReferenceChecks.model_validate({
        "extraction_status": "complete", "extraction_reason": "Two references.", "original_total": 2,
        "extracted_count": 2, "checked_count": 2, "matched_count": 1, "conflict_count": 0,
        "unverified_count": 1, "not_checked_count": 0, "query_completion_ratio": 1,
        "extraction_coverage_ratio": 1, "gaps": [], "items": [{
            "reference_id": f"ref_{index:03d}", "raw_text": f"Reference {index}", "paper_evidence_ids": [],
            "parsed": {"title": None, "authors": [], "year": None, "doi": None, "arxiv_id": None},
            "outcome": outcome, "reason": reason, "source_ids": [], "candidates": [],
            "selected_candidate_id": None, "score_rule_id": None, "item_score": None,
        } for index, outcome, reason in (
            (1, "matched", "Official identity confirmed."),
            (2, "unverified", "[target_not_found_in_scope] No bounded official target."),
        )],
    })
    return result


def _inputs(tmp_path, *, offline=False):
    database, artifacts, pdf = tmp_path / "input.db", tmp_path / "input-artifacts", tmp_path / "pinned.pdf"
    database.touch()
    artifacts.mkdir()
    pdf.write_bytes(b"%PDF-1.4 pinned fixture")
    state, output = tmp_path / "state.sqlite3", tmp_path / "output"
    if offline:
        state.touch()
    args = ["--input-db", str(database), "--input-artifacts", str(artifacts), "--assessment-id", "origin_task",
            "--input-pdf", str(pdf), "--state-path", str(state), "--output-dir", str(output), "--expect-entries", "2"]
    if offline:
        args.append("--offline-cache")
    return args, output, state


@pytest.mark.parametrize("offline", [False, True])
@pytest.mark.parametrize("recovered_failure", [False, True])
def test_main_saves_scored_result_summary_and_audit_without_old_results(
    tmp_path, monkeypatch, offline, recovered_failure,
):
    argv, output, _ = _inputs(tmp_path, offline=offline)
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": "2503.12345v1", "upload_id": None}, repo_root=tmp_path,
        generated_at="2026-10-19T00:00:00Z", ref=None, unresolved_reason=None,
        doc=StubParser._fake(paper_id="cli_fixture", title="Offline CLI fixture"),
    )
    calls = Counter()
    monkeypatch.setattr(get_settings, "cache_clear", lambda: None)

    def load(database, artifacts, task):
        calls["parsing_lineage"] += 1
        assert database.name == "input.db" and artifacts.name == "input-artifacts" and task == "origin_task"
        return copy.deepcopy(ctx), {"artifact_ref": "snapshot:v1:" + "a" * 64}

    def parse(context, pdf):
        calls["fresh_parse"] += 1
        assert pdf.name == "pinned.pdf" and context.doc is not None
        return {"mode": "fresh_pdf_parse", "pdf_sha256": "f" * 64, "reused_parsed_blocks": False}

    def execute(context, args, audit):
        calls["reference_check"] += 1
        assert context.previous_check is None and context.model_history == [] and context.model_id == "not_called"
        assert context.checkpoint_load is context.checkpoint_save is None
        assert args.offline_cache == offline
        assert get_settings().reference_multisource_enabled is True
        result = _result()
        if recovered_failure:
            from app.credibility.snapshots import capture_snapshot

            url, timestamp = "https://api.crossref.org/works?query=fixture", "2026-10-19T00:00:00Z"
            ref = capture_snapshot(kind="failed_attempt", requested_url=url, retrieved_at=timestamp,
                                   fetch_status="timeout", http_status=None, is_cached=False)
            result.sources.append(CredibilityExternalSource(
                source_id="failed_attempt", kind="metadata", url=url, fetch_status="timeout",
                http_status=None, retrieved_at=timestamp, observed_at=timestamp, is_cached=False,
                snapshot_ref=ref, association_reason="An earlier attempt failed before another source confirmed it.",
            ))
            result.checks.items[0].source_ids.append("failed_attempt")
        return result

    monkeypatch.setattr(legacy, "_load_parsing_context", load)
    monkeypatch.setattr(legacy, "_fresh_parse_context", parse)
    monkeypatch.setattr(legacy, "_execute_references", execute)
    assert verifier.main(argv) == 0
    assert calls == {"parsing_lineage": 1, "fresh_parse": 1, "reference_check": 1}
    summary = json.loads((output / "summary.json").read_text())
    references = json.loads((output / "reference-result.json").read_text())
    rate = json.loads((output / "reference-summary.json").read_text())
    audit = json.loads((output / "http-audit.json").read_text())
    assert summary["schema_version"] == CREDIBILITY_SCHEMA_VERSION
    assert summary["status"] == "passed" and summary["reference_only"] is True
    assert summary["reference_score"] == references["dimension"]["score"] == 7
    assert [item["item_score"] for item in references["checks"]["items"]] == [10, 4]
    assert rate["verification_rate"] == summary["verification_rate"] == 0.5
    assert summary["unresolved_reason_counts"] == {"target_not_found_in_scope": 1}
    assert len(summary["rubric_sha256"]) == 64
    assert "app/credibility/reference_publisher_http.py" in summary["implementation_sha256"]
    assert summary["model_calls"] == 0 and summary["reused_reference_results"] is False
    assert summary["manual_supplement_results_read"] is False
    assert summary["source_statuses"] == ({"timeout": 1} if recovered_failure else {})
    assert audit["provider_http_call_counts"] == {"crossref": 0, "arxiv": 0, "publisher": 0}
    assert {"input-context.json", "reference-result.json", "reference-summary.json", "sources.json",
            "summary.json", "http-audit.json"} <= {path.name for path in output.iterdir()}


@pytest.mark.parametrize("url,method,expected", [
    ("https://api.crossref.org/works?query.title=Title", "GET", "crossref"),
    ("https://export.arxiv.org/api/query?id_list=2106.09685", "GET", "arxiv"),
    ("https://arxiv.org/abs/2106.09685v2", "GET", "publisher"),
    ("https://aclanthology.org/2022.acl-long.1/", "GET", "publisher"),
    ("https://proceedings.mlr.press/v139/example.html", "GET", "publisher"),
    ("https://aclanthology.org/2022.acl-long.1/", "POST", None),
    ("https://api.deepseek.com/chat/completions", "POST", None),
    ("https://example.com/paper", "GET", None),
    ("http://aclanthology.org/paper", "GET", None),
    ("https://arxiv.org/search/?query=title", "GET", None),
    ("https://user:password@aclanthology.org/paper", "GET", None),
    ("https://aclanthology.org:444/paper", "GET", None),
])
def test_live_guard_only_accepts_get_requests_to_declared_official_sources(url, method, expected):
    assert verifier._allowed_provider(httpx.Request(method, url)) == expected


def test_offline_guard_blocks_official_publisher_get_before_transport(tmp_path, monkeypatch):
    import app.credibility.references as references

    args = Namespace(state_path=tmp_path / "state.sqlite3", min_interval_seconds=0,
                     timeout_seconds=1, retry_wait_budget_seconds=0, offline_cache=True)
    audit = {"http_get_attempt_count": 0, "http_call_count": 0, "blocked_http_count": 0,
             "lookup_call_count": 0, "cache_hit_count": 0, "http_calls": [], "lookups": [],
             "publisher_http_call_count": 0, "provider_http_call_counts": {"crossref": 0, "arxiv": 0, "publisher": 0}}
    transported = []

    def check(_context):
        with httpx.Client(transport=httpx.MockTransport(lambda request: transported.append(request))) as client:
            client.get("https://aclanthology.org/2022.acl-long.1/")

    monkeypatch.setattr(references, "check_references", check)
    with patch.object(legacy, "_allowed_provider", verifier._allowed_provider):
        with pytest.raises(legacy.NetworkForbidden):
            legacy._execute_references(None, args, audit)
    assert audit["blocked_http_count"] == 1 and audit["http_call_count"] == 0
    assert transported == []


def test_live_existing_state_and_existing_output_are_rejected(tmp_path):
    argv, output, state = _inputs(tmp_path)
    state.touch()
    with pytest.raises(SystemExit, match="2"):
        verifier._arguments(argv)
    state.unlink()
    output.mkdir()
    with pytest.raises(SystemExit, match="2"):
        verifier._arguments(argv)


def test_incomplete_reference_execution_is_saved_without_claiming_acceptance(tmp_path, monkeypatch):
    argv, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(get_settings, "cache_clear", lambda: None)
    ctx = CheckContext(source={}, repo_root=tmp_path, generated_at="2026-10-19T00:00:00Z", ref=None,
                       unresolved_reason=None, doc=StubParser._fake(paper_id="partial", title="Partial"))
    monkeypatch.setattr(legacy, "_load_parsing_context", lambda *args: (ctx, {"artifact_ref": "fixture"}))
    monkeypatch.setattr(legacy, "_fresh_parse_context", lambda *args: {"mode": "fresh_pdf_parse"})
    monkeypatch.setattr(legacy, "_execute_references", lambda *args: _result(status="partial"))
    assert verifier.main(argv) == 2
    assert json.loads((output / "summary.json").read_text())["status"] == "incomplete"
