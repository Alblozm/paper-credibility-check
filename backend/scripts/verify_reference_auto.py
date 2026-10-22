#!/usr/bin/env python3
"""Verify automatic multi-source reference checks from the original pinned PDF.

This is a reference-only acceptance run. It reparses the byte-identical PDF,
executes the production Crossref/arXiv/official-publication flow, and applies the
unchanged 0.2 provisional reference scoring rules. It never runs DeepSeek, claim
checks or code checks, and never reads earlier reference results or manual audits.
Use a new output directory and state path for a live cold run. A separate process
with --offline-cache reuses that provider cache and must make zero HTTP attempts.
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

from scripts import verify_crossref_live as legacy  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LEGACY_ALLOWED_PROVIDER = legacy._allowed_provider


def _allowed_provider(request) -> str | None:
    """The production transport still applies DNS and redirect checks itself."""
    existing = _LEGACY_ALLOWED_PROVIDER(request)
    if existing is not None:
        return existing
    if request.method != "GET" or request.url.fragment:
        return None
    from app.credibility.reference_publisher_http import canonical_url

    try:
        canonical_url(str(request.url))
    except (TypeError, ValueError):
        return None
    return "publisher"


def _implementation_hashes() -> dict[str, str]:
    paths = set((_REPO_ROOT / "app/credibility").glob("reference_*.py"))
    paths.update(_REPO_ROOT / relative for relative in (
        "app/credibility/references.py", "app/credibility/crossref_http.py",
        "app/credibility/item_scoring.py", "app/credibility/scoring.py", "app/credibility/rubric.py",
        "app/credibility/models.py", "app/credibility/coverage.py", "app/credibility/runtime.py",
        "app/credibility/shell.py", "app/credibility/execution.py", "app/credibility/snapshots.py",
        "app/storage/crossref_state.py", "app/parsing/generic.py", "app/config.py",
    ))
    return {str(path.relative_to(_REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-db", type=Path, required=True)
    parser.add_argument("--input-artifacts", type=Path, required=True)
    parser.add_argument("--assessment-id", required=True, help="Task whose parsing lineage is read only")
    parser.add_argument("--input-pdf", type=Path, required=True, help="Original PDF, checked against saved SHA-256")
    parser.add_argument("--state-path", type=Path, required=True, help="New live state, or existing offline cache")
    parser.add_argument("--output-dir", type=Path, required=True, help="A new run directory")
    parser.add_argument("--artifact-dir", type=Path, help="New evidence root; defaults to OUTPUT/artifacts")
    parser.add_argument("--offline-cache", action="store_true", help="Block every HTTP getter/send attempt")
    parser.add_argument("--expect-entries", type=int, help="Assert extracted reference count, e.g. 62 or 30")
    parser.add_argument("--min-interval-seconds", type=legacy._finite_number)
    parser.add_argument("--timeout-seconds", type=legacy._finite_number)
    parser.add_argument("--retry-wait-budget-seconds", type=legacy._finite_number)
    args = parser.parse_args(argv)
    for name in ("input_db", "input_artifacts", "input_pdf", "state_path", "output_dir"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    args.artifact_dir = (args.artifact_dir or args.output_dir / "artifacts").expanduser().resolve()
    if not args.input_db.is_file() or not args.input_artifacts.is_dir() or not args.input_pdf.is_file():
        parser.error("The input database, artifact directory and pinned PDF must already exist")
    if args.state_path == args.input_db or args.state_path.is_relative_to(args.input_artifacts):
        parser.error("Provider state must be separate from the source database and evidence")
    if args.artifact_dir.is_relative_to(args.input_artifacts) or args.output_dir.is_relative_to(args.input_artifacts):
        parser.error("Output and new evidence must be separate from the original evidence root")
    if args.output_dir.exists() or args.artifact_dir.exists():
        parser.error("Output and artifact directories must be new; existing runs cannot be replaced")
    if args.expect_entries is not None and args.expect_entries < 1:
        parser.error("--expect-entries must be positive")
    if args.timeout_seconds == 0:
        parser.error("--timeout-seconds must be positive")
    state_files = [args.state_path, Path(str(args.state_path) + ".arxiv-versions.sqlite3"),
                   Path(str(args.state_path) + ".publisher.sqlite3")]
    if not args.offline_cache and any(path.exists() for path in state_files):
        parser.error("A live cold run requires a new --state-path with no existing provider caches")
    if args.offline_cache and not args.state_path.is_file():
        parser.error("--offline-cache requires an existing provider state file")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    return args


def main(argv=None) -> int:
    args = _arguments(argv)
    os.environ["CREDIBILITY_ARTIFACT_DIR"] = str(args.artifact_dir)
    os.environ["CROSSREF_STATE_PATH"] = str(args.state_path)

    from app.config import get_settings
    from app.credibility.coverage import build_reference_summary
    from app.credibility.item_scoring import score_reference_result
    from app.credibility.models import CREDIBILITY_SCHEMA_VERSION
    from app.credibility.rubric import PROVISIONAL_VERSION, load_credibility_rubric
    from app.credibility.runtime import encode_context, request_versions
    from app.credibility.snapshots import capture_snapshot, verify_report_snapshots

    get_settings.cache_clear()
    # This verifier tests the automatic production flow even when local env
    # defaults disable it for ordinary development. No real model is selected.
    get_settings().reference_multisource_enabled = True
    rubric = load_credibility_rubric(PROVISIONAL_VERSION)
    started = time.monotonic()
    audit = {
        "http_get_attempt_count": 0, "http_call_count": 0, "blocked_http_count": 0,
        "lookup_call_count": 0, "cache_hit_count": 0, "http_calls": [], "lookups": [],
        "crossref_http_call_count": 0, "arxiv_http_call_count": 0, "publisher_http_call_count": 0,
        "provider_http_call_counts": {"crossref": 0, "arxiv": 0, "publisher": 0},
    }
    summary = {
        "schema_version": CREDIBILITY_SCHEMA_VERSION, "artifact_schema_version": "reference-auto-verification-1",
        "run_id": f"reference_auto_{uuid.uuid4().hex}", "started_at": legacy._now(), "status": "failed",
        "reference_only": True, "scope": "automatic_reference_verification_only",
        "model_calls": 0, "claim_checks_run": False, "code_checks_run": False,
        "offline_cache": args.offline_cache, "state_path": str(args.state_path),
        "artifact_dir": str(args.artifact_dir), "reused_reference_results": False,
        "no_previous_reference_result": True, "manual_supplement_results_read": False,
        "rubric_version": rubric.rubric_version, "rubric_sha256": rubric.content_sha256,
        "execution_versions": request_versions(), "implementation_sha256": _implementation_hashes(),
    }
    try:
        context, provenance = legacy._load_parsing_context(args.input_db, args.input_artifacts, args.assessment_id)
        summary["input"] = provenance
        summary["parsing"] = legacy._fresh_parse_context(context, args.input_pdf)
        context.assessment_id, context.model_id, context.generated_at = summary["run_id"], "not_called", legacy._now()
        context.previous_check, context.model_history = None, []
        context.checkpoint_load = context.checkpoint_save = None
        encoded = encode_context(context)
        legacy._write_json(args.output_dir / "input-context.json", encoded)
        summary["input_snapshot_ref"] = capture_snapshot(
            kind="reference_auto_verification_input", normalized_json={"context": encoded},
            metadata={"run_id": summary["run_id"], "origin_parsing_artifact_ref": provenance["artifact_ref"]},
        )
        with patch.object(legacy, "_allowed_provider", _allowed_provider):
            result = legacy._execute_references(context, args, audit)
        result = score_reference_result(result, rubric=rubric)
        verification = build_reference_summary(result.checks)
        payload = result.model_dump(mode="json")
        verification_payload = verification.model_dump(mode="json")
        legacy._write_json(args.output_dir / "reference-result.json", payload)
        legacy._write_json(args.output_dir / "reference-summary.json", verification_payload)
        legacy._write_json(args.output_dir / "sources.json", payload["sources"])
        summary["result_snapshot_ref"] = capture_snapshot(
            kind="reference_auto_verification_result", normalized_json=payload, metadata={"run_id": summary["run_id"]},
        )
        summary["reference_summary_snapshot_ref"] = capture_snapshot(
            kind="reference_auto_verification_summary", normalized_json=verification_payload,
            metadata={"run_id": summary["run_id"]},
        )
        summary["snapshots"] = verify_report_snapshots(payload)
        summary["reference_counts"] = {name: getattr(result.checks, name) for name in (
            "original_total", "extracted_count", "checked_count", "matched_count", "conflict_count",
            "unverified_count", "not_checked_count", "query_completion_ratio", "extraction_coverage_ratio",
        )}
        summary["verification_rate"] = verification.verification_rate
        summary["verification_denominator"] = verification.denominator
        summary["extraction_complete"] = verification.extraction_complete
        summary["unresolved_reason_counts"] = dict(Counter(item.reason_code for item in verification.unresolved_items))
        summary["source_statuses"] = dict(Counter(source.fetch_status for source in result.sources))
        summary["reference_check_status"] = result.dimension.check_status
        summary["reference_score"] = result.dimension.score
        summary["reference_score_status"] = result.dimension.score_status
        summary["reference_score_rule_ids"] = list(result.dimension.score_rule_ids)
        if args.expect_entries is not None and result.checks.extracted_count != args.expect_entries:
            raise legacy.VerificationError("Extracted reference count differs from --expect-entries")
        if context.model_history:
            raise legacy.VerificationError("Reference-only execution unexpectedly recorded model calls")
        if audit["blocked_http_count"] or (args.offline_cache and (
            audit["http_get_attempt_count"] or audit["http_call_count"]
        )):
            raise legacy.VerificationError("Network guard detected a prohibited HTTP attempt")
        summary["status"] = "passed" if (
            verification.extraction_complete and result.checks.not_checked_count == 0
            and result.dimension.check_status == "completed"
        ) else "incomplete"
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        if isinstance(exc, legacy.VerificationError):
            summary["error"] = str(exc)
    finally:
        summary.update({key: value for key, value in audit.items() if key not in {"http_calls", "lookups"}})
        summary["completed_at"] = legacy._now()
        summary["elapsed_seconds"] = round(time.monotonic() - started, 4)
        summary["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        summary["http_audit_snapshot_ref"] = capture_snapshot(
            kind="reference_auto_http_audit", normalized_json=audit, metadata={"run_id": summary["run_id"]},
        )
        legacy._write_json(args.output_dir / "http-audit.json", audit)
        summary["run_summary_snapshot_ref"] = capture_snapshot(
            kind="reference_auto_run_summary", normalized_json=dict(summary), metadata={"run_id": summary["run_id"]},
        )
        legacy._write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 2 if summary["status"] == "incomplete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
