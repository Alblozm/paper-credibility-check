"""Independent, bounded check branches and durable successful checkpoints."""
from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from app.config import get_settings
from app.credibility import live
from app.credibility.models import ClaimCheckResult, CodeCheckResult, ReferenceCheckResult
from app.credibility.runtime import StageExecution, digest, encode_context

logger = logging.getLogger(__name__)


STOPPED_BY_USER = "STOPPED_BY_USER"


def failed_check(name, ctx, exc, message=None):
    message = message or (f"{name} could not complete ({type(exc).__name__}); "
                          "no positive or negative finding was inferred.")
    if name == "claim_check":
        from app.credibility.claim_model import PROMPT_VERSION
        from app.credibility.claims import _not_run
        from app.credibility.models import CredibilityModelCall

        result = _not_run(message, message)
        result.model_calls = [CredibilityModelCall.model_validate(call) for call in ctx.model_history]
        result.prompt_version = PROMPT_VERSION if result.model_calls else "not_run"
    elif name == "reference_check":
        from app.credibility.references import _empty_failed

        result = _empty_failed(message, [message])
    else:
        from app.credibility.code_checks import check_code

        result = check_code(replace(ctx, doc=None))
        result.checks.scope_limitations.append(message)
    result.dimension.check_status = "failed"
    result.dimension.summary = message
    result.dimension.unassessed_reason = message
    return result


def check_code_with_model(ctx):
    from app.credibility.code_checks import (
        check_code,
        complete_confirmed_repositories,
        intake_discovered_repositories,
        source_read_cap,
    )
    from app.credibility.code_discovery import discover_code, finalize_discovery

    previous = CodeCheckResult.model_validate(ctx.previous_check) if ctx.previous_check else None
    reuse_facts = (
        previous is not None and bool(previous.sources)
        and previous.dimension.check_status not in {"failed", "not_run", "running"}
        and all(s.fetch_status == "ok" for s in previous.sources)
    )
    result = previous.model_copy(deep=True) if reuse_facts else check_code(ctx)
    previous_audit_ref = (result.external_review or {}).get("audit_ref") if reuse_facts else None
    # A fresh check already carries its paper-link labelling call; a reused one its earlier calls.
    historical_calls = list(result.model_calls)
    if reuse_facts:
        from app.credibility.code_lookup import LOOKUP
        from app.credibility.snapshots import verify_report_snapshots

        verify_report_snapshots({"sources": [source.model_dump(mode="json") for source in result.sources],
                                 "code_discovery": result.discovery})
        for source in result.sources:
            if source.snapshot_ref:
                LOOKUP.restore_public_repository_proof(source.snapshot_ref)
        # The request cap applies to this execution. Archived source reads do
        # not spend a new HTTP budget, and continuation state stays intact.
        result.external_request_count = 0
        result.model_calls = []
        result.prompt_version = "not_run"
        result.external_review = None
    settings = get_settings()
    if not settings.credibility_code_model_enabled or ctx.model_id in (None, "not_called"):
        result.discovery.status = "disabled"
        result.discovery.reason = "Model-assisted code checking is disabled; external discovery was not run."
        return result
    from app.credibility.code_model import (
        _discussion_needs_review,
        _unreviewed_result_feedback_warning,
        enrich_code_checks,
    )
    from app.credibility.snapshots import capture_snapshot
    from app.pipeline.orchestrator import build_llm_client

    passes, calls, warnings = [], historical_calls, []
    attempted_discussions: set[str] = set()
    discovery_audit = {}
    model_remaining = settings.credibility_code_max_rounds
    tools_remaining = settings.credibility_code_max_tool_calls
    review_status, prompt_version = "not_needed", "not_run"
    had_partial_review = False
    followup = {"new_discussion_ids": [], "executed": False}

    def review_all_discussions():
        """Later batches cover discussions the first passes left; each target is attempted once."""
        nonlocal review_status
        attempted = attempted_discussions
        cap = settings.credibility_code_max_reviewed_discussions
        batches = 0
        while True:
            rejected = {repo.repository_id for repo in result.checks.repositories if repo.association == "rejected"}
            pending = [item.discussion_id for item in result.checks.discussions
                       if item.discussion_id not in attempted and item.repository_id not in rejected
                       and _discussion_needs_review(item)]
            room = cap - len(attempted)
            # An exhausted shared budget keeps its recorded gap; batches only extend coverage.
            if not pending or room <= 0 or model_remaining <= 0:
                break
            before, size = review_status, len(attempted)
            review(repository_ids=[], discussion_ids=pending,
                              batch_rounds=settings.credibility_code_discussion_batch_rounds,
                              max_discussions=min(settings.credibility_code_discussion_batch_size, room))
            if before in {"partial", "failed"} and review_status in {"completed", "not_needed"}:
                review_status = before  # a later success cannot erase an earlier incomplete pass
            if len(attempted) == size:
                break
            batches += 1
        followup["discussion_batches"] = batches
        # Earlier passes reported their own leftovers; restate coverage once for the whole run.
        stale = ("Semantic discussion item budget reached", "Possible result-level feedback")
        result.checks.scope_limitations = [v for v in result.checks.scope_limitations if not v.startswith(stale)]
        warnings[:] = [v for v in warnings if not v.startswith(stale)]
        rejected = {repo.repository_id for repo in result.checks.repositories if repo.association == "rejected"}
        left = [item.discussion_id for item in result.checks.discussions
                if item.discussion_id not in attempted and item.repository_id not in rejected
                and _discussion_needs_review(item)]
        if left:
            message = (f"Semantic discussion review attempted {len(attempted)} threads (cap {cap}); "
                       f"{len(left)} keep rule labels only.")
            result.checks.scope_limitations.append(message)
            warnings.append(message)
            if flagged := _unreviewed_result_feedback_warning(left, result):
                result.checks.scope_limitations.append(flagged)
                warnings.append(flagged)

    def mark_gap(message):
        nonlocal review_status
        review_status = "partial"
        result.dimension.check_status = "partial"
        if message not in result.checks.scope_limitations:
            result.checks.scope_limitations.append(message)
        if message not in warnings:
            warnings.append(message)

    def review(*, discussion_ids=None, repository_ids=None, batch_rounds=None, max_discussions=None):
        """batch_rounds: a later discussion batch with its own round allowance (shared budget untouched)."""
        nonlocal result, model_remaining, tools_remaining, review_status, prompt_version, had_partial_review
        remaining_requests = max(0, source_read_cap() - result.external_request_count)
        options = {}
        if repository_ids is not None:
            options["target_repository_ids"] = repository_ids
        if discussion_ids is not None:
            options["target_discussion_ids"] = discussion_ids
        reviewed = enrich_code_checks(
            ctx, result, client, mode=settings.credibility_code_tool_mode,
            max_rounds=batch_rounds or model_remaining, max_tool_calls=min(tools_remaining, remaining_requests),
            max_characters=settings.credibility_code_max_characters,
            max_output_tokens=settings.credibility_code_max_output_tokens,
            max_discussions=max_discussions or settings.credibility_code_discussion_batch_size, **options,
        )
        result = reviewed.checks
        passes.append(reviewed.audit)
        attempted_discussions.update(reviewed.audit.get("target_discussion_ids", discussion_ids or []))
        calls.extend(reviewed.model_calls)
        warnings.extend(reviewed.warnings)
        prompt_version, review_status = reviewed.prompt_version, reviewed.status
        had_partial_review |= reviewed.status == "partial"
        attempts = reviewed.audit.get("attempt_count", len(reviewed.model_calls))
        if batch_rounds is None:
            model_remaining = max(0, model_remaining - attempts)
        tools_remaining = max(0, tools_remaining - reviewed.audit.get("tool_call_count", 0))
        if reviewed.status in {"failed", "unsupported", "partial"}:
            result.dimension.check_status = "partial"
        return reviewed

    client = None
    try:
        from app.credibility.code_discovery_provider import discovery_provider_for

        client = build_llm_client(ctx.model_id)
        if reuse_facts and result.discovery.audit_ref:
            # Saved discovery observations are evidence, not permission to run
            # paid searches again during a continuation of the same facts.
            discovery_audit = {"reused_audit_ref": result.discovery.audit_ref}
            if result.discovery.status in {"failed", "partial"}:
                had_partial_review = True
                mark_gap(result.discovery.reason)
        elif not settings.credibility_code_discovery_enabled:
            result.discovery.status = "disabled"
            result.discovery.reason = "External code discovery is disabled by configuration."
        elif result.checks.code_availability in {"available", "not_applicable"}:
            result.discovery.status = "not_needed"
            result.discovery.reason = "Existing source-bound implementation/non-applicability evidence is sufficient."
        elif ctx.doc is None or not ctx.doc.blocks:
            result.discovery.reason = "No usable parsed paper is available for an identity-bound search."
        elif result.external_request_count >= source_read_cap():
            result.discovery.status = "partial"
            result.discovery.reason = "Shared source-read budget exhausted before external code discovery."
            had_partial_review = True
            mark_gap(result.discovery.reason)
        elif not getattr(client, "supports_native_tools", False) or (provider := discovery_provider_for(
            client, max_requests=min(
                settings.credibility_code_discovery_max_queries + settings.credibility_code_discovery_max_fetches,
                max(0, source_read_cap() - result.external_request_count),
            ), deadline_seconds=600,
        )) is None:
            result.discovery.status = "disabled"
            result.discovery.reason = "Search credentials or native model tools are unavailable for the selected model."
        else:
            discovered = discover_code(ctx, result, client, provider,
                max_rounds=settings.credibility_code_discovery_max_rounds,
                max_queries=settings.credibility_code_discovery_max_queries,
                max_fetches=settings.credibility_code_discovery_max_fetches,
                max_calls=settings.credibility_code_discovery_max_tool_calls,
                max_characters=settings.credibility_code_max_characters,
                max_output_tokens=settings.credibility_code_max_output_tokens)
            result, discovery_audit = discovered.result, discovered.audit
            calls.extend(discovered.model_calls)
            result = intake_discovered_repositories(ctx, result, discovered.intake_candidates,
                                                    max_requests=source_read_cap())
            if result.discovery.status in {"failed", "partial"}:
                had_partial_review = True
                mark_gap(result.discovery.reason)
        # A cited dependency's long README must not consume the discussion
        # review's context before ownership is decided. Defer those existing
        # discussions only when association work and discussion work coexist;
        # keep the established single-pass path for already-confirmed repos.
        separate_association = any(
            repo.association == "uncertain" for repo in result.checks.repositories
        ) and bool(result.checks.discussions)
        # A resumed attempt may already have rejected dependencies; their old
        # discussions must not re-enter the model queue after association work.
        rejected = {repo.repository_id for repo in result.checks.repositories if repo.association == "rejected"}
        initial_discussions = None
        if separate_association:
            initial_discussions = []
        elif rejected:
            initial_discussions = [
                item.discussion_id for item in result.checks.discussions if item.repository_id not in rejected
            ]
        first = review(discussion_ids=initial_discussions)
        accepted_partial = first.status == "partial" and bool(first.audit.get("output_snapshot_ref"))
        if first.status in {"completed", "not_needed"} or accepted_partial:
            old_discussions = {item.discussion_id for item in result.checks.discussions}
            result = complete_confirmed_repositories(ctx, result, max_requests=source_read_cap())
            followup["executed"] = True
            added = sorted({item.discussion_id for item in result.checks.discussions} - old_discussions)
            followup["new_discussion_ids"] = added
            confirmed = {
                repo.repository_id for repo in result.checks.repositories if repo.association == "confirmed"
            }
            deferred = sorted(
                item.discussion_id for item in result.checks.discussions
                if separate_association and item.discussion_id in old_discussions
                and item.repository_id in confirmed
            )
            if separate_association:
                followup["deferred_discussion_ids"] = deferred
            active_discussions = sorted(set(added + deferred))
            if active_discussions:
                if model_remaining <= 0:
                    mark_gap(
                        "Model-round budget exhausted before interpreting pending confirmed-repository discussions."
                    )
                else:
                    review(repository_ids=[], discussion_ids=active_discussions)
            if result.dimension.check_status == "partial" and review_status in {"completed", "not_needed"}:
                review_status = "partial"
        review_all_discussions()
    except Exception as exc:
        logger.warning("external model review failed: %s", type(exc).__name__)
        result.dimension.check_status = "partial"
        result.checks.scope_limitations.append(f"External model review failed ({type(exc).__name__}).")
        review_status = "failed"
        warnings.append(f"External review or continuation failed ({type(exc).__name__}).")
        followup["error_code"] = type(exc).__name__
    # External reports are a separate snippet-only lead search. Available official
    # code and a failed discussion pass must not suppress this bounded follow-up.
    if settings.credibility_code_discovery_enabled and settings.credibility_code_reproduction_search_enabled:
        from app.credibility.code_reproduction_discovery import search_reproduction_reports

        try:
            remaining = max(0, source_read_cap() - result.external_request_count)
            provider = (discovery_provider_for(client, max_requests=min(2, remaining), deadline_seconds=60)
                        if getattr(client, "supports_native_tools", False) else None)
            reports = search_reproduction_reports(
                ctx, result, client, provider, max_source_reads=source_read_cap(),
            )
            result = reports.result
            calls.extend(reports.model_calls)
            warnings.extend(reports.warnings)
            discovery_audit["reproduction_reports"] = reports.audit
            for key in ("rounds", "attempts", "attempt_refs", "tool_calls"):
                discovery_audit.setdefault(key, []).extend(reports.audit.get(key, []))
            discovery_audit["attempt_count"] = (
                discovery_audit.get("attempt_count", 0) + reports.audit.get("attempt_count", 0)
            )
            followup["reproduction_reports"] = {
                "status": reports.status,
                "reused": bool(reports.audit.get("reused_audit_ref")),
            }
            if reports.status in {"partial", "failed"}:
                had_partial_review = True
                result.dimension.check_status = "partial"
        except Exception as exc:
            # Keep all earlier source-bound results if this optional step fails.
            had_partial_review = True
            message = f"External reproduction report search failed ({type(exc).__name__})."
            result.checks.scope_limitations.append(message)
            warnings.append(message)
            followup["reproduction_reports"] = {"status": "failed", "error_code": type(exc).__name__}
    # Accepted positive judgments can drive safe deterministic collection even
    # when another tool read failed. Successful continuation cannot erase that
    # recorded partial semantic pass.
    if had_partial_review:
        result.dimension.check_status = "partial"
        if review_status in {"completed", "not_needed"}:
            review_status = "partial"
    result = finalize_discovery(result)
    result.model_calls = calls
    result.prompt_version = prompt_version
    audit = {
        "mode": settings.credibility_code_tool_mode, "status": review_status,
        "passes": passes, "followup": followup, "previous_attempt_audit_snapshot_ref": previous_audit_ref,
        "warnings": list(dict.fromkeys(warnings)),
        "attempt_count": sum(item.get("attempt_count", 0) for item in passes),
        "model_call_count": len(calls), "external_request_count": result.external_request_count,
        "remaining_model_rounds": model_remaining, "remaining_tool_calls": tools_remaining,
        "discovery": discovery_audit,
        "discovery_audit_ref": result.discovery.audit_ref,
        "max_combined_model_rounds": settings.credibility_code_discovery_max_rounds
            + settings.credibility_code_max_rounds
            + followup.get("discussion_batches", 0) * settings.credibility_code_discussion_batch_rounds
            + int(settings.credibility_code_reproduction_search_enabled),
        "max_combined_tool_calls": settings.credibility_code_discovery_max_tool_calls
            + settings.credibility_code_max_tool_calls
            + 2 * int(settings.credibility_code_reproduction_search_enabled),
    }
    for key in ("rounds", "attempts", "attempt_refs", "tool_calls"):
        audit[key] = [row for item in passes for row in item.get(key, [])]
        audit[key] = list(discovery_audit.get(key, [])) + audit[key]
    audit["attempt_count"] += discovery_audit.get("attempt_count", 0)
    audit["tool_call_count"] = len(audit["tool_calls"])
    audit_ref = capture_snapshot(kind="external_review_audit", normalized_json=audit, metadata={
        "assessment_id": ctx.assessment_id, "prompt_version": prompt_version,
    })
    result.external_review = {
        "mode": settings.credibility_code_tool_mode, "status": review_status,
        "audit_ref": audit_ref, "warnings": audit["warnings"], "base_facts_reused": reuse_facts,
    }
    return result



def _result_state(result) -> str:
    """Execution success follows the producer status, never the score status."""
    dimension = result.dimension
    if dimension.check_status == "failed":
        return "failed"
    if dimension.check_status in {"running", "partial"}:
        return "partial"
    if dimension.check_status == "not_run" and dimension.applicability != "not_applicable":
        return "partial"
    # Reference producers already distinguish unresolved failures from failed
    # attempts recovered by evidence from another provider. Keep those attempts
    # in the audit without reopening a completed reference check on every resume.
    failed_sources = not isinstance(result, ReferenceCheckResult) and any(
        source.fetch_status != "ok" for source in getattr(result, "sources", [])
    )
    review_failed = isinstance(result, CodeCheckResult) and (
        result.discovery.status in {"failed", "partial"}
        or (result.external_review is not None
            and result.external_review.get("status") in {"failed", "unsupported", "partial"})
    )
    return "partial" if failed_sources or review_failed else "completed"


def stopped_check(name, ctx):
    """The result of a check the reader stopped: failed with a fallback score, saying so plainly."""
    message = (f"{name} was stopped by the user before it finished; "
               "no positive or negative finding was inferred.")
    # Outside the check's live scope, so building the fallback cannot trip the stop flag again.
    with live.scope(None, name):
        result = failed_check(name, ctx, None, message)
    return result, {"status": "failed", "reused": False, "stopped_by_user": True,
                    "error": {"code": STOPPED_BY_USER, "retryable": False}}


def run_checks(ctx, checks, execution: StageExecution | None = None):
    settings = get_settings()
    lock = threading.Lock()
    events = {}
    context_identity = digest(encode_context(ctx))
    specifications = [
        ("claim_check", checks.claims, ClaimCheckResult),
        ("reference_check", checks.references, ReferenceCheckResult),
        ("code_check", checks.code, CodeCheckResult),
    ]

    def run(spec):
        name = spec[0]
        with live.scope(getattr(ctx, "assessment_id", None), name):
            try:
                live.note("started")
            except live.CheckStopped:
                pass  # stopped before it began; run_check records it without running
            return run_check(spec)

    def run_check(spec):
        name, fn, model = spec
        start = time.monotonic()
        branch = copy.copy(ctx)
        branch.doc = copy.deepcopy(ctx.doc)
        branch.model_history = []
        fingerprint = digest({"request": execution.fingerprint if execution else None,
                              "context": context_identity, "stage": name})
        cached = execution.load(name, fingerprint) if execution else None
        reusable = False
        if cached is not None:
            result = model.model_validate(cached["result"])
            from app.credibility.snapshots import verify_report_snapshots

            verify_report_snapshots({"sources": [s.model_dump(mode="json") for s in getattr(result, "sources", [])],
                                     "code_discovery": getattr(result, "discovery", None)})
            # Older workers could mark a failed/never-run producer completed.
            # Revalidate the actual result before trusting that checkpoint flag.
            reusable = _result_state(result) == "completed"
            if not reusable:
                # Normal writes preserve genuine completed checkpoints. Remove
                # only this validated legacy misclassification before replacing
                # it with the next attempt's actual failed/partial state.
                execution.store.discard_stage(execution.assessment_id, execution.lease, name, fingerprint)
        stopped_earlier = execution is not None and execution.stopped_by_user(name, fingerprint)
        if reusable:
            event = {"status": "completed", "reused": True}
        elif stopped_earlier:
            # A check the reader stopped stays stopped when the task resumes after a restart.
            result, event = stopped_check(name, branch)
        elif live.stop_requested(getattr(ctx, "assessment_id", None) or "", name):
            result, event = stopped_check(name, branch)
            if execution:
                execution.record(name, fingerprint, {"error": event["error"]}, status="failed",
                                 note=STOPPED_BY_USER)
        else:
            try:
                partial = cached or (execution.load_partial(name, fingerprint) if execution else None)
                if partial and partial.get("result"):
                    from app.credibility.snapshots import verify_report_snapshots

                    previous = model.model_validate(partial["result"])
                    verify_report_snapshots({"sources": [
                        s.model_dump(mode="json") for s in getattr(previous, "sources", [])
                    ], "code_discovery": getattr(previous, "discovery", None)})
                    branch.previous_check = partial["result"]
                result = model.model_validate(fn(branch))
                state = _result_state(result)
                if execution:
                    execution.record(name, fingerprint, {"result": result.model_dump(mode="json")}, status=state)
                event = {"status": state, "reused": False}
            except live.CheckStopped:
                logger.info("%s stopped by the user", name)
                result, event = stopped_check(name, branch)
                if execution:
                    execution.record(name, fingerprint, {"error": event["error"]}, status="failed",
                                     note=STOPPED_BY_USER)
            except Exception as exc:
                # A stale lease is an ownership failure, not a paper-check result.
                from app.storage.credibility_tasks import LeaseConflict

                if isinstance(exc, LeaseConflict):
                    raise
                logger.warning("%s failed: %s", name, type(exc).__name__)
                result = failed_check(name, branch, exc)
                error = {"code": getattr(getattr(exc, "error_code", None), "value", type(exc).__name__),
                         "retryable": getattr(exc, "retryable", False)}
                if execution:
                    execution.record(name, fingerprint, {"error": error}, status="failed", note=type(exc).__name__)
                event = {"status": "failed", "reused": False, "error": error}
        event["elapsed_seconds"] = round(time.monotonic() - start, 4)
        with lock:
            events[name] = event
        return result

    workers = max(1, min(3, settings.credibility_workers))
    if workers == 1:
        results = [run(spec) for spec in specifications]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="credibility") as pool:
            results = list(pool.map(run, specifications))
    if execution:
        execution.events.update(events)
    return results, {"workers": workers, "checks": {name: events[name] for name, _, _ in specifications}}
