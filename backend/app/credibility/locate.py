"""论文内证据的定位核对。三项检查共用，任务二、任务三的论文内证据也走这里。

候选证据（已准备文件或以后的模型输出）只能给 chunk_id 或 element_id、摘录和数字。
页码、章节、表号一律以解析结果为准；quote 必须能在原文找到；转述里写出的数字
必须在原文出现。任何一条不满足，这条证据就不进入报告。
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal

from app.credibility.context import CheckContext
from app.credibility.models import CredibilityEvidence, CredibilityPaperLocator, CredibilityReport
from app.schemas.enums import ParseQuality
from app.validation.quote_match import find_source_quote  # noqa: F401  # re-exported for claim checks

_ELEMENT_CONFIDENCE = {
    ParseQuality.GOOD: "high",
    ParseQuality.PARTIAL: "medium",
    ParseQuality.POOR: "low",
}


@dataclass(frozen=True)
class PaperSpanCandidate:
    evidence_id: str
    mode: Literal["quote", "paraphrase"]
    excerpt: str
    chunk_id: str | None = None
    element_id: str | None = None
    values: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LocatedSpan:
    evidence: CredibilityEvidence
    section_role: str


def locate_paper_span(
    ctx: CheckContext,
    candidate: PaperSpanCandidate,
    *,
    allowed_roles: Collection[str] | None = None,
    forbidden_roles: Collection[str] = (),
) -> tuple[LocatedSpan | None, list[str]]:
    """返回核对后的证据和问题清单。有问题时证据为 None。"""
    if ctx.doc is None:
        return None, ["the paper has not been parsed"]
    if (candidate.chunk_id is None) == (candidate.element_id is None):
        return None, [f"{candidate.evidence_id} must point to exactly one chunk_id or element_id"]

    if candidate.chunk_id is not None:
        block = ctx.block(candidate.chunk_id)
        if block is None:
            return None, [f"chunk_id {candidate.chunk_id} was not found in the parsed paper"]
        text = block.text
        role = block.section_role
        locator = CredibilityPaperLocator(
            page_start=block.page_start,
            page_end=block.page_end if block.page_end is not None else block.page_start,
            chunk_id=block.chunk_id,
            section_title=block.section_title or None,
            element_label=None,
        )
        confidence = block.location_confidence.value
        where = candidate.chunk_id
    else:
        element = ctx.element(candidate.element_id or "")
        if element is None:
            return None, [f"element_id {candidate.element_id} was not found in the parsed paper"]
        if element.page_number is None:
            return None, [f"element_id {element.element_id} has no page number in the parsed paper"]
        text = "\n".join(part for part in (element.caption, element.extracted_text) if part)
        role = element.section_role
        locator = CredibilityPaperLocator(
            page_start=element.page_number,
            page_end=element.page_number,
            chunk_id=element.element_id,
            section_title=ctx.section_title(element.section_id),
            element_label=element.label or None,
        )
        confidence = _ELEMENT_CONFIDENCE[element.parse_quality]
        where = element.element_id

    problems: list[str] = []
    if allowed_roles is not None and role not in allowed_roles:
        problems.append(f"{where} is in a '{role or 'unknown'}' section, outside {sorted(allowed_roles)}")
    if role in forbidden_roles:
        problems.append(f"{where} is in a '{role}' section, which cannot serve as evidence here")
    source_excerpt = candidate.excerpt
    if candidate.mode == "quote":
        matched = find_source_quote(text, candidate.excerpt)
        if matched is None:
            problems.append(f"the quote for {candidate.evidence_id} was not found in {where}")
        else:
            source_excerpt = matched
    for value in candidate.values:
        if not re.search(rf"(?<![\d.]){re.escape(value)}(?![\d])", text):
            problems.append(f"value {value} does not appear in {where}")
    if problems:
        return None, problems

    return LocatedSpan(
        evidence=CredibilityEvidence(
            evidence_id=candidate.evidence_id,
            origin="paper",
            mode=candidate.mode,
            excerpt=source_excerpt,
            paper_locator=locator,
            source_id=None,
            location_confidence=confidence,
        ),
        section_role=role,
    ), []


def assert_report_links(report: CredibilityReport) -> None:
    """报告内所有 id 引用必须闭合；论文内证据和外部证据不得混写。"""
    evidence_ids = [e.evidence_id for e in report.evidence]
    source_ids = [s.source_id for s in report.sources]
    finding_ids = [f.finding_id for f in report.findings]
    for name, ids in (("evidence", evidence_ids), ("source", source_ids), ("finding", finding_ids)):
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate {name} ids: {sorted(duplicates)}")
    evidence, sources, findings = set(evidence_ids), set(source_ids), set(finding_ids)

    for e in report.evidence:
        if e.origin == "paper" and (e.paper_locator is None or e.source_id is not None):
            raise ValueError(f"paper evidence {e.evidence_id} needs a paper locator and no source_id")
        if e.origin == "external" and (e.paper_locator is not None or e.source_id not in sources):
            raise ValueError(f"external evidence {e.evidence_id} needs an existing source_id and no paper locator")

    def need(ids: list[str], pool: set[str], where: str) -> None:
        missing = [i for i in ids if i not in pool]
        if missing:
            raise ValueError(f"{where} references unknown ids {missing}")

    for d in report.dimensions:
        need(d.evidence_ids, evidence, f"dimension {d.dimension_id}")
        need(d.finding_ids, findings, f"dimension {d.dimension_id}")
    for f in report.findings:
        need(f.evidence_ids, evidence, f"finding {f.finding_id}")
        need(f.source_ids, sources, f"finding {f.finding_id}")
    for a in report.alerts:
        need(a.finding_ids, findings, f"alert {a.type}")
    for c in report.checks.claims.items:
        need(c.claim_evidence_ids + c.comparison_evidence_ids, evidence, f"claim {c.claim_id}")
        if c.reconciled_from is not None:
            audit = c.reconciled_from
            need(audit.comparison_evidence_ids, evidence, f"claim audit {c.claim_id}")
            need(
                audit.repository_ids, {row.repository_id for row in report.checks.code.repositories},
                f"claim audit {c.claim_id}",
            )
            for finding in audit.findings:
                need(finding.evidence_ids, evidence, f"claim audit finding {finding.finding_id}")
                need(finding.source_ids, sources, f"claim audit finding {finding.finding_id}")
    for r in report.checks.references.items:
        need(r.paper_evidence_ids, evidence, f"reference {r.reference_id}")
        need(r.source_ids, sources, f"reference {r.reference_id}")
        for cand in r.candidates:
            need(cand.evidence_ids, evidence, f"reference candidate {cand.candidate_id}")
            need([cand.source_id], sources, f"reference candidate {cand.candidate_id}")
    code = report.checks.code
    discovery = report.code_discovery
    query_ids = [row.query_id for row in discovery.queries]
    candidate_ids = [row.candidate_id for row in discovery.candidates]
    if len(query_ids) != len(set(query_ids)) or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("duplicate code discovery query/candidate ids")
    repositories = {row.repository_id: row for row in code.repositories}
    for query in discovery.queries:
        need(query.source_ids, sources, f"discovery query {query.query_id}")
    for candidate in discovery.candidates:
        need(candidate.query_ids, set(query_ids), f"discovery candidate {candidate.candidate_id}")
        need(candidate.source_ids, sources, f"discovery candidate {candidate.candidate_id}")
        need(candidate.evidence_ids, evidence, f"discovery candidate {candidate.candidate_id}")
        if candidate.repository_id is not None:
            need([candidate.repository_id], set(repositories), f"discovery candidate {candidate.candidate_id}")
        if candidate.status == "verified":
            repository = repositories.get(candidate.repository_id)
            if (repository is None or repository.association != "confirmed"
                    or not repository.association_evidence_ids
                    or not set(repository.association_evidence_ids) <= set(candidate.evidence_ids)):
                raise ValueError("verified discovery candidate lacks actual confirmed repository evidence")
    for s in code.searches:
        need(s.evidence_ids, evidence, f"code search {s.scope_id}")
        need(s.source_ids, sources, f"code search {s.scope_id}")
    for repo in code.repositories:
        need([repo.source_id], sources, f"repository {repo.repository_id}")
        need(
            repo.association_evidence_ids + repo.implementation_evidence_ids,
            evidence,
            f"repository {repo.repository_id}",
        )
    for disc in code.discussions:
        need(disc.source_ids + disc.author_response_source_ids, sources, f"discussion {disc.discussion_id}")
        need(disc.evidence_ids, evidence, f"discussion {disc.discussion_id}")
