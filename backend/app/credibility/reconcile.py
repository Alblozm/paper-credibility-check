"""Resolve narrow code-release claims against already verified repository evidence.

This does not infer data/checkpoint availability, licensing, reproducibility or
experimental results from the existence of an implementation. No network/model
calls are made, and the original judgment and findings remain in the audit field.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlsplit

from app.credibility.models import (
    ClaimCheckResult,
    CodeCheckResult,
    CredibilityClaimItem,
    CredibilityClaimReconciliation,
    CredibilityFinding,
    CredibilityRepository,
)

SUPPORTED_BY_CODE_RULE = "P_C_SUPPORTED_BY_CODE_CHECK"
_ELIGIBLE_OUTCOMES = {"unable_to_determine", "not_found", "partially_supported"}
_URL = re.compile(r"https?://[^\s<>]+", re.I)
_CODE = re.compile(r"\b(?:code|implementations?)\b", re.I)
_UNVERIFIED_ARTIFACT = re.compile(r"\b(?:data(?:sets?)?|checkpoints?|weights?|models?)\b", re.I)
# Entire publication assertions are recognized, not arbitrary statements that
# happen to mention both code and GitHub. In particular, qualifiers such as
# 'complete', a license name or an additional empirical claim do not match.
_PREFIX = r"(?:(?:the|this) paper (?:states|reports|says) that\s+)?"
_ARTIFACT = (
    r"(?:(?:our|the|its|their)\s+)?(?:source\s+code|model\s+code|code|implementations?"
    r"|(?:pre[- ]?trained\s+)?(?:checkpoints?|weights?|models?)|data(?:sets?)?)"
)
_ARTIFACTS = rf"{_ARTIFACT}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s+as well as\s+){_ARTIFACT})*"
_CONTEXT = r"(?:\s+(?:for|of)\s+(?:this|our|the|its|their)\s+(?:paper|study|work|method|approach|model))?"
_LOCATION = (
    r"(?:\s+(?:at|on|via|in)\s+(?:(?:the|a|our|its|their|stated|provided|public)\s+)*"
    r"(?:url|github(?:\s+(?:url|repository))?|repository)(?:\s*:?\s+url)?)?"
)
_RELEASE = (
    r"(?:(?:made\s+)?publicly\s+available|available(?:\s+online)?|open[- ]sourc(?:e|ed)"
    r"|released(?:\s+publicly)?|made\s+public|public)"
)
_RELEASE_SENTENCE = re.compile(
    rf"{_PREFIX}(?:"
    rf"{_ARTIFACTS}{_CONTEXT}\s+(?:(?:is|are|was|were|has been|have been)\s+)?{_RELEASE}{_LOCATION}"
    rf"|(?:we|(?:the|this)\s+paper|(?:the\s+)?authors)\s+"
    rf"(?:release[sd]?|open[- ]source[sd]?|publish(?:es|ed)?|provide[sd]?)\s+"
    rf"{_ARTIFACTS}{_CONTEXT}(?:\s+(?:as\s+)?open[- ]source|\s+publicly)?{_LOCATION}"
    rf"|(?:we|(?:the|this)\s+paper|(?:the\s+)?authors)\s+make[sd]?\s+"
    rf"{_ARTIFACTS}{_CONTEXT}\s+(?:publicly\s+available|available|public){_LOCATION}"
    rf"|{_ARTIFACTS}\s*:\s*url"
    rf")[.!;\s]*",
    re.I,
)
_NOT_CURRENT = re.compile(
    r"\b(?:not|never|no|will|would|shall|should|may|might|could|if|unless|once|after|upon"
    r"|plan(?:s|ned)?|intend(?:s|ed)?|future|soon|previously|formerly)\b|n['’]t\b",
    re.I,
)


def reconcile_artifact_claims(
    claim: ClaimCheckResult, code: CodeCheckResult, *, classify: Callable[[list[dict]], dict | None] | None = None,
) -> ClaimCheckResult:
    """Return a copy with release claims reconciled once against verified repositories.

    ``classify`` labels what each candidate claim asserts (normally a model call,
    see app.credibility.release_classifier). Without it, or when it returns None,
    the fixed sentence rules below decide. Repository association and URL matching
    are always deterministic. Repository association and implementation evidence
    may be paper quotes, external quotes or validated paraphrases; reconciliation
    preserves their existing evidence objects and links to them in the report.
    """
    result = claim.model_copy(deep=True)
    eligible = _verified_repositories(code)
    if not eligible:
        return result
    evidence = {row.evidence_id: row for row in result.evidence}
    source_by_id = {row.source_id: row for row in code.sources}
    candidates: list[tuple[CredibilityClaimItem, list[str]]] = []
    for item in result.checks.items:
        if item.outcome not in _ELIGIBLE_OUTCOMES or item.reconciled_from is not None:
            continue
        # A valid abstract/introduction quote remains necessary even though the
        # later comparison can now use external repository evidence.
        quotes = [evidence[eid].excerpt for eid in item.claim_evidence_ids if eid in evidence]
        if not quotes or any(eid not in evidence for eid in item.claim_evidence_ids):
            continue
        if any(evidence[eid].origin != "paper" for eid in item.claim_evidence_ids):
            continue
        candidates.append((item, quotes))
    labels = None
    if classify is not None:
        from app.credibility.release_classifier import candidate_text

        labels = classify([
            {"claim_id": item.claim_id, "statement": item.statement, "paper_quotes": quotes}
            for item, quotes in candidates if candidate_text(item.statement, quotes)
        ])
    reconciled = 0
    for item, quotes in candidates:
        if labels is not None:
            scope = _label_scope(labels.get(item.claim_id))
        else:
            scope = _claim_release_scope(item.statement, quotes)
            if any(_NOT_CURRENT.search(_URL.sub("", text)) for text in quotes):
                scope = None
        if scope is None:
            continue
        explicit_urls = {
            _repository_key(match.group())
            for text in [item.statement, *quotes] for match in _URL.finditer(text)
        }
        repositories = [
            repo for repo in eligible
            if not explicit_urls or _repository_key(source_by_id[repo.source_id].url) in explicit_urls
        ]
        verified_urls = {_repository_key(source_by_id[repo.source_id].url) for repo in repositories}
        # A claim naming another repository, or several repositories of which
        # only one is verified, cannot be supported by an unrelated code result.
        if not repositories or explicit_urls - verified_urls:
            continue
        _reconcile_one(result, item, repositories, code, partial=scope == "mixed")
        reconciled += 1
    if reconciled:
        result.dimension.summary += f" {reconciled} code-release claim(s) reconciled with verified repository evidence."
    return result


def _label_scope(label) -> str | None:
    """A model label maps to the same scopes as the sentence rules: code only, or code plus other artifacts."""
    if (label is None or not label.is_release_claim or not label.release_is_current or label.other_assertions
            or "code" not in label.artifacts):
        return None
    return "code" if set(label.artifacts) == {"code"} else "mixed"


def _claim_release_scope(statement: str, quotes: list[str]) -> str | None:
    scope = _release_scope(statement)
    if scope is not None:
        return scope
    # A generic artifact statement can be clarified by an explicit code-release
    # quote, but a broader method/performance assertion cannot inherit support
    # from an unrelated release sentence in the same quoted paragraph.
    generic = re.sub(r"\bartifacts?\b", "code", statement, flags=re.I)
    generic_scope = _release_scope(generic)
    if generic == statement or generic_scope is None:
        return None
    quote_scopes = [_release_scope(text) for text in quotes]
    if not quote_scopes or any(scope is None for scope in quote_scopes):
        return None
    return "mixed" if generic_scope == "mixed" or "mixed" in quote_scopes else "code"


def _release_scope(statement: str) -> str | None:
    text = _URL.sub("URL", statement).strip()
    if not _CODE.search(text) or _NOT_CURRENT.search(text) or not _RELEASE_SENTENCE.fullmatch(text):
        return None
    # 'Model code' names source code, whereas 'code and models' also asserts a
    # model artifact release that the implementation check cannot establish.
    artifacts = re.sub(r"\bmodel\s+code\b", "code", text, flags=re.I)
    artifacts = re.sub(r"\b(?:for|of)\s+(?:this|our|the|its|their)\s+model\b", "", artifacts, flags=re.I)
    return "mixed" if _UNVERIFIED_ARTIFACT.search(artifacts) else "code"


def _repository_key(url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(url.rstrip(".,;:'\"!?)]}"))
    except ValueError:
        # URLs originate in untrusted paper/model text; malformed input must
        # leave the claim unresolved rather than fail report assembly.
        return "", url.casefold()
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if host == "api.github.com" and parts[:1] == ["repos"]:
        host, parts = "github.com", parts[1:]
    if host in {"www.github.com", "github.com"}:
        host = "github.com"
    # Preserve branches/files in a claimed URL: root repository availability
    # alone does not verify that a particular branch or artifact URL exists.
    return host, "/".join(parts).casefold().removesuffix(".git")


def _verified_repositories(code: CodeCheckResult) -> list[CredibilityRepository]:
    evidence = {row.evidence_id: row for row in code.evidence}
    sources = {row.source_id: row for row in code.sources}
    verified = []
    for repo in code.checks.repositories:
        ids = repo.association_evidence_ids + repo.implementation_evidence_ids
        if (repo.association != "confirmed" or repo.availability != "accessible"
                or not repo.association_evidence_ids or not repo.implementation_evidence_ids
                or repo.source_id not in sources or sources[repo.source_id].fetch_status != "ok"):
            continue
        if any(eid not in evidence for eid in ids):
            continue
        if any(
            evidence[eid].origin == "external" and (
                evidence[eid].source_id not in sources
                or sources[evidence[eid].source_id].fetch_status != "ok"
            ) for eid in ids
        ):
            continue
        verified.append(repo)
    return verified


def _reconcile_one(
    result: ClaimCheckResult,
    item: CredibilityClaimItem,
    repositories: list[CredibilityRepository],
    code: CodeCheckResult,
    *,
    partial: bool,
) -> None:
    matching_findings = [finding for finding in result.findings if item.claim_id in finding.item_ids]
    item.reconciled_from = CredibilityClaimReconciliation(
        outcome=item.outcome, reason=item.reason,
        comparison_evidence_ids=list(item.comparison_evidence_ids), search_scope=item.search_scope,
        score_rule_id=item.score_rule_id, item_score=item.item_score,
        findings=[finding.model_copy(deep=True) for finding in matching_findings],
        repository_ids=[repo.repository_id for repo in repositories],
    )
    ids = list(dict.fromkeys([
        eid for repo in repositories for eid in repo.association_evidence_ids + repo.implementation_evidence_ids
    ]))
    item.comparison_evidence_ids = list(dict.fromkeys(item.comparison_evidence_ids + ids))
    result.dimension.evidence_ids = list(dict.fromkeys(result.dimension.evidence_ids + ids))
    item.outcome = "partially_supported" if partial else "supported"
    repo_names = ", ".join(repo.repository_id for repo in repositories)
    prefix = f"[verified by code dimension: {repo_names}] "
    item.reason = prefix + (
        "Code availability is verified by an associated accessible repository with implementation evidence. "
        "The claimed data, checkpoints, weights or model artifacts have not been verified by the code check."
        if partial else
        "Code availability is verified by an associated accessible repository with implementation evidence."
    )
    item.search_scope += f" Code dimension: verified repository evidence ({repo_names})."
    item.score_rule_id = "P_C_PARTIALLY_SUPPORTED" if partial else SUPPORTED_BY_CODE_RULE
    item.item_score = None
    external_sources = {
        row.source_id for row in code.evidence if row.evidence_id in ids and row.source_id is not None
    }
    for finding in matching_findings:
        # Other claims in a combined finding retain their original concern.
        # The prior single-claim finding is preserved verbatim in reconciled_from.
        if finding.item_ids != [item.claim_id]:
            continue
        _update_finding(finding, item, ids, external_sources)


def _update_finding(
    finding: CredibilityFinding, item: CredibilityClaimItem, evidence_ids: list[str], source_ids: set[str],
) -> None:
    finding.kind, finding.severity = "context", "info"
    finding.certainty = "reported"
    finding.title = (
        "Code release verified; other artifact availability remains unverified"
        if item.outcome == "partially_supported" else "Code release verified by repository evidence"
    )
    finding.detail = item.reason
    finding.evidence_ids = list(dict.fromkeys(finding.evidence_ids + evidence_ids))
    finding.source_ids = sorted(set(finding.source_ids) | source_ids)
    finding.limitations = [
        "The original paper-internal judgment and finding are retained in the claim's reconciled_from audit.",
        "Repository association and implementation availability were checked; code was not executed."
        + (" Other claimed artifacts were not verified." if item.outcome == "partially_supported" else ""),
    ]
