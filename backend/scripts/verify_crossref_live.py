#!/usr/bin/env python3
"""Run reference verification from a validated parsing lineage, without a model.

The source task database is opened read-only. Neither its report nor any old
reference result is read. A fresh process with --offline-cache must complete
without calling httpx.get, so it proves query-cache reuse rather than stage reuse.
All output goes to a new --output-dir; existing run directories are never replaced.
With --input-pdf the saved checkpoint only proves source identity; the current
generic parser extracts the same PDF again before any reference queries run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sqlite3
import sys
import time
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class VerificationError(RuntimeError):
    """A local verification precondition or assertion failed."""


class NetworkForbidden(VerificationError):
    """Offline runs and requests outside the two metadata providers are blocked."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _write_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def _finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("Must be a finite, non-negative number")
    return number


def _load_parsing_context(database: Path, artifacts: Path, assessment_id: str):
    from app.credibility.runtime import decode_context, digest
    from app.credibility.snapshots import SnapshotStore

    # Deliberately select no task payload and no reference checkpoint. The join
    # verifies the parsing checkpoint still belongs to the task's input lineage.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT c.fingerprint, c.task_fingerprint, c.status, c.payload, t.fingerprint "
            "FROM credibility_stage_checkpoints c JOIN credibility_tasks t "
            "ON t.assessment_id = c.assessment_id "
            "WHERE c.assessment_id = ? AND c.stage = 'parsing'",
            (assessment_id,),
        ).fetchall()
    if len(rows) != 1:
        raise VerificationError("Expected exactly one parsing checkpoint for the selected task")
    fingerprint, task_fingerprint, status, payload, current_fingerprint = rows[0]
    if status != "completed" or not fingerprint == task_fingerprint == current_fingerprint:
        raise VerificationError("Parsing checkpoint is incomplete or has a different task lineage")
    reference = json.loads(payload)["artifact_ref"]
    snapshot = SnapshotStore(artifacts).read(reference)
    meta = snapshot.manifest["metadata"]
    if snapshot.manifest["kind"] != "stage_artifact" or (
        meta.get("assessment_id"), meta.get("stage"), meta.get("fingerprint"), meta.get("status")
    ) != (assessment_id, "parsing", fingerprint, "completed"):
        raise VerificationError("Parsing artifact identity, fingerprint or completion state differs")
    value = snapshot.normalized_json
    if not isinstance(value, dict) or set(value) != {"context"}:
        raise VerificationError("Parsing artifact must contain only the saved input context")
    context = decode_context(value["context"])
    if context.doc is None:
        raise VerificationError("Saved parsing input has no document")
    return context, {
        "database": str(database), "artifacts": str(artifacts), "assessment_id": assessment_id,
        "stage": "parsing", "fingerprint": fingerprint, "artifact_ref": reference,
        "context_sha256": digest(value["context"]),
    }


def _fresh_parse_context(context, pdf_path: Path) -> dict:
    """Reparse only the byte-identical PDF belonging to the validated lineage."""
    from app.config import get_settings
    from app.credibility.context import paper_from_context
    from app.parsing.arxiv import ArxivMetadata
    from app.parsing.generic import GenericPdfParser

    if context.doc is None:
        raise VerificationError("Saved parsing input has no document")
    settings = get_settings()
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise VerificationError("The input PDF must already exist")
    if pdf_path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise VerificationError("The input PDF exceeds the configured file size limit")
    source_hash = hashlib.sha256()
    with pdf_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            source_hash.update(chunk)
    actual_sha = source_hash.hexdigest()
    expected = {}
    if context.doc.source_sha256:
        expected["context.doc.source_sha256"] = context.doc.source_sha256.lower()
    if context.ref is not None and context.ref.metadata.get("pdf_sha256"):
        expected["context.ref.metadata.pdf_sha256"] = context.ref.metadata["pdf_sha256"].lower()
    if not expected or any(value != actual_sha for value in expected.values()):
        raise VerificationError("The input PDF SHA-256 differs from the saved parsing lineage")
    old_doc, old_paper = context.doc, context.doc.paper
    record = context.ref.source_record if context.ref is not None else {}
    identity = paper_from_context(context)
    arxiv_id = (identity.arxiv_id if identity is not None else None) or context.source.get("arxiv_id")
    metadata = ArxivMetadata(
        arxiv_id=arxiv_id or "",
        title=old_paper.title or record.get("title") or "",
        authors=list(old_paper.authors or record.get("authors") or []),
        abstract=old_paper.abstract or record.get("abstract") or "",
        categories=list(old_doc.source_categories or record.get("subjects") or record.get("categories") or []),
    )
    parser = GenericPdfParser(
        arxiv_cache_dir=settings.arxiv_cache_dir,
        max_download_bytes=settings.max_upload_mb * 1024 * 1024,
        max_pages=settings.max_page_count,
    )
    # These libraries can emit one font warning per glyph. Preserve every logger
    # level after this local parse; parse exceptions and error logs still surface.
    names = {"pypdf", "fontTools"} | {
        name for name in logging.Logger.manager.loggerDict
        if name.startswith(("pypdf.", "fontTools."))
    }
    levels = {name: logging.getLogger(name).level for name in names}
    try:
        for name in names:
            logging.getLogger(name).setLevel(logging.ERROR)
        fresh = parser.parse_pdf(str(pdf_path), arxiv_id=arxiv_id, arxiv_metadata=metadata)
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
    if fresh.source_sha256 != actual_sha:
        raise VerificationError("The PDF changed while it was being parsed")
    old_identity = context.paper_version_id
    context.doc, context.ref, context.unresolved_reason = fresh, None, None
    return {
        "mode": "fresh_pdf_parse", "pdf_path": str(pdf_path), "pdf_sha256": actual_sha,
        "validated_hash_fields": sorted(expected), "parser_name": fresh.parser_name,
        "parser_implementation_sha256": hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "app/parsing/generic.py").read_bytes()
        ).hexdigest(),
        "origin_parser_name": old_doc.parser_name,
        "origin_paper_version_id": old_identity, "paper_version_id": context.paper_version_id,
        "paper_metadata_origin": "validated_saved_parsing_context", "reused_parsed_blocks": False,
        "page_count": fresh.paper.page_count,
    }


def _allowed_provider(request) -> str | None:
    url = request.url
    if (request.method != "GET" or url.scheme != "https" or url.username or url.password
            or url.port not in {None, 443} or url.fragment):
        return None
    if url.host == "api.crossref.org" and (url.path == "/works" or url.path.startswith("/works/")):
        return "crossref"
    if url.host == "export.arxiv.org" and url.path == "/api/query":
        return "arxiv"
    return None


def _execute_references(context, args, audit: dict):
    import httpx

    from app.credibility import references

    lookup = references.CrossrefLookup(
        state_path=args.state_path,
        min_interval_seconds=args.min_interval_seconds,
        timeout_seconds=args.timeout_seconds,
        retry_wait_budget_seconds=args.retry_wait_budget_seconds,
    )
    original_send, original_lookup = httpx.Client.send, lookup.lookup
    audit.setdefault("provider_http_call_counts", {"crossref": 0, "arxiv": 0})
    audit.setdefault("crossref_http_call_count", 0)
    audit.setdefault("arxiv_http_call_count", 0)

    def guarded_get(*positional, **keywords):
        audit["http_get_attempt_count"] += 1
        audit["blocked_http_count"] += 1
        raise NetworkForbidden("Offline cache miss: httpx.get was called")

    def guarded_send(client, request, *positional, **keywords):
        provider = _allowed_provider(request)
        if args.offline_cache or provider is None:
            audit["blocked_http_count"] += 1
            raise NetworkForbidden("Only live Crossref works or arXiv metadata GET requests are allowed")
        audit["http_call_count"] += 1
        audit["provider_http_call_counts"][provider] += 1
        audit[f"{provider}_http_call_count"] += 1
        event = {"provider": provider, "method": request.method, "url": str(request.url), "started_at": _now()}
        audit["http_calls"].append(event)
        started = time.monotonic()
        try:
            response = original_send(client, request, *positional, **keywords)
            event["http_status"] = response.status_code
            return response
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["elapsed_seconds"] = round(time.monotonic() - started, 4)

    async def forbidden_async(*_positional, **_keywords):
        audit["blocked_http_count"] += 1
        raise NetworkForbidden("This reference-only verifier never makes asynchronous or model requests")

    def tracked_lookup(metadata, *, prior_ref=None):
        audit["lookup_call_count"] += 1
        event = {"metadata": metadata.model_dump(mode="json")}
        audit["lookups"].append(event)
        try:
            result = original_lookup(metadata, prior_ref=prior_ref)
            audit["cache_hit_count"] += int(result.is_cached)
            event.update(
                url=result.url, fetch_status=result.fetch_status, http_status=result.http_status,
                is_cached=result.is_cached, snapshot_ref=result.snapshot_ref,
            )
            return result
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise

    with ExitStack() as stack:
        # Replacing get in live mode would select the client's injected-getter
        # path instead of its bounded production stream transport.
        if args.offline_cache:
            stack.enter_context(patch.object(httpx, "get", guarded_get))
        stack.enter_context(patch.object(httpx.Client, "send", guarded_send))
        stack.enter_context(patch.object(httpx.AsyncClient, "send", forbidden_async))
        stack.enter_context(patch.object(lookup, "lookup", tracked_lookup))
        stack.enter_context(patch.object(references, "_LOOKUP", lookup))
        return references.check_references(context)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-db", type=Path, required=True)
    parser.add_argument("--input-artifacts", type=Path, required=True)
    parser.add_argument("--assessment-id", required=True, help="Task whose parsing input will be read")
    parser.add_argument("--input-pdf", type=Path,
                        help="Freshly parse this PDF, whose SHA-256 must match the saved input lineage")
    parser.add_argument("--state-path", type=Path, required=True, help="Separate persistent Crossref query cache")
    parser.add_argument("--output-dir", type=Path, required=True, help="A new directory; must not already exist")
    parser.add_argument("--artifact-dir", type=Path, help="New evidence root; defaults to OUTPUT/artifacts")
    parser.add_argument("--offline-cache", action="store_true", help="Fail if any HTTP getter or send is attempted")
    parser.add_argument("--expect-entries", type=int, help="Assert the number of extracted reference items, e.g. 60")
    parser.add_argument("--min-interval-seconds", type=_finite_number)
    parser.add_argument("--timeout-seconds", type=_finite_number)
    parser.add_argument("--retry-wait-budget-seconds", type=_finite_number)
    args = parser.parse_args()
    for name in ("input_db", "input_artifacts", "state_path", "output_dir"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    args.artifact_dir = (args.artifact_dir or args.output_dir / "artifacts").expanduser().resolve()
    if args.input_pdf is not None:
        args.input_pdf = args.input_pdf.expanduser().resolve()
        if not args.input_pdf.is_file():
            parser.error("--input-pdf must be an existing PDF file")
    if not args.input_db.is_file() or not args.input_artifacts.is_dir():
        parser.error("The input database and artifact directory must already exist")
    if args.state_path == args.input_db or args.state_path.is_relative_to(args.input_artifacts):
        parser.error("Crossref state must be separate from the original task database and evidence")
    if args.artifact_dir.is_relative_to(args.input_artifacts) or args.output_dir.is_relative_to(args.input_artifacts):
        parser.error("Output and evidence must be separate from the original input evidence root")
    if args.expect_entries is not None and args.expect_entries < 1:
        parser.error("--expect-entries must be positive")
    if args.timeout_seconds == 0:
        parser.error("--timeout-seconds must be positive")
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("Output directory already exists; select a fresh run directory")
    os.environ["CREDIBILITY_ARTIFACT_DIR"] = str(args.artifact_dir)
    os.environ["CROSSREF_STATE_PATH"] = str(args.state_path)

    from app.config import get_settings
    from app.credibility.runtime import encode_context, request_versions
    from app.credibility.snapshots import capture_snapshot, verify_report_snapshots

    get_settings.cache_clear()
    started = time.monotonic()
    audit = {"http_get_attempt_count": 0, "http_call_count": 0, "blocked_http_count": 0,
             "lookup_call_count": 0, "cache_hit_count": 0, "http_calls": [], "lookups": [],
             "crossref_http_call_count": 0, "arxiv_http_call_count": 0,
             "provider_http_call_counts": {"crossref": 0, "arxiv": 0}}
    summary = {
        "schema_version": "crossref-live-verification-1", "run_id": f"crossref_{uuid.uuid4().hex}",
        "started_at": _now(), "status": "failed", "reference_only": True, "model_calls": 0,
        "offline_cache": args.offline_cache, "state_path": str(args.state_path),
        "artifact_dir": str(args.artifact_dir), "reused_reference_results": False,
        "execution_versions": request_versions(),
        "implementation_sha256": {
            name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
            for name in (
                "app/credibility/references.py", "app/credibility/reference_extraction.py",
                "app/credibility/crossref_http.py", "app/storage/crossref_state.py",
                "app/credibility/reference_matching.py", "app/credibility/reference_versions.py",
                "app/credibility/reference_version_transport.py", "app/parsing/generic.py",
            )
        },
    }
    try:
        context, provenance = _load_parsing_context(args.input_db, args.input_artifacts, args.assessment_id)
        summary["input"] = provenance
        summary["parsing"] = (_fresh_parse_context(context, args.input_pdf) if args.input_pdf is not None else {
            "mode": "validated_saved_parsing_context", "reused_parsed_blocks": True,
            "parser_name": context.doc.parser_name,
        })
        context.assessment_id, context.model_id, context.generated_at = summary["run_id"], "not_called", _now()
        context.previous_check, context.model_history = None, []
        context.checkpoint_load = context.checkpoint_save = None
        encoded = encode_context(context)
        _write_json(args.output_dir / "input-context.json", encoded)
        summary["input_snapshot_ref"] = capture_snapshot(
            kind="crossref_verification_input", normalized_json={"context": encoded},
            metadata={"run_id": summary["run_id"], "origin_parsing_artifact_ref": provenance["artifact_ref"]},
        )
        result = _execute_references(context, args, audit)
        payload = result.model_dump(mode="json")
        _write_json(args.output_dir / "reference-result.json", payload)
        _write_json(args.output_dir / "sources.json", payload["sources"])
        summary["result_snapshot_ref"] = capture_snapshot(
            kind="crossref_verification_result", normalized_json=payload, metadata={"run_id": summary["run_id"]},
        )
        summary["snapshots"] = verify_report_snapshots(payload)
        summary["reference_counts"] = {name: getattr(result.checks, name) for name in (
            "original_total", "extracted_count", "checked_count", "matched_count", "conflict_count",
            "unverified_count", "not_checked_count", "query_completion_ratio", "extraction_coverage_ratio",
        )}
        summary["source_statuses"] = dict(Counter(source.fetch_status for source in result.sources))
        summary["reference_check_status"] = result.dimension.check_status
        if args.expect_entries is not None and result.checks.extracted_count != args.expect_entries:
            raise VerificationError("Extracted reference count differs from --expect-entries")
        if context.model_history:
            raise VerificationError("Reference-only execution unexpectedly recorded model calls")
        if audit["blocked_http_count"] or (args.offline_cache and (
            audit["http_get_attempt_count"] or audit["http_call_count"]
        )):
            raise VerificationError("Network guard detected a prohibited HTTP attempt")
        summary["status"] = "passed" if (
            result.dimension.check_status == "completed" and result.checks.not_checked_count == 0
            and all(source.fetch_status == "ok" for source in result.sources)
        ) else "incomplete"
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        if isinstance(exc, VerificationError):
            summary["error"] = str(exc)
    finally:
        summary.update({key: value for key, value in audit.items() if key not in {"http_calls", "lookups"}})
        summary["completed_at"] = _now()
        summary["elapsed_seconds"] = round(time.monotonic() - started, 4)
        summary["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        _write_json(args.output_dir / "http-audit.json", audit)
        _write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 2 if summary["status"] == "incomplete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
