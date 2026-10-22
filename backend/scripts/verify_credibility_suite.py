"""Run fresh live credibility jobs against source-pinned, pre-frozen candidates.

Start the API with its desired execution mode and isolated storage first. This
client submits each paper sequentially. It never reads old reports to re-score,
resumes a job, or changes the server's environment. The candidate expectations
are AI-assisted review prompts, not human Gold or an accuracy benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_BACKEND = Path(__file__).resolve().parents[1]
_DEFAULT_SAMPLES = _BACKEND / "docs" / "credibility" / "acceptance-samples.json"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_DIMENSIONS = {"claim_evidence", "reference_verification", "code_reproduction"}
_CHECK_STAGES = ("claim_check", "reference_check", "code_check")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_samples(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    actual = hashlib.sha256(_canonical({key: value for key, value in manifest.items()
                                       if key != "selection_sha256"})).hexdigest()
    if actual != manifest.get("selection_sha256"):
        raise ValueError("Candidate selection hash mismatch; re-freeze intentional changes before any live run.")
    if manifest.get("is_human_gold") is not False:
        raise ValueError("This candidate suite must explicitly declare that it is not human Gold.")
    ids = [sample["sample_id"] for sample in manifest["samples"]]
    if len(ids) != len(set(ids)) or any(not _SAFE_ID.fullmatch(item) for item in ids):
        raise ValueError("Sample identifiers must be unique, filesystem-safe identifiers.")
    for sample in manifest["samples"]:
        if not re.fullmatch(r"[0-9a-f]{64}", sample["input"]["pdf_sha256"]):
            raise ValueError(f"Missing pinned PDF digest: {sample['sample_id']}.")
    return manifest


def verify_provenance(manifest: dict, backend_root: Path = _BACKEND) -> None:
    roots = {"backend": backend_root, "data": backend_root.parent / "data", "workspace": backend_root.parents[1]}
    for sample in manifest["samples"]:
        for evidence in sample.get("provenance", []):
            root = roots[evidence["repository"]].resolve()
            path = (root / evidence["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Provenance path escapes its repository.")
            if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                raise ValueError(f"Frozen provenance changed: {evidence['path']}.")


def _select(manifest: dict, ids: list[str] | None, limit: int | None) -> list[dict]:
    wanted = set(ids or [])
    available = {sample["sample_id"] for sample in manifest["samples"]}
    if wanted - available:
        raise ValueError(f"Unknown sample identifiers: {sorted(wanted - available)}")
    selected = [sample for sample in manifest["samples"] if not wanted or sample["sample_id"] in wanted]
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be positive.")
        selected = selected[:limit]
    return selected


def _pdf_source(client: httpx.Client, sample: dict, output: Path, local_pdf: Path | None) -> dict:
    source = sample["input"]
    if local_pdf is None and source["type"] == "arxiv":
        return {"type": "arxiv", "arxiv_id": source["arxiv_id"], "upload_id": None}
    if local_pdf is not None:
        content = local_pdf.read_bytes()
        metadata = {"source": "explicit_local_pdf", "filename": local_pdf.name}
    else:
        # Every URL comes directly from the frozen source record; no search or guessed URL.
        url = source["pdf_url"]
        if not url.startswith("https://"):
            raise ValueError("Candidate PDF source must use HTTPS.")
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        content = response.content
        metadata = {"source": "frozen_pdf_url", "requested_url": url, "final_url": str(response.url),
                    "retrieved_at": _now(), "http_status": response.status_code}
    digest = hashlib.sha256(content).hexdigest()
    metadata["sha256"] = digest
    _write(output / "input-metadata.json", metadata)
    if digest != source["pdf_sha256"]:
        raise ValueError("Downloaded/provided PDF does not match the frozen paper version digest.")
    if not content.startswith(b"%PDF-"):
        raise ValueError("Candidate input is not a PDF.")
    (output / "input.pdf").write_bytes(content)
    uploaded = client.post("/v1/uploads", files={"file": (sample["sample_id"] + ".pdf", content, "application/pdf")})
    uploaded.raise_for_status()
    upload = uploaded.json()
    _write(output / "upload.json", upload)
    if upload.get("sha256") != digest:
        raise ValueError("API upload digest does not match the pinned paper version.")
    return {"type": "pdf", "arxiv_id": None, "upload_id": upload["upload_id"]}


def _summary(sample: dict, assessment: dict, elapsed: float, reused_ids: set[str]) -> dict:
    report = assessment.get("report") or {}
    dimensions = report.get("dimensions", [])
    issues = []
    scoring_issues = []
    identifier = assessment.get("assessment_id")
    if not identifier or identifier in reused_ids:
        issues.append("Assessment ID is absent or repeated; fresh-job requirement failed.")
    if identifier:
        reused_ids.add(identifier)
    if assessment.get("status") != "completed":
        issues.append("Assessment did not complete.")
    if assessment.get("data_origin") != "live":
        issues.append("Assessment is not labelled live.")
    if (assessment.get("paper") or {}).get("source_sha256") != sample["input"]["pdf_sha256"]:
        issues.append("Reported PDF digest differs from frozen candidate version.")
    if {item["dimension_id"] for item in dimensions} != _DIMENSIONS:
        scoring_issues.append("The three credibility dimensions were not all returned.")
    def valid_score(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 10

    for dimension in dimensions:
        if dimension.get("applicability") != "not_applicable" and not valid_score(dimension.get("score")):
            scoring_issues.append(f"No valid numeric trial score for {dimension['dimension_id']}.")
    if not valid_score(report.get("overall", {}).get("score")):
        scoring_issues.append("No valid numeric overall trial score was returned.")
    issues.extend(scoring_issues)
    calls = report.get("versions", {}).get("model_calls", [])
    if not calls:
        issues.append("No model calls were recorded.")
    execution = report.get("execution")
    executed_checks = execution.get("checks") if isinstance(execution, dict) else None
    execution_issues = []
    for stage in _CHECK_STAGES:
        event = executed_checks.get(stage) if isinstance(executed_checks, dict) else None
        status = event.get("status") if isinstance(event, dict) else None
        if status != "completed":
            execution_issues.append(f"Check execution did not complete: {stage}={status or 'missing'}.")
    for dimension in dimensions:
        if dimension.get("check_status") in {"failed", "not_run", "running"}:
            execution_issues.append(
                f"Dimension check did not complete: {dimension['dimension_id']}={dimension['check_status']}.")
    issues.extend(execution_issues)
    sources = report.get("sources", [])
    if any(not source.get("snapshot_ref") for source in sources):
        issues.append("At least one external source has no evidence snapshot.")
    if not report.get("coverage"):
        issues.append("Execution coverage was not returned separately from the trial score.")
    external_review = report.get("external_review")
    semantic_issues = []
    external_enabled = False
    if not isinstance(external_review, dict):
        semantic_issues.append("External model review status was not returned.")
    else:
        external_enabled = external_review.get(
            "enabled", external_review.get("mode") in {"prefetch", "native_tools"}
        ) is True
        if not external_enabled:
            semantic_issues.append("External model review does not confirm an enabled review mode.")
        if external_review.get("status") not in {"completed", "not_needed"}:
            semantic_issues.append(
                f"External semantic review did not complete: {external_review.get('status', 'missing')}.")
    issues.extend(semantic_issues)
    return {
        "sample_id": sample["sample_id"], "title": sample["title"], "assessment_id": identifier,
        "status": assessment.get("status"), "data_origin": assessment.get("data_origin"),
        "elapsed_seconds": round(elapsed, 3), "source_sha256": (assessment.get("paper") or {}).get("source_sha256"),
        "dimensions": dimensions, "overall": report.get("overall"), "coverage": report.get("coverage"),
        "execution": report.get("execution"), "external_review": report.get("external_review"),
        "versions": report.get("versions"), "model_call_count": len(calls),
        "source_count": len(sources), "source_status_counts": {
            status: sum(source.get("fetch_status") == status for source in sources)
            for status in sorted({str(source.get("fetch_status")) for source in sources})
        },
        "error": assessment.get("error"), "technical_acceptance_passed": not issues, "issues": issues,
        "pipeline_scoring_passed": not scoring_issues, "pipeline_scoring_issues": scoring_issues,
        "execution_acceptance_passed": not execution_issues, "execution_issues": execution_issues,
        "external_review_enabled": external_enabled,
        "external_semantic_acceptance_passed": not semantic_issues,
        "external_semantic_issues": semantic_issues,
        "manual_review_status": "pending", "is_human_gold": False,
    }


def run_suite(
    client: httpx.Client, manifest: dict, *, output: Path, model_id: str = "model_standard_v1",
    provider: str = "deepseek", ids: list[str] | None = None, limit: int | None = None,
    local_pdfs: dict[str, Path] | None = None, poll_seconds: float = 4, timeout_seconds: float = 1800,
    service_label: str | None = None, sleep=time.sleep, clock=time.monotonic,
) -> dict:
    selected = _select(manifest, ids, limit)
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental overwriting/reuse of a previous run.
    with (output / "run-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump({"started_at": _now(), "selection_sha256": manifest["selection_sha256"],
                   "selected_ids": [sample["sample_id"] for sample in selected], "model_id": model_id,
                   "required_provider": provider, "service_label_unverified": service_label,
                   "base_url": str(client.base_url), "fresh_jobs_only": True,
                   "note": "Execution mode must be configured when starting the API; this client does not change it."},
                  handle, ensure_ascii=False, indent=2)
    _write(output / "frozen-selection.json", manifest)
    health = client.get("/health")
    health.raise_for_status()
    models = client.get("/v1/models")
    models.raise_for_status()
    service_metadata = {"observed_at": _now(), "health": health.json(), "models": models.json()}
    _write(output / "service-metadata.json", service_metadata)
    selected_model = next((item for item in models.json() if item.get("model_id") == model_id), None)
    if not selected_model or not selected_model.get("is_available") or selected_model.get("provider") != provider:
        raise ValueError("The selected real model/provider is not available on this API.")
    summaries, seen = [], set()
    for sample in selected:
        folder = output / sample["sample_id"]
        folder.mkdir()
        start = clock()
        identifier = None
        print(f"{sample['sample_id']}: starting fresh live assessment", flush=True)
        try:
            source = _pdf_source(client, sample, folder, (local_pdfs or {}).get(sample["sample_id"]))
            request = {"schema_version": "credibility-0.2.0-draft", "analysis_mode": "credibility", "source": source,
                       "model_id": model_id, "rubric_version": "0.2-provisional-complete"}
            _write(folder / "request.json", request)
            created = client.post("/v1/credibility/assessments", json=request)
            created.raise_for_status()
            assessment = created.json()
            _write(folder / "created.json", assessment)
            identifier = assessment["assessment_id"]
            deadline, previous, last_print = start + timeout_seconds, None, clock()
            while assessment.get("status") not in {"completed", "failed", "cancelled"}:
                if clock() >= deadline:
                    raise TimeoutError(f"Timed out waiting for new assessment {identifier}.")
                polled = client.get("/v1/credibility/assessments/" + identifier)
                polled.raise_for_status()
                assessment = polled.json()
                _write(folder / "latest.json", assessment)
                state = (assessment.get("status"), assessment.get("stage"), assessment.get("progress_percent"))
                if state != previous or clock() - last_print >= 30:
                    print(f"{sample['sample_id']}: {state} ({clock() - start:.1f}s)", flush=True)
                    previous, last_print = state, clock()
                if assessment.get("status") not in {"completed", "failed", "cancelled"}:
                    sleep(min(poll_seconds, max(0, deadline - clock())))
            _write(folder / "assessment.json", assessment)
            if assessment.get("report"):
                _write(folder / "report.json", assessment["report"])
            summary = _summary(sample, assessment, clock() - start, seen)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TimeoutError) as exc:
            summary = {"sample_id": sample["sample_id"], "assessment_id": identifier, "status": "client_error",
                       "elapsed_seconds": round(clock() - start, 3), "technical_acceptance_passed": False,
                       "issues": [str(exc)], "pipeline_scoring_passed": False,
                       "execution_acceptance_passed": False,
                       "external_semantic_acceptance_passed": False,
                       "manual_review_status": "pending", "is_human_gold": False}
        _write(folder / "summary.json", summary)
        summaries.append(summary)
        result = {"completed_at": _now(), "selection_sha256": manifest["selection_sha256"],
                  "selected_count": len(selected), "finished_count": len(summaries),
                  "technical_pass_count": sum(item["technical_acceptance_passed"] for item in summaries),
                  "pipeline_scoring_pass_count": sum(item["pipeline_scoring_passed"] for item in summaries),
                  "execution_pass_count": sum(item["execution_acceptance_passed"] for item in summaries),
                  "external_semantic_pass_count": sum(
                      item["external_semantic_acceptance_passed"] for item in summaries),
                  "human_gold_validated": False, "coverage_gaps": manifest.get("coverage_gaps", []),
                  "samples": summaries}
        _write(output / "summary.json", result)
        print(f"{sample['sample_id']}: technical_pass={summary['technical_acceptance_passed']}", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", type=Path, default=_DEFAULT_SAMPLES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", default="model_standard_v1")
    parser.add_argument("--provider", choices=("deepseek", "openai"), default="deepseek")
    parser.add_argument("--ids", nargs="+", help="Subset of sample IDs, in frozen manifest order.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pdf", action="append", default=[], metavar="SAMPLE_ID=PATH",
                        help="Explicit local PDF override, always checked against the frozen digest.")
    parser.add_argument("--poll-seconds", type=float, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--service-label", help="Human label only; actual execution settings come from reports.")
    parser.add_argument("--skip-provenance-check", action="store_true",
                        help="Skip local provenance files on standalone deployments; candidate hash stays required.")
    args = parser.parse_args()
    if args.poll_seconds <= 0 or args.timeout_seconds <= 0:
        parser.error("Polling interval and timeout must be positive.")
    manifest = load_samples(args.samples)
    if not args.skip_provenance_check:
        verify_provenance(manifest)
    local_pdfs = {}
    for item in args.pdf:
        identifier, separator, path = item.partition("=")
        if not separator or identifier in local_pdfs:
            parser.error("--pdf must specify a unique SAMPLE_ID=PATH.")
        local_pdfs[identifier] = Path(path)
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60) as client:
        result = run_suite(client, manifest, output=args.output, model_id=args.model_id, provider=args.provider,
                           ids=args.ids, limit=args.limit, local_pdfs=local_pdfs, poll_seconds=args.poll_seconds,
                           timeout_seconds=args.timeout_seconds, service_label=args.service_label)
    return 0 if result["technical_pass_count"] == result["selected_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
