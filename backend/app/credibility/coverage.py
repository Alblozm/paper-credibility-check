"""Explain check coverage and failures independently of trial scores."""
from collections import Counter
from typing import get_args

from app.credibility.models import (
    CredibilityCoverage,
    CredibilityReferenceChecks,
    CredibilityReferenceVerificationSummary,
    CredibilityUnresolvedReference,
    ReferenceUnresolvedReasonCode,
)

_REFERENCE_REASON_CODES = frozenset(get_args(ReferenceUnresolvedReasonCode))


def build_reference_summary(checks: CredibilityReferenceChecks) -> CredibilityReferenceVerificationSummary:
    """Expose observed verification separately from the frozen scoring rules.

    Reasons are deterministically encoded by the reference producer. Legacy
    free-text reasons remain visible without guessing a more specific cause.
    """
    unresolved = []
    for item in checks.items:
        if item.outcome == "matched":
            continue
        prefix, separator, _ = item.reason.partition("]")
        encoded = prefix[1:] if separator and prefix.startswith("[") else ""
        reason_code = encoded if encoded in _REFERENCE_REASON_CODES else {
            "metadata_conflict": "metadata_conflict",
            "not_checked": "not_checked",
        }.get(item.outcome, "undetermined_reason")
        unresolved.append(CredibilityUnresolvedReference(
            reference_id=item.reference_id, outcome=item.outcome,
            reason_code=reason_code, reason=item.reason, source_ids=list(item.source_ids),
        ))
    return CredibilityReferenceVerificationSummary(
        verification_rate=checks.matched_count / checks.extracted_count if checks.extracted_count else None,
        extracted_count=checks.extracted_count, matched_count=checks.matched_count,
        unverified_count=checks.unverified_count, not_checked_count=checks.not_checked_count,
        metadata_conflict_count=checks.conflict_count,
        extraction_complete=checks.extraction_status == "complete" and not checks.gaps,
        unresolved_items=unresolved,
    )


def explain_coverage(ctx, claim, references, code) -> list[CredibilityCoverage]:
    results = (claim, references, code)
    output = []
    for index, result in enumerate(results):
        dim = result.dimension
        failures = sum(source.fetch_status != "ok" for source in getattr(result, "sources", []))
        if dim.check_status == "failed":
            failures = max(1, failures)
        confirmed = sum(f.kind == "concern" and f.certainty == "confirmed" for f in result.findings)
        if index == 0:
            counts = Counter(item.outcome for item in result.checks.items)
            item_count = len(result.checks.items)
            total = None if dim.check_status == "failed" else item_count
            inspected = item_count - counts["not_checked"]
            unresolved = counts["unable_to_determine"] + counts["not_checked"]
            complete = result.checks.scope_complete and ctx.doc is not None and ctx.doc.parse_quality.value == "good"
            population, limitations = "selected_claims", list(result.checks.scope_limitations)
            ratio, extraction = inspected / total if total else None, None
        elif index == 1:
            checks = result.checks
            counts = Counter(item.outcome for item in checks.items)
            total, inspected = checks.extracted_count, checks.checked_count
            unresolved = checks.unverified_count + checks.not_checked_count
            complete = checks.extraction_status == "complete" and not checks.gaps and not unresolved
            population, limitations = "extracted_references", list(checks.gaps)
            ratio, extraction = checks.query_completion_ratio, checks.extraction_coverage_ratio
        else:
            checks = result.checks
            counts = Counter(repo.association for repo in checks.repositories)
            total, inspected = None, len(checks.repositories)
            unresolved = sum(repo.association == "uncertain" for repo in checks.repositories)
            unresolved += sum(d.relevance == "undetermined" for d in checks.discussions)
            complete = checks.scope_complete or dim.applicability == "not_applicable"
            population, limitations = "bounded_repository_search", list(checks.scope_limitations)
            ratio, extraction = None, None
            if result.external_review and result.external_review.get("status") == "failed":
                failures += 1
        output.append(CredibilityCoverage(
            dimension_id=dim.dimension_id, check_status=dim.check_status,
            scope_complete=complete, population=population, inspected_count=inspected,
            total_count=total, completion_ratio=ratio, extraction_coverage_ratio=extraction,
            outcomes=dict(counts), confirmed_concern_count=confirmed,
            unresolved_count=unresolved, technical_failure_count=failures,
            fallback_rule_ids=[rule for rule in dim.score_rule_ids if any(
                token in rule for token in ("FALLBACK", "UNVERIFIABLE", "UNVERIFIED")
            )], limitations=limitations,
        ))
    return output
