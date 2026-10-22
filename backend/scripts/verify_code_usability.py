#!/usr/bin/env python3
"""Run one fresh, source-pinned code-only acceptance check with real DeepSeek.

Only the original parsing lineage is read. No old code/model conclusions are
loaded. The current production code branch is executed, then scored through the
unchanged 0.2 helper. This never runs repository code, claim/reference checks,
or computes an overall paper score. Run each sample in a separate new process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import verify_crossref_live as lineage  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_sample(path, sample_id):
    data = json.loads(Path(path).read_text())
    content = {k: v for k, v in data.items() if k != "selection_sha256"}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()
    if digest != data.get("selection_sha256") or data.get("is_human_gold") is not False:
        raise ValueError("Sample selection integrity or evaluation provenance is invalid")
    rows = [row for row in data["samples"] if row["sample_id"] == sample_id]
    if len(rows) != 1:
        raise ValueError("Expected exactly one selected sample")
    if _sha(rows[0]["pdf_path"]) != rows[0]["pdf_sha256"]:
        raise ValueError("Original PDF differs from the frozen selection")
    return data, rows[0]


def implementation_hashes():
    files = list((REPO / "app/credibility").glob("*.py"))
    files += [REPO / path for path in ("app/config.py", "app/parsing/generic.py",
              "app/llm/deepseek.py", "app/llm/base.py", "app/pipeline/orchestrator.py")]
    return {str(path.relative_to(REPO)): _sha(path) for path in sorted(set(files))}


def _allowed_provider(request, model_endpoint):
    from app.credibility.code_discovery_provider import FETCH_ENDPOINT, SEARCH_ENDPOINT

    url = str(request.url)
    if request.method == "POST":
        return {model_endpoint: "deepseek", SEARCH_ENDPOINT: "deepseek_search",
                FETCH_ENDPOINT: "deepseek_fetch"}.get(url)
    if request.method == "GET" and request.url.scheme == "https" and not (
        request.url.username or request.url.password
    ):
        return "github_api" if request.url.host == "api.github.com" else "public_page"
    return None


def _discovery_summary(result):
    from app.credibility.snapshots import read_snapshot

    sources = {row.source_id for row in result.sources}
    evidence = {row.evidence_id for row in result.evidence}
    queries = {row.query_id for row in result.discovery.queries}
    repositories = {row.repository_id: row for row in result.checks.repositories}
    if any(row.source_id not in sources for row in result.evidence if row.origin == "external"):
        raise ValueError("External evidence references an unknown source")
    for query in result.discovery.queries:
        if not set(query.source_ids) <= sources:
            raise ValueError("Discovery query references an unknown source")
    for candidate in result.discovery.candidates:
        if not (set(candidate.query_ids) <= queries and set(candidate.source_ids) <= sources
                and set(candidate.evidence_ids) <= evidence):
            raise ValueError("Discovery candidate references unknown provenance IDs")
        if candidate.repository_id is not None and candidate.repository_id not in repositories:
            raise ValueError("Discovery candidate references an unknown repository")
        if candidate.status == "verified":
            repository = repositories.get(candidate.repository_id)
            if (repository is None or repository.association != "confirmed"
                    or not repository.association_evidence_ids
                    or not set(repository.association_evidence_ids) <= set(candidate.evidence_ids)):
                raise ValueError("Verified discovery candidate has no confirmed repository proof")
    discovery = result.discovery
    verified = False
    if discovery.audit_ref:
        saved = read_snapshot(discovery.audit_ref)
        if saved.manifest.get("kind") != "code_discovery_audit":
            raise ValueError("Discovery audit has the wrong snapshot kind")
        verified = True
    elif discovery.status in {"completed", "failed"} or any((
        discovery.budget.model_rounds_used, discovery.budget.tool_calls_used, discovery.budget.source_reads_used,
    )):
        raise ValueError("Executed discovery has no independent audit snapshot")
    return {
        "code_discovery": discovery.model_dump(mode="json"),
        "discovery_status": discovery.status,
        "discovery_budget": discovery.budget.model_dump(mode="json"),
        "discovery_candidates": [row.model_dump(mode="json") for row in discovery.candidates],
        "discovery_audit_snapshot_ref": discovery.audit_ref,
        "discovery_audit_snapshot_verified": verified,
        "source_links_verified": True,
    }


def run(sample_path, sample_id, output, mode):
    selection, sample = load_sample(sample_path, sample_id)
    if output.exists():
        raise ValueError("Output directory must be new; earlier runs cannot be replaced")
    output.mkdir(parents=True)
    os.environ["CREDIBILITY_ARTIFACT_DIR"] = str(output / "artifacts")
    os.environ["MAX_UPLOAD_MB"] = "64"
    from app.config import get_settings
    from app.credibility.execution import _result_state, check_code_with_model
    from app.credibility.item_scoring import score_code_result
    from app.credibility.models import CREDIBILITY_SCHEMA_VERSION
    from app.credibility.rubric import load_credibility_rubric
    from app.credibility.runtime import encode_context, request_versions
    from app.credibility.snapshots import capture_snapshot, verify_report_snapshots

    get_settings.cache_clear()
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise ValueError("The existing environment has no DeepSeek credential")
    settings.credibility_code_model_enabled = True
    settings.credibility_code_tool_mode = mode
    rubric = load_credibility_rubric()
    if rubric.content_sha256 != selection.get("rubric_sha256"):
        raise ValueError("The scoring policy differs from the frozen acceptance selection")
    import httpx

    audit = {"http_calls": [], "blocked_count": 0}
    original_send = httpx.Client.send
    endpoint = settings.deepseek_base_url.rstrip("/") + "/chat/completions"

    def send(client, request, *args, **kwargs):
        url = str(request.url)
        provider = _allowed_provider(request, endpoint)
        if provider is None:
            audit["blocked_count"] += 1
            raise RuntimeError("Unexpected HTTP operation in a code-only verification")
        event = {"provider": provider, "method": request.method, "url": url,
                 "started_at": lineage._now()}
        audit["http_calls"].append(event)
        started = time.monotonic()
        try:
            response = original_send(client, request, *args, **kwargs)
            event["http_status"] = response.status_code
            if provider == "github_api":
                event["rate_limit_headers"] = {key: response.headers[key] for key in (
                    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "retry-after"
                ) if key in response.headers}
            return response
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["elapsed_seconds"] = round(time.monotonic() - started, 4)

    summary = {
        "schema_version": CREDIBILITY_SCHEMA_VERSION, "run_id": "code_live_" + uuid.uuid4().hex,
        "sample_id": sample_id, "title": sample["title"], "started_at": lineage._now(),
        "status": "failed", "scope": "code_association_implementation_public_reproduction_evidence",
        "reference_checks_run": False, "claim_checks_run": False, "repository_code_executed": False,
        "previous_code_results_read": False, "previous_model_outputs_read": False,
        "selection_sha256": selection["selection_sha256"], "rubric_version": rubric.rubric_version,
        "rubric_sha256": rubric.content_sha256, "mode": mode, "model": settings.deepseek_model,
        "thinking_enabled": settings.deepseek_thinking_enabled,
        "versions": request_versions(), "implementation_sha256": implementation_hashes(),
        "artifact_dir": str(output / "artifacts"), "script_sha256": _sha(__file__),
    }
    started = time.monotonic()
    result = None
    try:
        ctx, provenance = lineage._load_parsing_context(Path(selection["input_db"]),
            Path(selection["input_artifacts"]), sample["assessment_id"])
        summary["input_lineage"] = provenance
        summary["parsing"] = lineage._fresh_parse_context(ctx, Path(sample["pdf_path"]))
        ctx.assessment_id, ctx.model_id, ctx.generated_at = summary["run_id"], "model_standard_v1", lineage._now()
        ctx.previous_check, ctx.model_history = None, []
        ctx.checkpoint_load = ctx.checkpoint_save = None
        encoded = encode_context(ctx)
        lineage._write_json(output / "input-context.json", encoded)
        summary["input_snapshot_ref"] = capture_snapshot(kind="code_acceptance_input",
            normalized_json={"context": encoded}, metadata={"run_id": summary["run_id"]})
        with patch.object(httpx.Client, "send", send):
            result = check_code_with_model(ctx)
        result = score_code_result(result, rubric=rubric)
        payload = result.model_dump(mode="json")
        lineage._write_json(output / "code-result.json", payload)
        summary["result_snapshot_ref"] = capture_snapshot(kind="code_acceptance_result",
            normalized_json=payload, metadata={"run_id": summary["run_id"]})
        summary["snapshots"] = verify_report_snapshots({
            "sources": payload["sources"], "code_discovery": payload["discovery"],
        })
        summary.update(_discovery_summary(result))
        summary["execution_status"] = _result_state(result)
        summary["check_status"] = result.dimension.check_status
        summary["scope_complete"] = result.checks.scope_complete
        summary["code_availability"] = result.checks.code_availability
        summary["reproduction_signal"] = result.checks.reproduction_signal
        summary["code_score"] = result.dimension.score
        summary["score_rule_ids"] = result.dimension.score_rule_ids
        summary["repository_count"] = len(result.checks.repositories)
        summary["repository_associations"] = dict(Counter(r.association for r in result.checks.repositories))
        summary["discussion_count"] = len(result.checks.discussions)
        summary["discussion_signals"] = dict(Counter(d.signal for d in result.checks.discussions))
        summary["discussion_relevance"] = dict(Counter(d.relevance for d in result.checks.discussions))
        summary["scoring_eligible_discussions"] = sum(d.scoring_eligible for d in result.checks.discussions)
        summary["source_statuses"] = dict(Counter(s.fetch_status for s in result.sources))
        summary["source_count"] = len(result.sources)
        summary["external_review"] = result.external_review
        summary["model_call_count"] = len(result.model_calls)
        summary["model_calls"] = [c.model_dump(mode="json") for c in result.model_calls]
        summary["external_request_count"] = result.external_request_count
        summary["status"] = "passed" if summary["execution_status"] == "completed" and (
            result.external_review is not None
            and result.external_review.get("status") in {"completed", "not_needed"}
        ) else "incomplete"
        if audit["blocked_count"]:
            raise ValueError("Network guard blocked an unexpected operation")
    except Exception as exc:
        summary["status"], summary["error_type"] = "failed", type(exc).__name__
    finally:
        summary["elapsed_seconds"] = round(time.monotonic() - started, 4)
        summary["completed_at"] = lineage._now()
        summary["http_counts"] = dict(Counter(e["provider"] for e in audit["http_calls"]))
        summary["http_status_counts"] = dict(Counter(str(e.get("http_status", "transport_error"))
                                                    for e in audit["http_calls"]))
        summary["blocked_http_count"] = audit["blocked_count"]
        summary["audit_snapshot_ref"] = capture_snapshot(kind="code_acceptance_http_audit",
            normalized_json=audit, metadata={"run_id": summary["run_id"]})
        lineage._write_json(output / "http-audit.json", audit)
        lineage._write_json(output / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in {
        "implementation_sha256", "model_calls", "versions"
    }}, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("native_tools", "prefetch"), default="native_tools")
    parser.add_argument("--execute", action="store_true", help="Make authorized live DeepSeek/public-source requests")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("Pass --execute for this real model acceptance run")
    return run(args.samples.resolve(), args.sample_id, args.output_dir.resolve(), args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
