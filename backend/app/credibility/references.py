"""文末引用的存在性核查（任务二）。

这一模块只回答「论文末尾列出的文献，能否在公开元数据中找到？」。
它不调用 LLM、不阅读被引论文全文，也不判断被引论文是否支持正文论点。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
import unicodedata
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import httpx

from app.config import get_settings
from app.credibility.context import CheckContext
from app.credibility.crossref_http import CrossrefHTTP, recommended_interval, retry_after_seconds
from app.credibility.locate import PaperSpanCandidate, locate_paper_span
from app.credibility.models import (
    CredibilityBibliographicMetadata,
    CredibilityDimension,
    CredibilityEvidence,
    CredibilityExternalSource,
    CredibilityFinding,
    CredibilityReferenceCandidate,
    CredibilityReferenceChecks,
    CredibilityReferenceItem,
    ReferenceCheckResult,
)
from app.credibility.reference_extraction import (
    ReferenceEntry as _ReferenceEntry,
)
from app.credibility.reference_extraction import extract_reference_entries, metadata_hints
from app.credibility.reference_matching import classify_reference
from app.credibility.reference_sources import ReferenceFallback
from app.credibility.reference_versions import resolve_versions
from app.credibility.snapshots import (
    SnapshotError,
    artifact_root,
    capture_snapshot,
    derive_snapshot,
    read_snapshot,
    verify_report_snapshots,
)
from app.storage.crossref_state import (
    CacheEntry,
    CorruptCacheEntryError,
    CrossrefStateStore,
    StaleReservationError,
    Wait,
)

_REFERENCE_TITLE_RE = re.compile(r"\b(?:references?|bibliography)\b", re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_HTTPX_GET = httpx.get
REFERENCE_AUTO_VERSION = "reference-auto-1.1"


@dataclass
class _RetryBudget:
    remaining: float
    deadline: float


_RETRY_BUDGET: ContextVar[_RetryBudget | None] = ContextVar("crossref_retry_budget", default=None)


@contextmanager
def reference_retry_budget(seconds: float):
    """One cumulative wait allowance per reference branch, isolated across tasks."""
    deadline = time.monotonic() + get_settings().crossref_branch_deadline_seconds
    token = _RETRY_BUDGET.set(_RetryBudget(max(0.0, seconds), deadline))
    try:
        yield
    finally:
        _RETRY_BUDGET.reset(token)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalise(value: str | None) -> str:
    """给 DOI、标题和作者做保守比较用的标准化，不用于展示原文。"""
    return " ".join(_WORD_RE.findall((value or "").casefold()))


def _clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip(".,;:)]}").casefold() or None


@dataclass(frozen=True)
class _LookupResult:
    """一次外部元数据查询的结果；网络故障不能被伪装成「没有匹配」。"""

    url: str
    fetch_status: str
    http_status: int | None
    retrieved_at: str
    candidates: tuple[CredibilityBibliographicMetadata, ...] = ()
    is_cached: bool = False
    snapshot_ref: str | None = None


class CrossrefLookup:
    """Shared FIFO pacing, single-flight HTTP, and durable successful responses.

    All workers must use one provider-wide state path. Successes are read before
    cooldowns; failures never replace them. Retry-After has a branch-wide budget,
    while normal queueing/pacing has a separate overall deadline. Every response
    gets an immutable observation, including failures and cache reuse.
    """

    def __init__(
        self, *, min_interval_seconds: float | None = None, timeout_seconds: float | None = None,
        retry_wait_budget_seconds: float | None = None, state_path: str | None = None,
    ) -> None:
        configured_wait = retry_wait_budget_seconds if retry_wait_budget_seconds is not None else float(
            os.environ.get("CREDIBILITY_CROSSREF_WAIT_BUDGET_SECONDS", "60")
        )
        if not math.isfinite(configured_wait) or not 0 <= configured_wait <= 300:
            raise ValueError("Crossref total wait budget must be between 0 and 300 seconds.")
        for value in (min_interval_seconds, timeout_seconds):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("Crossref timing must be finite and nonnegative.")
        self.retry_wait_budget_seconds = configured_wait
        self._min_interval_seconds = min_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._state_path = state_path

    def _state(self) -> CrossrefStateStore:
        return CrossrefStateStore(
            self._state_path or get_settings().crossref_state_path, clock=lambda: time.time(),
            max_raw_bytes=2_000_000,
        )

    def clear_failed_cache(self) -> None:
        """Failures are never cached. An explicit resume must retain provider cooldowns."""

    def lookup(
        self, metadata: CredibilityBibliographicMetadata, *, prior_ref: str | None = None,
    ) -> _LookupResult:
        budget = _RETRY_BUDGET.get()
        if budget is None:
            with reference_retry_budget(self.retry_wait_budget_seconds):
                return self.lookup(metadata, prior_ref=prior_ref)
        settings = get_settings()
        state = self._state()
        key, url = self._request_for(metadata)
        interval = self._min_interval_seconds
        if interval is None:
            interval = settings.crossref_min_interval_seconds
        timeout = self._timeout_seconds
        if timeout is None:
            timeout = settings.crossref_timeout_seconds
        attempts = 0
        last_result = None
        while True:
            try:
                cached = state.lookup(key)
                if cached is not None:
                    return self._cached_result(cached, metadata, prior_ref)
                remaining = budget.deadline - time.monotonic()
                if remaining <= 0:
                    state.abandon(key)
                    return self._pending(url, "timeout", prior_ref, "branch_deadline")
                reservation = state.reserve(key, interval_seconds=interval, lease_seconds=timeout * 5 + 30)
            except CorruptCacheEntryError:
                state.invalidate(key)
                continue
            if isinstance(reservation, Wait):
                if reservation.reason == "cached":
                    continue
                delay = reservation.seconds
                if reservation.reason == "cooldown":
                    if delay > budget.remaining or delay > remaining:
                        state.abandon(key)
                        return last_result or self._pending(url, "rate_limited", prior_ref, "shared_cooldown")
                    # Shared retry allowance excludes ordinary spacing and FIFO waits.
                    budget.remaining = max(0.0, budget.remaining - delay)
                else:
                    delay = min(delay, 1.0)
                if delay > remaining:
                    state.abandon(key)
                    return self._pending(url, "timeout", prior_ref, "branch_deadline")
                try:
                    time.sleep(delay)
                except BaseException:
                    state.abandon(key)
                    raise
                continue
            # SQLite acquisition can itself wait on another writer.
            remaining = budget.deadline - time.monotonic()
            if remaining <= 0:
                state.cancel(reservation.token)
                return self._pending(url, "timeout", prior_ref, "branch_deadline")
            try:
                response = CrossrefHTTP(
                    mailto=settings.crossref_mailto.get_secret_value(),
                    timeout_seconds=min(timeout, max(0.01, remaining)),
                    getter=None if httpx.get is _DEFAULT_HTTPX_GET else httpx.get,
                ).fetch(url)
                retry_after = retry_after_seconds(response.headers, time.time())
                # Provider cooldown survives even if the evidence disk fails next.
                state.update_limits(
                    interval_seconds=recommended_interval(response.headers, interval),
                    retry_after_seconds=(30.0 if retry_after is None else retry_after)
                    if response.fetch_status == "rate_limited" else 0,
                )
                candidates = tuple(_crossref_candidates(response.payload, doi_lookup=metadata.doi is not None)) \
                    if response.fetch_status == "ok" else ()
                ref = capture_snapshot(
                    kind="crossref_response", raw=response.raw, normalized_json=response.payload,
                    requested_url=url, final_url=response.final_url, retrieved_at=response.retrieved_at,
                    fetch_status=response.fetch_status, http_status=response.http_status,
                    truncated=response.error_code == "response_too_large",
                    metadata={"provider": "crossref", "raw_available": response.raw is not None,
                              "response_headers": response.headers, "error_code": response.error_code},
                    parent_ref=prior_ref,
                )
                last_result = _LookupResult(
                    url, response.fetch_status, response.http_status, response.retrieved_at,
                    candidates, snapshot_ref=ref,
                )
                state.finish(
                    reservation.token,
                    payload=response.payload if response.fetch_status == "ok" else None,
                    raw=response.raw if response.fetch_status == "ok" else None,
                    http_status=response.http_status, url=url, final_url=response.final_url,
                    retrieved_at=response.retrieved_at, ttl_seconds=settings.crossref_cache_ttl_seconds,
                    retry_after_seconds=0,  # Already persisted on receipt, without extending it twice.
                    interval_seconds=recommended_interval(response.headers, interval),
                    snapshot_ref=ref, artifact_namespace=str(artifact_root().resolve()),
                )
            except BaseException:
                # Cancellation and evidence failures release our own lease only.
                try:
                    state.cancel(reservation.token)
                except StaleReservationError:
                    pass
                raise
            attempts += 1
            if last_result.fetch_status != "rate_limited" or attempts >= 2:
                return last_result
            prior_ref = last_result.snapshot_ref

    @staticmethod
    def _pending(url: str, status: str, prior_ref: str | None, reason: str) -> _LookupResult:
        stamp = _now()
        return _LookupResult(url, status, None, stamp, snapshot_ref=capture_snapshot(
            kind="crossref_response", requested_url=url, final_url=url, retrieved_at=stamp,
            fetch_status=status, metadata={"provider": "crossref", "request_sent": False, "reason": reason},
            parent_ref=prior_ref,
        ))

    @staticmethod
    def _cached_result(
        cached: CacheEntry, metadata: CredibilityBibliographicMetadata, prior_ref: str | None,
    ) -> _LookupResult:
        origin = cached.local_snapshot_ref(str(artifact_root().resolve()))
        if origin:
            try:
                read_snapshot(origin)
            except SnapshotError:
                origin = None
        ref = capture_snapshot(
            kind="crossref_response", raw=cached.raw, normalized_json=cached.payload,
            requested_url=cached.url, final_url=cached.final_url, retrieved_at=cached.retrieved_at,
            fetch_status="ok", http_status=200, is_cached=True, parent_ref=prior_ref or origin,
            metadata={"provider": "crossref", "raw_available": cached.raw is not None,
                      "normalized_only": cached.normalized_only, "cache_payload_sha256": cached.payload_sha256,
                      "origin_snapshot_ref": cached.origin_snapshot_ref},
        )
        return _LookupResult(
            cached.url or "", "ok", 200, cached.retrieved_at or _now(),
            tuple(_crossref_candidates(cached.payload, doi_lookup=metadata.doi is not None)),
            is_cached=True, snapshot_ref=ref,
        )

    @staticmethod
    def _request_for(metadata: CredibilityBibliographicMetadata) -> tuple[str, str]:
        if metadata.doi:
            doi = _clean_doi(metadata.doi) or metadata.doi
            return f"doi:{doi}", f"https://api.crossref.org/works/{quote(doi, safe='')}"
        query = " ".join(
            part for part in (metadata.title, " ".join(metadata.authors[:1]), str(metadata.year or "")) if part
        )
        canonical = " ".join(unicodedata.normalize("NFC", query).casefold().split())
        key = "query:v2:" + hashlib.sha256(canonical.encode()).hexdigest()
        return key, f"https://api.crossref.org/works?{urlencode({'query.bibliographic': query, 'rows': 5})}"


_LOOKUP = CrossrefLookup()


def check_references(ctx: CheckContext) -> ReferenceCheckResult:
    """执行任务二；同一分支的限流等待总额默认不超过 60 秒。"""
    with reference_retry_budget(getattr(_LOOKUP, "retry_wait_budget_seconds", 60.0)):
        result = _check_references(ctx)
    if get_settings().reference_multisource_enabled and isinstance(_LOOKUP, CrossrefLookup):
        return _supplement_references(ctx, result)
    return result


def _supplement_references(ctx: CheckContext, result: ReferenceCheckResult) -> ReferenceCheckResult:
    """Complete unresolved items through primary sources, keeping every attempt.

    Crossref runs first so supplemental source latency never consumes the
    provider's separate branch retry budget. An evidence-backed match may
    recover an earlier provider failure; failures remain in the source audit.
    """
    from app.credibility.reference_arxiv import resolve_arxiv
    from app.credibility.reference_publishers import resolve_publishers

    deadline = time.monotonic() + get_settings().reference_fallback_deadline_seconds
    recovered = set()
    budget_exhausted = False
    for item in result.checks.items:
        if item.outcome not in {"unverified", "not_checked"}:
            continue
        if not item.paper_evidence_ids:
            item.reason = "[extraction_gap] " + item.reason
            continue
        if re.search(r"\bpersonal\s+communication\b", item.raw_text, re.I):
            item.reason = "[personal_communication] Private communication has no public publication record to verify."
            continue
        if not item.parsed.title or not item.parsed.authors:
            item.reason = ("[insufficient_metadata] A title and listed authors are required "
                           "for official-source matching.")
            continue
        if time.monotonic() >= deadline:
            budget_exhausted = True
            item.reason = "[budget_exhausted] The bounded supplementary reference branch reached its deadline."
            continue
        primary_ref = next((s.snapshot_ref for s in result.sources
                            if s.source_id in item.source_ids and s.snapshot_ref
                            and "_metadata" in s.source_id), None)
        resolutions = []
        for provider in ("arxiv", "publisher"):
            identity_ref = resolutions[0].identity_snapshot_ref if resolutions else None
            if provider == "publisher" and item.parsed.arxiv_id and not identity_ref:
                # A title search cannot repair an unbound explicit identifier.
                # Publication-year differences can still be resolved below when
                # arXiv independently establishes this ID, title and author list.
                resolutions[0].reason += (
                    " The cited arXiv identity was not established; an unrelated "
                    "publication search cannot substitute for that identifier."
                )
                break
            try:
                resolution = (resolve_arxiv(item.parsed, raw_text=item.raw_text) if provider == "arxiv"
                              else resolve_publishers(item.parsed, raw_text=item.raw_text,
                                                      crossref_snapshot_ref=primary_ref))
            except (SnapshotError, OSError):
                # Evidence durability failures must reach the stage boundary.
                raise
            except Exception as exc:
                stamp = _now()
                failure_ref = capture_snapshot(
                    kind="reference_fallback_error", fetch_status="error", retrieved_at=stamp,
                    metadata={"provider": provider, "error_type": type(exc).__name__,
                              "reference_id": item.reference_id},
                )
                from app.credibility.reference_sources import ReferenceObservation

                resolution = ReferenceFallback(observations=[ReferenceObservation(
                    "https://arxiv.org/" if provider == "arxiv" else "https://proceedings.mlr.press/",
                    "error", None, stamp, False, failure_ref, provider,
                )], reason_code="source_fetch_failed", reason="Supplementary provider could not complete its lookup.")
            if provider == "publisher" and identity_ref and resolution.confirmations:
                _bind_publication_identity(item, resolution, identity_ref)
            resolutions.append(resolution)
            _attach_fallback(result, item, provider, resolution)
            if resolution.confirmations:
                recovered.add(item.reference_id)
                break
            if time.monotonic() >= deadline:
                budget_exhausted = True
                break
        if item.reference_id not in recovered:
            informative = next((row for row in resolutions if row.reason_code in {
                "year_mismatch", "author_mismatch", "version_evidence_missing",
                "identifier_mismatch", "invalid_arxiv_id", "title_mismatch",
            }), None)
            if informative is None:
                informative = next((row for row in resolutions if row.reason_code == "source_fetch_failed"
                                    or any(o.fetch_status != "ok" for o in row.observations)), None)
                if informative is not None:
                    informative = replace(informative, reason_code="source_fetch_failed")
            informative = informative or (resolutions[-1] if resolutions else ReferenceFallback())
            details = "; ".join(dict.fromkeys(row.reason for row in resolutions))
            item.reason = f"[{informative.reason_code}] {details or informative.reason}"
    agent_matched, agent_summary = _reference_agent_pass(ctx, result)
    recovered |= agent_matched
    findings = [finding for finding in result.findings if not (
        finding.kind == "verification_gap" and finding.item_ids
        and all(reference_id in recovered for reference_id in finding.item_ids)
    )]
    for finding in findings:
        item = next((row for row in result.checks.items if row.reference_id in finding.item_ids), None)
        if item is not None and item.outcome in {"unverified", "not_checked"}:
            finding.detail = item.reason
            finding.source_ids = list(item.source_ids)
    enriched = _result(result.checks.items, result.evidence, result.sources, findings,
                       result.checks.original_total, result.checks.gaps)
    enriched.model_calls = result.model_calls
    enriched.agent_audit_ref = result.agent_audit_ref
    if agent_summary:
        enriched.dimension.summary += " " + agent_summary
    if budget_exhausted:
        enriched.dimension.check_status = "partial"
        enriched.dimension.summary += " Supplementary reference source budget was exhausted."
    return enriched


def _reference_agent_pass(ctx: CheckContext, result: ReferenceCheckResult) -> tuple[set[str], str]:
    """Bounded tool-using agent for items still unresolved; the program verifies every proposal."""
    if not get_settings().credibility_reference_agent_enabled or ctx.model_id in {None, "not_called"}:
        return set(), ""
    from app.credibility.reference_agent import verify_unresolved_references

    return verify_unresolved_references(ctx, result)


def _bind_publication_identity(item, resolution: ReferenceFallback, identity_ref: str) -> None:
    """Retain both proofs when arXiv establishes identity and a venue its date."""
    identity = read_snapshot(identity_ref)
    if identity.manifest.get("fetch_status") != "ok" or not identity.raw:
        raise SnapshotError("Publication confirmation has no archived arXiv identity proof")
    for index, confirmation in enumerate(resolution.confirmations):
        publication = read_snapshot(confirmation.snapshot_ref)
        if publication.manifest.get("fetch_status") != "ok":
            raise SnapshotError("Publication confirmation has no successful source proof")
        metadata = confirmation.metadata.model_copy(update={"arxiv_id": item.parsed.arxiv_id})
        proof = capture_snapshot(
            kind="reference_identity_publication_confirmation", raw=publication.raw,
            normalized_json={
                "relationship": "same_arxiv_identity_with_official_publication_date",
                "parsed_metadata": item.parsed.model_dump(mode="json"),
                "confirmed_metadata": metadata.model_dump(mode="json"),
                "arxiv_identity_snapshot_ref": identity_ref,
                "publication_confirmation_snapshot_ref": confirmation.snapshot_ref,
            }, parent_ref=confirmation.snapshot_ref, fetch_status="ok", http_status=200,
            requested_url=confirmation.url, final_url=confirmation.url,
            retrieved_at=publication.manifest["retrieved_at"],
            is_cached=bool(publication.manifest.get("is_cached")),
            metadata={"resolver_version": REFERENCE_AUTO_VERSION},
        )
        resolution.confirmations[index] = replace(confirmation, metadata=metadata, snapshot_ref=proof,
            reason=confirmation.reason + " The cited arXiv ID is independently bound by archived official metadata.")


def _attach_fallback(result, item, provider: str, resolution: ReferenceFallback) -> None:
    for number, observation in enumerate(resolution.observations, 1):
        source_id = f"src_{item.reference_id}_auto_{provider}_{number}"
        snapshot_ref = derive_snapshot(observation.snapshot_ref, metadata={
            "source_id": source_id, "source_url": observation.url, "reference_id": item.reference_id,
        })
        result.sources.append(CredibilityExternalSource(
            source_id=source_id, kind="metadata", url=observation.url,
            fetch_status=observation.fetch_status, http_status=observation.http_status,
            retrieved_at=observation.retrieved_at, observed_at=read_snapshot(snapshot_ref).manifest["observed_at"],
            is_cached=observation.is_cached, snapshot_ref=snapshot_ref,
            association_reason=f"Automatic {observation.provider} reference lookup for {item.reference_id}.",
        ))
        item.source_ids.append(source_id)
    for number, confirmation in enumerate(resolution.confirmations, 1):
        proof = read_snapshot(confirmation.snapshot_ref)
        if proof.manifest.get("fetch_status") != "ok":
            raise SnapshotError("Reference confirmation has no successful source proof")
        source_id = f"src_{item.reference_id}_auto_{provider}_proof_{number}"
        snapshot_ref = derive_snapshot(confirmation.snapshot_ref, metadata={
            "source_id": source_id, "source_url": confirmation.url, "reference_id": item.reference_id,
        })
        result.sources.append(CredibilityExternalSource(
            source_id=source_id, kind="metadata", url=confirmation.url, fetch_status="ok", http_status=200,
            retrieved_at=proof.manifest["retrieved_at"],
            observed_at=read_snapshot(snapshot_ref).manifest["observed_at"],
            is_cached=bool(proof.manifest.get("is_cached")), snapshot_ref=snapshot_ref,
            association_reason=f"Source-backed automatic {confirmation.provider} identity confirmation.",
        ))
        evidence_id = f"ev_{item.reference_id}_auto_{provider}_{number}"
        result.evidence.append(CredibilityEvidence(
            evidence_id=evidence_id, origin="external", mode="paraphrase", excerpt=confirmation.reason,
            paper_locator=None, source_id=source_id, location_confidence="high",
        ))
        candidate_id = f"cand_{item.reference_id}_auto_{provider}_{number}"
        item.candidates.append(CredibilityReferenceCandidate(
            candidate_id=candidate_id, metadata=confirmation.metadata, source_id=source_id,
            evidence_ids=[evidence_id], match_basis=confirmation.reason,
        ))
        item.source_ids.append(source_id)
        if number == 1:
            item.outcome, item.selected_candidate_id = "matched", candidate_id
            item.reason = confirmation.reason


def _check_references(ctx: CheckContext) -> ReferenceCheckResult:
    entries, original_total, gaps = _extract_entries(ctx)
    if ctx.doc is None:
        return _empty_failed(
            "The paper has not been parsed, so references cannot be extracted.",
            ["The paper has not been parsed."],
            check_status="not_run",
        )
    if not entries:
        reason = "No trustworthy reference entries could be extracted from the parsed paper."
        if gaps:
            reason += " " + gaps[0]
        return _empty_failed(reason, gaps)

    items: list[CredibilityReferenceItem] = []
    evidence: list[CredibilityEvidence] = []
    sources: list[CredibilityExternalSource] = []
    findings: list[CredibilityFinding] = []
    try:
        previous = ReferenceCheckResult.model_validate(ctx.previous_check) if ctx.previous_check else None
    except ValueError:
        previous = None
    model_calls: list = []
    model_fields = _model_fields(ctx, entries, model_calls)
    for index, entry in enumerate(entries, start=1):
        reference_id = f"ref_{index:03d}"
        paper_evidence, item_gaps = _paper_evidence(ctx, entry, reference_id)
        gaps.extend(item_gaps)
        reusable = _reusable_reference(previous, reference_id, entry.text, ctx) if not item_gaps else None
        if reusable is not None:
            item, old_evidence, old_sources, old_findings = reusable
            items.append(item)
            evidence.extend(old_evidence)
            sources.extend(old_sources)
            findings.extend(old_findings)
            continue
        evidence.extend(paper_evidence)
        parsed = _parse_metadata(
            "\n".join(fragment.text for fragment in entry.fragments),
            model_fields[index - 1] if model_fields else None,
        )

        if item_gaps or not paper_evidence:
            items.append(
                _not_checked_item(
                    reference_id, entry.text, paper_evidence, parsed,
                    "The complete reference could not be linked to its parsed source text.",
                )
            )
            continue

        # 没有 DOI、标题、作者、年份中的任何一种，就没有可负责任地发出的查询。
        if not any((parsed.doi, parsed.title, parsed.authors, parsed.year)):
            items.append(
                _not_checked_item(
                    reference_id,
                    entry.text,
                    paper_evidence,
                    parsed,
                    "The entry has no usable DOI, title, author, or year.",
                )
            )
            continue

        prior_ref = _previous_failed_snapshot(previous, reference_id, entry.text)
        lookup = (
            _LOOKUP.lookup(parsed, prior_ref=prior_ref) if isinstance(_LOOKUP, CrossrefLookup)
            else _LOOKUP.lookup(parsed)
        )
        source_id = f"src_{reference_id}_metadata"
        source = _external_source(source_id, lookup, reference_id)
        sources.append(source)
        if lookup.fetch_status != "ok":
            items.append(
                _not_checked_item(
                    reference_id,
                    entry.text,
                    paper_evidence,
                    parsed,
                    f"Metadata lookup was not completed: {lookup.fetch_status}.",
                    source_ids=[source_id],
                )
            )
            continue

        external_evidence, candidates = _candidates(reference_id, lookup, source_id)
        evidence.extend(external_evidence)
        outcome, selected_id, reason = _classify(parsed, candidates)
        item_source_ids = [source_id]
        if outcome == "unverified" and isinstance(_LOOKUP, CrossrefLookup) and lookup.snapshot_ref:
            resolution = resolve_versions(parsed, candidates, lookup.snapshot_ref, raw_text=entry.text)
            for number, observation in enumerate(resolution.observations, start=1):
                version_source_id = f"src_{reference_id}_version_{number}"
                snapshot_ref = derive_snapshot(observation.snapshot_ref, metadata={
                    "source_id": version_source_id, "source_url": observation.url,
                    "reference_id": reference_id,
                })
                sources.append(CredibilityExternalSource(
                    source_id=version_source_id, kind="metadata", url=observation.url,
                    fetch_status=observation.fetch_status, http_status=observation.http_status,
                    retrieved_at=observation.retrieved_at,
                    observed_at=read_snapshot(snapshot_ref).manifest["observed_at"],
                    is_cached=observation.is_cached, snapshot_ref=snapshot_ref,
                    association_reason=f"Official preprint version metadata for {reference_id}.",
                ))
                item_source_ids.append(version_source_id)
                proof_ids = [candidate_id for candidate_id, link in resolution.links.items()
                             if link.evidence_ref == observation.snapshot_ref]
                if proof_ids:
                    evidence_id = f"ev_{reference_id}_version_{number}"
                    link = resolution.links[proof_ids[0]]
                    version_evidence = CredibilityEvidence(
                        evidence_id=evidence_id, origin="external", mode="paraphrase",
                        excerpt=(f"{link.relationship}: arXiv {link.cited_arxiv_id}; "
                                 f"cited title {link.cited_title}; cited year {link.cited_year}; "
                                 f"publication DOI {link.candidate_doi}."),
                        paper_locator=None, source_id=version_source_id, location_confidence="high",
                    )
                    evidence.append(version_evidence)
                    external_evidence.append(version_evidence)
                    for candidate in candidates:
                        if candidate.candidate_id in proof_ids:
                            candidate.evidence_ids.append(evidence_id)
            if resolution.links:
                outcome, selected_id, reason = classify_reference(
                    parsed, candidates, raw_text=entry.text, version_links=resolution.links,
                )
            elif any(row.fetch_status != "ok" for row in resolution.observations):
                reason += " Supplementary preprint version lookup was incomplete."
        items.append(
            CredibilityReferenceItem(
                reference_id=reference_id,
                raw_text=entry.text,
                paper_evidence_ids=[item.evidence_id for item in paper_evidence],
                parsed=parsed,
                outcome=outcome,
                reason=reason,
                source_ids=item_source_ids,
                candidates=candidates,
                selected_candidate_id=selected_id,
                score_rule_id=None,
                item_score=None,
            )
        )
        if outcome == "unverified":
            findings.append(
                CredibilityFinding(
                    finding_id=f"finding_{reference_id}_unverified",
                    dimension_id="reference_verification",
                    item_ids=[reference_id],
                    kind="verification_gap",
                    severity="minor",
                    certainty="unresolved",
                    title="Reference metadata could not be confirmed",
                    detail=reason,
                    evidence_ids=[item.evidence_id for item in paper_evidence]
                    + [item.evidence_id for item in external_evidence],
                    source_ids=item_source_ids,
                    limitations=[
                        "A completed metadata query without a confident match is not proof that "
                        "the citation is fabricated."
                    ],
                )
            )

    result = _result(items, evidence, sources, findings, original_total, gaps)
    result.model_calls = model_calls
    return result


def _model_fields(ctx: CheckContext, entries, calls: list) -> list[dict | None] | None:
    """Model-split reference fields; None keeps the sentence rules for every entry."""
    if not get_settings().credibility_reference_field_parser_enabled or ctx.model_id in {None, "not_called"}:
        return None
    from app.credibility.reference_fields import parse_reference_fields

    return parse_reference_fields(ctx, ["\n".join(f.text for f in entry.fragments) for entry in entries], calls)


def _previous_failed_snapshot(previous, reference_id: str, raw_text: str) -> str | None:
    if previous is None:
        return None
    item = next((item for item in previous.checks.items if item.reference_id == reference_id), None)
    if item is None or item.raw_text != raw_text or item.outcome != "not_checked":
        return None
    for source in previous.sources:
        if source.source_id in item.source_ids and source.fetch_status != "ok" and source.snapshot_ref:
            try:
                verify_report_snapshots({"sources": [source.model_dump(mode="json")]})
            except (SnapshotError, OSError):
                continue
            return source.snapshot_ref
    return None


def _reusable_reference(previous, reference_id: str, raw_text: str, ctx: CheckContext):
    """Reuse only completed queries whose exact item and archived sources still agree."""
    if previous is None:
        return None
    item = next((item for item in previous.checks.items if item.reference_id == reference_id), None)
    if item is None or item.raw_text != raw_text or item.outcome == "not_checked":
        return None
    findings = [finding for finding in previous.findings if reference_id in finding.item_ids]
    evidence_ids = set(item.paper_evidence_ids)
    source_ids = set(item.source_ids)
    for candidate in item.candidates:
        evidence_ids.update(candidate.evidence_ids)
        source_ids.add(candidate.source_id)
    for finding in findings:
        evidence_ids.update(finding.evidence_ids)
        source_ids.update(finding.source_ids)
    evidence = [e for e in previous.evidence if e.evidence_id in evidence_ids]
    if {e.evidence_id for e in evidence} != evidence_ids:
        return None
    for entry in evidence:
        if entry.origin == "external":
            if not entry.source_id:
                return None
            source_ids.add(entry.source_id)
        else:
            locator = entry.paper_locator
            block = ctx.block(locator.chunk_id) if locator else None
            if block is None or entry.excerpt not in block.text:
                return None
    sources = [source for source in previous.sources if source.source_id in source_ids]
    if not sources or {source.source_id for source in sources} != source_ids:
        return None
    if any(source.fetch_status != "ok" for source in sources):
        return None
    try:
        verify_report_snapshots({"sources": [source.model_dump(mode="json") for source in sources]})
    except (SnapshotError, OSError):
        return None
    return item.model_copy(deep=True), [e.model_copy(deep=True) for e in evidence], [
        source.model_copy(deep=True) for source in sources
    ], [finding.model_copy(deep=True) for finding in findings]


def _extract_entries(ctx: CheckContext) -> tuple[list[_ReferenceEntry], int | None, list[str]]:
    """找并拆分参考文献。无法可靠拆分的文本只记缺口，不编造条目数。"""
    blocks = [block for block in ctx.blocks_with_role("references") if block.text.strip()]
    if not blocks and ctx.doc is not None:
        # section_role 可能错误；只把标题明显是 References/Bibliography 的块作为保守回退。
        blocks = [
            block
            for block in ctx.doc.blocks
            if _REFERENCE_TITLE_RE.search(block.section_title or "") and block.text.strip()
        ]
    if not blocks:
        return [], None, ["No blocks with section_role=references or a References/Bibliography title were available."]

    return extract_reference_entries(blocks)


def _paper_evidence(
    ctx: CheckContext, entry: _ReferenceEntry, reference_id: str
) -> tuple[list[CredibilityEvidence], list[str]]:
    evidence: list[CredibilityEvidence] = []
    gaps: list[str] = []
    for index, fragment in enumerate(entry.fragments, start=1):
        suffix = "" if len(entry.fragments) == 1 else f"_{index}"
        located, problems = locate_paper_span(
            ctx,
            PaperSpanCandidate(
                evidence_id=f"ev_{reference_id}_paper{suffix}",
                mode="quote", excerpt=fragment.text, chunk_id=fragment.chunk_id,
            ),
        )
        if located is None:
            gaps.append(
                f"{fragment.chunk_id}: cannot attach paper evidence for {reference_id}: {'; '.join(problems)}"
            )
        else:
            evidence.append(located.evidence)
    return evidence, gaps


def _parse_metadata(raw: str, model_fields: dict | None = None) -> CredibilityBibliographicMetadata:
    from app.credibility.reference_fields import merge_hints

    return CredibilityBibliographicMetadata(**merge_hints(metadata_hints(raw), model_fields))


def _crossref_candidates(payload: object, *, doi_lookup: bool) -> Iterable[CredibilityBibliographicMetadata]:
    if not isinstance(payload, dict):
        return []
    message = payload.get("message")
    rows = (
        [message]
        if doi_lookup and isinstance(message, dict)
        else message.get("items", [])
        if isinstance(message, dict)
        else []
    )
    candidates: list[CredibilityBibliographicMetadata] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        titles = row.get("title")
        title = titles[0] if isinstance(titles, list) and titles and isinstance(titles[0], str) else None
        authors = []
        for author in row.get("author", []) if isinstance(row.get("author"), list) else []:
            if isinstance(author, dict):
                name = " ".join(
                    piece for piece in (author.get("given"), author.get("family")) if isinstance(piece, str)
                )
                if name.strip():
                    authors.append(name.strip())
        published = row.get("issued") or row.get("published-print") or row.get("published-online")
        date_parts = published.get("date-parts") if isinstance(published, dict) else None
        year = (
            date_parts[0][0]
            if isinstance(date_parts, list)
            and date_parts
            and isinstance(date_parts[0], list)
            and date_parts[0]
            and isinstance(date_parts[0][0], int)
            else None
        )
        candidates.append(
            CredibilityBibliographicMetadata(
                title=title,
                authors=authors,
                year=year,
                doi=_clean_doi(row.get("DOI") if isinstance(row.get("DOI"), str) else None),
                arxiv_id=None,
            )
        )
    return candidates


def _external_source(source_id: str, lookup: _LookupResult, reference_id: str) -> CredibilityExternalSource:
    source_metadata = {"source_id": source_id, "source_url": lookup.url, "reference_id": reference_id}
    snapshot_ref = (
        derive_snapshot(lookup.snapshot_ref, metadata=source_metadata)
        if lookup.snapshot_ref else capture_snapshot(
            kind="crossref_response", requested_url=lookup.url, final_url=lookup.url,
            retrieved_at=lookup.retrieved_at, fetch_status=lookup.fetch_status,
            http_status=lookup.http_status, is_cached=lookup.is_cached,
            normalized_json={"candidates": [item.model_dump(mode="json") for item in lookup.candidates]}
            if lookup.fetch_status == "ok" else None,
            metadata={**source_metadata, "raw_available": False},
        )
    )
    return CredibilityExternalSource(
        source_id=source_id,
        kind="metadata",
        url=lookup.url,
        fetch_status=lookup.fetch_status,
        http_status=lookup.http_status,
        retrieved_at=lookup.retrieved_at,
        observed_at=read_snapshot(snapshot_ref).manifest["observed_at"],
        is_cached=lookup.is_cached,
        snapshot_ref=snapshot_ref,
        association_reason=f"Public metadata lookup for {reference_id}.",
    )


def _candidates(
    reference_id: str, lookup: _LookupResult, source_id: str
) -> tuple[list[CredibilityEvidence], list[CredibilityReferenceCandidate]]:
    evidence: list[CredibilityEvidence] = []
    candidates: list[CredibilityReferenceCandidate] = []
    for index, metadata in enumerate(lookup.candidates, start=1):
        evidence_id = f"ev_{reference_id}_metadata_{index}"
        excerpt = "; ".join(
            part
            for part in (
                metadata.title,
                ", ".join(metadata.authors),
                str(metadata.year) if metadata.year else None,
                metadata.doi,
            )
            if part
        )
        evidence.append(
            CredibilityEvidence(
                evidence_id=evidence_id,
                origin="external",
                mode="paraphrase",
                excerpt=excerpt or "Crossref returned an empty metadata candidate.",
                paper_locator=None,
                source_id=source_id,
                location_confidence="high",
            )
        )
        candidates.append(
            CredibilityReferenceCandidate(
                candidate_id=f"cand_{reference_id}_{index}",
                metadata=metadata,
                source_id=source_id,
                evidence_ids=[evidence_id],
                match_basis="Candidate returned by the public metadata query; matching is evaluated separately.",
            )
        )
    return evidence, candidates


def _classify(
    parsed: CredibilityBibliographicMetadata, candidates: list[CredibilityReferenceCandidate]
) -> tuple[str, str | None, str]:
    return classify_reference(parsed, candidates)


def _title_overlap(left: str, right: str) -> float:
    a, b = set(_WORD_RE.findall(left.casefold())), set(_WORD_RE.findall(right.casefold()))
    return len(a & b) / len(a | b) if a and b else 0.0


def _not_checked_item(
    reference_id: str,
    raw: str,
    evidence: list[CredibilityEvidence],
    parsed: CredibilityBibliographicMetadata,
    reason: str,
    *,
    source_ids: list[str] | None = None,
) -> CredibilityReferenceItem:
    return CredibilityReferenceItem(
        reference_id=reference_id,
        raw_text=raw,
        paper_evidence_ids=[item.evidence_id for item in evidence],
        parsed=parsed,
        outcome="not_checked",
        reason=reason,
        source_ids=source_ids or [],
        candidates=[],
        selected_candidate_id=None,
        score_rule_id=None,
        item_score=None,
    )


def _result(
    items: list[CredibilityReferenceItem],
    evidence: list[CredibilityEvidence],
    sources: list[CredibilityExternalSource],
    findings: list[CredibilityFinding],
    original_total: int | None,
    gaps: list[str],
) -> ReferenceCheckResult:
    matched = sum(item.outcome == "matched" for item in items)
    conflicts = sum(item.outcome == "metadata_conflict" for item in items)
    unverified = sum(item.outcome == "unverified" for item in items)
    not_checked = sum(item.outcome == "not_checked" for item in items)
    checked = matched + conflicts + unverified
    extraction_status = "complete" if not gaps else "partial"
    failed_source_ids = {source.source_id for source in sources if source.fetch_status != "ok"}
    unresolved_source_failure = any(
        item.outcome != "matched" and failed_source_ids.intersection(item.source_ids) for item in items
    )
    check_complete = (extraction_status == "complete" and not_checked == 0
                      and not unresolved_source_failure)
    return ReferenceCheckResult(
        dimension=CredibilityDimension(
            dimension_id="reference_verification",
            applicability="applicable",
            applicability_reason=(
                "The parsed paper contains reference entries that can be checked against public metadata."
            ),
            check_status="completed" if check_complete else "partial",
            score_status="unable_to_assess",
            score=None,
            weight=1,
            score_rule_ids=[],
            summary=f"Checked {checked} of {len(items)} extracted reference entries.",
            unassessed_reason="Reference scoring remains unavailable until the draft Rubric is approved.",
            evidence_ids=[item.evidence_id for item in evidence],
            finding_ids=[item.finding_id for item in findings],
        ),
        checks=CredibilityReferenceChecks(
            extraction_status=extraction_status,
            extraction_reason=(
                "Reference entries were deterministically extracted from parsed reference blocks; "
                "gaps are listed separately."
            ),
            original_total=original_total,
            extracted_count=len(items),
            checked_count=checked,
            matched_count=matched,
            conflict_count=conflicts,
            unverified_count=unverified,
            not_checked_count=not_checked,
            query_completion_ratio=checked / len(items) if items else None,
            extraction_coverage_ratio=len(items) / original_total if original_total else None,
            gaps=gaps,
            items=items,
        ),
        evidence=evidence,
        sources=sources,
        findings=findings,
    )


def _empty_failed(reason: str, gaps: list[str], *, check_status: str = "failed") -> ReferenceCheckResult:
    return ReferenceCheckResult(
        dimension=CredibilityDimension(
            dimension_id="reference_verification",
            applicability="undetermined",
            applicability_reason=reason,
            check_status=check_status,
            score_status="unable_to_assess",
            score=None,
            weight=1,
            score_rule_ids=[],
            summary="Reference verification could not start because reliable reference entries were unavailable.",
            unassessed_reason=reason,
            evidence_ids=[],
            finding_ids=[],
        ),
        checks=CredibilityReferenceChecks(
            extraction_status="failed",
            extraction_reason=reason,
            original_total=None,
            extracted_count=0,
            checked_count=0,
            matched_count=0,
            conflict_count=0,
            unverified_count=0,
            not_checked_count=0,
            query_completion_ratio=None,
            extraction_coverage_ratio=None,
            gaps=gaps,
            items=[],
        ),
    )
