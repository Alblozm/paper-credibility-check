"""Persistent execution boundaries; no network or models on import/replay."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.credibility.context import CheckContext, ParsedPaperRef
from app.parsing.base import PaperElement, PaperLink, ParsedDocument, TextBlock
from app.schemas.enums import Confidence, ParseQuality
from app.schemas.paper import Paper


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@lru_cache
def get_task_store():
    from app.storage.credibility_tasks import CredibilityTaskStore

    return CredibilityTaskStore()


def request_versions() -> dict:
    """Only public config; credentials never become a fingerprint or artifact."""
    from app.credibility.claim_model import PROMPT_VERSION
    from app.credibility.code_checks import CODE_CHECKS_VERSION
    from app.credibility.code_discovery import DISCOVERY_VERSION
    from app.credibility.code_discovery import PROMPT_VERSION as DISCOVERY_PROMPT_VERSION
    from app.credibility.code_discovery_provider import PROVIDER_VERSION as DISCOVERY_PROVIDER_VERSION
    from app.credibility.code_model import PROMPT_VERSION as CODE_PROMPT_VERSION
    from app.credibility.paper_link_classifier import PROMPT_VERSION as LINK_PROMPT_VERSION
    from app.credibility.reference_agent import AGENT_VERSION as REFERENCE_AGENT_VERSION
    from app.credibility.reference_agent import PROMPT_VERSION as REFERENCE_AGENT_PROMPT_VERSION
    from app.credibility.reference_arxiv import REFERENCE_ARXIV_VERSION
    from app.credibility.reference_extraction import REFERENCE_HINTS_VERSION
    from app.credibility.reference_fields import PROMPT_VERSION as REFERENCE_FIELDS_PROMPT_VERSION
    from app.credibility.reference_matching import REFERENCE_MATCHING_VERSION
    from app.credibility.reference_publisher_http import PUBLISHER_HTTP_VERSION
    from app.credibility.reference_publishers import REFERENCE_PUBLISHERS_VERSION
    from app.credibility.reference_versions import REFERENCE_VERSION_RESOLVER_VERSION
    from app.credibility.references import REFERENCE_AUTO_VERSION
    from app.credibility.release_classifier import PROMPT_VERSION as RELEASE_PROMPT_VERSION
    from app.credibility.shell import PIPELINE_VERSION

    settings = get_settings()
    return {
        "pipeline": PIPELINE_VERSION, "claim_prompt": PROMPT_VERSION,
        "code_prompt": CODE_PROMPT_VERSION,
        "release_prompt": RELEASE_PROMPT_VERSION,
        "release_classifier": settings.credibility_release_classifier_enabled,
        "link_prompt": LINK_PROMPT_VERSION,
        "link_classifier": settings.credibility_paper_link_classifier_enabled,
        "reference_fields_prompt": REFERENCE_FIELDS_PROMPT_VERSION,
        "reference_field_parser": settings.credibility_reference_field_parser_enabled,
        "reference_agent": REFERENCE_AGENT_VERSION, "reference_agent_prompt": REFERENCE_AGENT_PROMPT_VERSION,
        "reference_agent_enabled": settings.credibility_reference_agent_enabled,
        "code_checks": CODE_CHECKS_VERSION,
        "code_discovery": DISCOVERY_VERSION, "code_discovery_prompt": DISCOVERY_PROMPT_VERSION,
        "code_discovery_provider": DISCOVERY_PROVIDER_VERSION,
        "code_discovery_enabled": settings.credibility_code_discovery_enabled,
        "code_discovery_queries": settings.credibility_code_discovery_max_queries,
        "code_discovery_fetches": settings.credibility_code_discovery_max_fetches,
        "code_discovery_rounds": settings.credibility_code_discovery_max_rounds,
        "code_discovery_tools": settings.credibility_code_discovery_max_tool_calls,
        "reference_hints": REFERENCE_HINTS_VERSION,
        "reference_matching": REFERENCE_MATCHING_VERSION,
        "reference_version_resolver": REFERENCE_VERSION_RESOLVER_VERSION,
        "reference_auto": REFERENCE_AUTO_VERSION,
        "reference_arxiv": REFERENCE_ARXIV_VERSION,
        "reference_publishers": REFERENCE_PUBLISHERS_VERSION,
        "reference_publisher_http": PUBLISHER_HTTP_VERSION,
        "reference_multisource": settings.reference_multisource_enabled,
        "reference_fallback_deadline_seconds": settings.reference_fallback_deadline_seconds,
        "parser": settings.parser_provider, "parse_source": settings.credibility_parse_source,
        "provider": settings.llm_provider,
        "deepseek_model": settings.deepseek_model, "openai_model": settings.openai_model,
        "thinking": settings.deepseek_thinking_enabled, "effort": settings.deepseek_reasoning_effort,
        "openai_effort": settings.openai_reasoning_effort,
        "code_model": settings.credibility_code_model_enabled,
        "tool_mode": settings.credibility_code_tool_mode,
        "tool_rounds": settings.credibility_code_max_rounds,
        "tool_calls": settings.credibility_code_max_tool_calls,
        "tool_characters": settings.credibility_code_max_characters,
        "tool_output_tokens": settings.credibility_code_max_output_tokens,
        "endpoints_sha256": digest([settings.deepseek_base_url, settings.openai_base_url]),
    }


def request_fingerprint(source: dict, model_id: str, rubric_version: str, versions: dict) -> str:
    from app.credibility.rubric import load_credibility_rubric

    source_identity = dict(source)
    if source.get("type") == "pdf":
        from app.storage.uploads import resolve_upload

        try:
            source_identity["pdf_sha256"] = hashlib.sha256(resolve_upload(source["upload_id"]).read_bytes()).hexdigest()
        except Exception:
            source_identity["pdf_sha256"] = None
    return digest({"source": source_identity, "model_id": model_id, "rubric": rubric_version,
                   "rubric_sha256": load_credibility_rubric(rubric_version).content_sha256, "versions": versions})


def encode_context(ctx: CheckContext) -> dict:
    def encode(value):
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return str(value) if isinstance(value, Path) else value.value

    return json.loads(json.dumps({
        "source": ctx.source, "repo_root": str(ctx.repo_root), "generated_at": ctx.generated_at,
        "ref": asdict(ctx.ref) if ctx.ref else None,
        "doc": asdict(ctx.doc) if ctx.doc else None,
        "unresolved_reason": ctx.unresolved_reason, "model_id": ctx.model_id,
    }, default=encode))


def decode_context(payload: dict) -> CheckContext:
    raw = payload["doc"]
    doc = None
    if raw is not None:
        doc = ParsedDocument(
            paper=Paper.model_validate(raw["paper"]),
            blocks=[TextBlock(**{**b, "location_confidence": Confidence(b["location_confidence"])})
                    for b in raw["blocks"]],
            elements=[PaperElement(**{**e, "parse_quality": ParseQuality(e["parse_quality"])})
                      for e in raw["elements"]],
            links=[PaperLink(**link) for link in raw["links"]],
            **{k: v for k, v in raw.items() if k not in {"paper", "blocks", "elements", "links", "parse_quality"}},
            parse_quality=ParseQuality(raw["parse_quality"]),
        )
    ref = payload["ref"]
    return CheckContext(
        source=payload["source"], repo_root=Path(payload["repo_root"]), generated_at=payload["generated_at"],
        ref=ParsedPaperRef(**{**ref, "parsed_dir": Path(ref["parsed_dir"])}) if ref else None,
        doc=doc, unresolved_reason=payload["unresolved_reason"], model_id=payload.get("model_id"),
    )


class StageExecution:
    def __init__(self, store, assessment_id: str, lease, fingerprint: str):
        self.store, self.assessment_id, self.lease = store, assessment_id, lease
        self.fingerprint = fingerprint
        self.events: dict[str, dict] = {}
        self._stop_heartbeat = threading.Event()
        self._heartbeat_thread = None

    def start_heartbeat(self) -> None:
        seconds = get_settings().credibility_lease_seconds

        def renew():
            while not self._stop_heartbeat.wait(min(60, seconds / 3)):
                try:
                    self.store.renew_lease(self.assessment_id, self.lease, lease_seconds=seconds)
                except Exception:
                    self._stop_heartbeat.set()
                    break

        self._heartbeat_thread = threading.Thread(target=renew, daemon=True, name="credibility-lease")
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)

    def load(self, stage: str, fingerprint: str) -> dict | None:
        stored = self.store.load_stage(self.assessment_id, stage, fingerprint)
        if stored is None:
            return None
        from app.credibility.snapshots import read_snapshot

        snapshot = read_snapshot(stored["artifact_ref"])
        metadata = snapshot.manifest["metadata"]
        if snapshot.manifest.get("kind") != "stage_artifact" or metadata.get("status") != "completed":
            raise ValueError("Checkpoint is not a validated completed stage artifact")
        if (metadata.get("assessment_id"), metadata.get("stage"), metadata.get("fingerprint")) != (
            self.assessment_id, stage, fingerprint,
        ):
            raise ValueError("Checkpoint artifact does not belong to this task/stage/version")
        return snapshot.normalized_json

    def load_partial(self, stage: str, fingerprint: str) -> dict | None:
        record = self.store.load_stage_record(self.assessment_id, stage, fingerprint)
        if record is None or record["status"] != "partial":
            return None
        from app.credibility.snapshots import read_snapshot

        snapshot = read_snapshot(record["payload"]["artifact_ref"])
        meta = snapshot.manifest["metadata"]
        if snapshot.manifest.get("kind") != "stage_artifact" or (
            meta.get("assessment_id"), meta.get("stage"), meta.get("fingerprint"), meta.get("status")
        ) != (self.assessment_id, stage, fingerprint, "partial"):
            raise ValueError("Partial checkpoint identity or state mismatch")
        return snapshot.normalized_json

    def save(self, stage: str, fingerprint: str, payload: dict) -> None:
        self.record(stage, fingerprint, payload, status="completed")

    def record(self, stage: str, fingerprint: str, payload: dict, *, status: str, note: str | None = None) -> None:
        from app.credibility.snapshots import capture_snapshot

        ref = capture_snapshot(kind="stage_artifact", normalized_json=payload, metadata={
            "assessment_id": self.assessment_id, "stage": stage, "attempt": self.lease.attempt,
            "fingerprint": fingerprint, "status": status,
        })
        self.store.save_stage(
            self.assessment_id, self.lease, stage, fingerprint, {"artifact_ref": ref}, status=status, note=note,
        )

    def bind(self, ctx: CheckContext) -> None:
        ctx.assessment_id = self.assessment_id
        ctx.checkpoint_load = self.load
        ctx.checkpoint_save = self.save


def replay_task(assessment_id: str, *, store=None) -> dict:
    """Validate the stored report and evidence; never refetch or infer again."""
    from app.credibility.locate import assert_report_links
    from app.credibility.models import CredibilityAssessment
    from app.credibility.snapshots import read_snapshot, verify_report_snapshots

    task = (store or get_task_store()).load_task(assessment_id)
    if task is None:
        raise ValueError("Unknown assessment")
    parsed = CredibilityAssessment.model_validate(task)
    if parsed.report is None:
        raise ValueError("This task has no completed report to replay")
    assert_report_links(parsed.report)
    verify_report_snapshots(parsed.report)
    if parsed.report.external_review and parsed.report.external_review.get("audit_ref"):
        audit = read_snapshot(parsed.report.external_review["audit_ref"]).normalized_json

        def verify_audit_refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.endswith("snapshot_ref") and isinstance(item, str):
                        read_snapshot(item)
                    else:
                        verify_audit_refs(item)
            elif isinstance(value, list):
                for item in value:
                    verify_audit_refs(item)

        verify_audit_refs(audit)
    from app.credibility.item_scoring import score_check_results
    from app.credibility.models import ClaimCheckResult, CodeCheckResult, ReferenceCheckResult
    from app.credibility.rubric import load_credibility_rubric
    from app.credibility.scoring import score_dimensions

    report = parsed.report
    rubric = load_credibility_rubric(report.rubric_version)
    if report.rubric_sha256 is not None and report.rubric_sha256 != rubric.content_sha256:
        raise ValueError("The stored scoring policy is not available unchanged")
    results = score_check_results(
        ClaimCheckResult(applies=True, anchors_matched=True,
                         dimension=report.dimensions[0], checks=report.checks.claims,
                         evidence=[], findings=[]),
        ReferenceCheckResult(dimension=report.dimensions[1], checks=report.checks.references),
        CodeCheckResult(dimension=report.dimensions[2], checks=report.checks.code),
        rubric=rubric, paper_parse_usable=parsed.paper is not None and parsed.paper.parse_quality == "good",
    )
    scored = score_dimensions([result.dimension for result in results], rubric=rubric)
    if [d.score for d in scored.dimensions] != [d.score for d in report.dimensions] or (
        scored.overall.score, scored.overall.category
    ) != (report.overall.score, report.overall.category):
        raise ValueError("Stored scores do not match deterministic replay")
    return {"mode": "stored_evidence_replay", "network_calls": 0, "model_calls": 0, "assessment": task}
