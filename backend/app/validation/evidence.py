"""契约 §15.4 证据验收规则。

五件事：
1. 悬空引用：evidence_link.evidence_id 必须存在于报告的 evidence pool
2. 原文回查：quote 必须能在解析文本里找到；找不到就降 location_confidence
3. 页码核对：page_number 必须来自解析结果里出现过的页，模型不许自己编
4. 定位核对：表 / 图 / 公式的编号必须在解析出的 element 清单里存在；
   引用的 chunk_id 必须真的是我们发给模型的那些
5. 置信度上限：证据的 location_confidence 不得高于解析器对该 chunk 的自评

第 4、5 条对齐 `paper_structuring_spec.md` v0.1 —— 解析产物是带
chunk_id、page_start/page_end、location_confidence 的结构化记录，表图是独立
element，所以这些都是可以确定性核对的，不需要再调模型。
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from app.parsing.base import SourceIndex, confidence_rank, parse_label_tokens
from app.schemas.common import EvidenceLink
from app.schemas.enums import Confidence, EvidenceMode, Importance, LocatorType
from app.schemas.report import Report
from app.validation.errors import Severity, ValidationIssue
from app.validation.quote_match import find_source_quote

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """做原文匹配前的归一化：压空白、去掉连字符换行、统一引号。"""
    t = text.replace("-\n", "").replace("’", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", t).strip().lower()


def iter_evidence_links(report: Report) -> Iterator[tuple[str, EvidenceLink]]:
    """遍历报告里所有 evidence_link，附带它所在的路径，便于定位问题。"""
    for i, c in enumerate(report.claimed_contributions):
        for j, link in enumerate(c.evidence_links):
            yield f"claimed_contributions[{i}].evidence_links[{j}]", link
    for i, c in enumerate(report.claim_checks):
        for j, link in enumerate(c.evidence_links):
            yield f"claim_checks[{i}].evidence_links[{j}]", link
    for i, d in enumerate(report.dimensions):
        for j, link in enumerate(d.evidence_links):
            yield f"dimensions[{i}].evidence_links[{j}]", link
    for i, c in enumerate(report.reproducibility_checklist):
        for j, link in enumerate(c.evidence_links):
            yield f"reproducibility_checklist[{i}].evidence_links[{j}]", link
    for name in ("strengths", "concerns"):
        for i, f in enumerate(getattr(report, name)):
            for j, link in enumerate(f.evidence_links):
                yield f"{name}[{i}].evidence_links[{j}]", link
    for i, q in enumerate(report.unresolved_questions):
        for j, link in enumerate(q.evidence_links):
            yield f"unresolved_questions[{i}].evidence_links[{j}]", link


def _prune(links: list[EvidenceLink], valid_ids: set[str]) -> list[EvidenceLink]:
    return [link for link in links if link.evidence_id in valid_ids]


def drop_dangling_links(report: Report) -> list[ValidationIssue]:
    """删除指向不存在证据的 link，原地修改 report。契约 §15.4 第 3 条。"""
    valid_ids = {e.evidence_id for e in report.evidence}
    issues: list[ValidationIssue] = []

    for path, link in list(iter_evidence_links(report)):
        if link.evidence_id not in valid_ids:
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_DANGLING_REF",
                    path=path,
                    message=(
                        f"evidence_id '{link.evidence_id}' is not in report.evidence; "
                        f"the link was removed"
                    ),
                    severity=Severity.REPAIRED,
                )
            )

    for c in report.claimed_contributions:
        c.evidence_links = _prune(c.evidence_links, valid_ids)
    for c in report.claim_checks:
        c.evidence_links = _prune(c.evidence_links, valid_ids)
    for d in report.dimensions:
        d.evidence_links = _prune(d.evidence_links, valid_ids)
    for c in report.reproducibility_checklist:
        c.evidence_links = _prune(c.evidence_links, valid_ids)
    for f in report.strengths + report.concerns:
        f.evidence_links = _prune(f.evidence_links, valid_ids)
    for q in report.unresolved_questions:
        q.evidence_links = _prune(q.evidence_links, valid_ids)

    return issues


def verify_excerpts(report: Report, source_text: str | None) -> list[ValidationIssue]:
    """quote 模式的 excerpt 必须能在原文中找到，否则降 location_confidence。

    paraphrase 天然不要求逐字匹配，跳过。没有 source_text（例如只跑 schema 校验）
    时同样跳过，而不是把所有证据都判死。
    """
    if not source_text:
        return []

    haystack = normalize(source_text)
    issues: list[ValidationIssue] = []

    for i, ev in enumerate(report.evidence):
        if ev.evidence_mode is not EvidenceMode.QUOTE:
            continue
        needle = normalize(ev.excerpt)
        if len(needle) < 12:
            continue  # 太短的片段匹配没有意义
        # 严格匹配失败时，再用与可信度核查共用的匹配器：只容忍 PDF 排版字符和抽取造成的空格差异。
        if needle not in haystack and find_source_quote(source_text, ev.excerpt) is None:
            ev.location_confidence = Confidence.LOW
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_EXCERPT_NOT_FOUND",
                    path=f"evidence[{i}]",
                    message=(
                        f"The quote of evidence_id '{ev.evidence_id}' was not found in the parsed text; "
                        f"location_confidence was lowered to low"
                    ),
                    severity=Severity.REPAIRED,
                )
            )
    return issues


def verify_pages(report: Report, known_pages: set[int] | None) -> list[ValidationIssue]:
    """页码必须来自解析注入的 anchor。契约 §15.4：无法确定时置 null 并降置信度。"""
    if not known_pages:
        return []

    issues: list[ValidationIssue] = []
    for i, ev in enumerate(report.evidence):
        if ev.page_number is not None and ev.page_number not in known_pages:
            bad = ev.page_number
            ev.page_number = None
            ev.location_confidence = Confidence.LOW
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_PAGE_UNKNOWN",
                    path=f"evidence[{i}]",
                    message=f"page_number {bad} is not among the parsed pages and was set to null",
                    severity=Severity.REPAIRED,
                )
            )
    return issues


_CHUNK_ID_SHAPE = re.compile(r"chunk[_-]?\d+$", re.IGNORECASE)
_ELEMENT_LOCATORS = {
    LocatorType.TABLE: "table",
    LocatorType.FIGURE: "figure",
    LocatorType.EQUATION: "equation",
    LocatorType.THEOREM: "theorem",
}


def _downgrade(ev_ref, level: Confidence = Confidence.LOW) -> bool:
    """只往低处压。返回是否真的改动了。"""
    if confidence_rank(ev_ref.location_confidence) <= confidence_rank(level):
        return False
    ev_ref.location_confidence = level
    return True


def verify_locators(report: Report, index: SourceIndex | None) -> list[ValidationIssue]:
    """核对 locator_label 指向的东西是否真的存在。契约 §15.4「不得编造图表编号」。

    - 表 / 图 / 公式：编号必须出现在解析出的 element 清单里。模型爱写「Tables 4-6」
      这种区间，`parse_label_tokens` 会展开，命中任意一个即算通过。
    - 正文：`locator_label` 若长得像 chunk_id 却不在我们发出去的集合里，就是编的。
    - 编号对得上但页码对不上时，以 element 的页码为准直接改掉 —— 位置信息的
      事实来源是解析结果，不是模型。

    认不出编号的自由标签（如「Appendix A」）不判失败，只是无法核对。
    """
    if index is None:
        return []

    issues: list[ValidationIssue] = []
    for i, ev_ref in enumerate(report.evidence):
        kind = _ELEMENT_LOCATORS.get(ev_ref.locator_type)

        if kind is not None and index.has_elements:
            tokens = {t for t in parse_label_tokens(ev_ref.locator_label) if t[0] == kind}
            if not tokens:
                continue  # 标签里没有可识别的编号，无法核对
            known = tokens & set(index.element_pages)
            if not known:
                _downgrade(ev_ref)
                issues.append(
                    ValidationIssue(
                        code="EVIDENCE_LOCATOR_UNKNOWN",
                        path=f"evidence[{i}]",
                        message=(
                            f"evidence_id '{ev_ref.evidence_id}' cites "
                            f"'{ev_ref.locator_label}', which is not in the parsed {kind} list; "
                            f"location_confidence was lowered to low"
                        ),
                        severity=Severity.REPAIRED,
                    )
                )
                continue

            pages = {index.element_pages[t] for t in known if index.element_pages[t] is not None}
            if pages and ev_ref.page_number is not None and ev_ref.page_number not in pages:
                wrong = ev_ref.page_number
                ev_ref.page_number = min(pages)
                issues.append(
                    ValidationIssue(
                        code="EVIDENCE_LOCATOR_PAGE_FIXED",
                        path=f"evidence[{i}]",
                        message=(
                            f"'{ev_ref.locator_label}' is on page {ev_ref.page_number}; "
                            f"the model's page {wrong} was corrected from the parsed document"
                        ),
                        severity=Severity.REPAIRED,
                    )
                )
            continue

        if ev_ref.locator_type is LocatorType.TEXT and index.chunk_ids:
            label = (ev_ref.locator_label or "").strip()
            if not label or label in index.chunk_ids:
                continue
            if _CHUNK_ID_SHAPE.search(label):
                _downgrade(ev_ref)
                issues.append(
                    ValidationIssue(
                        code="EVIDENCE_CHUNK_UNKNOWN",
                        path=f"evidence[{i}]",
                        message=(
                            f"evidence_id '{ev_ref.evidence_id}' cites chunk "
                            f"'{label}', which was not in the text sent to the model; "
                            f"location_confidence was lowered to low"
                        ),
                        severity=Severity.REPAIRED,
                    )
                )
    return issues


def cap_location_confidence(report: Report, index: SourceIndex | None) -> list[ValidationIssue]:
    """证据的置信度不得高于解析器对那一块的自评。

    benchmark 的 chunk 自带 `location_confidence`：解析器都说这块位置只有 low 置信度，
    模型没有任何依据说自己 high。这条纯粹是取两者的下界。
    """
    if index is None or not index.chunk_confidence:
        return []

    issues: list[ValidationIssue] = []
    for i, ev_ref in enumerate(report.evidence):
        ceiling = index.chunk_confidence.get((ev_ref.locator_label or "").strip())
        if ceiling is None:
            continue
        if _downgrade(ev_ref, ceiling):
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_CONFIDENCE_CAPPED",
                    path=f"evidence[{i}]",
                    message=(
                        f"The parser rated the location of chunk '{ev_ref.locator_label}' as "
                        f"{ceiling.value}; the evidence confidence was lowered to match"
                    ),
                    severity=Severity.REPAIRED,
                )
            )
    return issues


def check_required_links(report: Report) -> list[ValidationIssue]:
    """契约 §15.4 前两条：有数字分数的维度、major finding 都必须至少一条证据。

    只报告不修改；如何降级由 app.validation.degradation 决定。
    """
    issues: list[ValidationIssue] = []

    for i, d in enumerate(report.dimensions):
        if d.score is not None and not d.evidence_links:
            issues.append(
                ValidationIssue(
                    code="DIMENSION_WITHOUT_EVIDENCE",
                    path=f"dimensions[{i}]",
                    message=f"Dimension '{d.dimension_id}' has a numeric score but no evidence_link",
                )
            )

    for name in ("strengths", "concerns"):
        for i, f in enumerate(getattr(report, name)):
            if f.importance is Importance.MAJOR and not f.evidence_links:
                issues.append(
                    ValidationIssue(
                        code="MAJOR_FINDING_WITHOUT_EVIDENCE",
                        path=f"{name}[{i}]",
                        message=f"Major finding '{f.finding_id}' has no evidence_link",
                    )
                )
    return issues
