"""校验层主入口：把 LLM 的原始输出变成符合契约 v0.4 的 Report。

管线顺序（每一步都可能产生 issue）：

    raw JSON
      → 1. JSON 解析
      → 2. 后台总分字段占位归一化 + Pydantic 结构校验（契约 §10.2 / §11 / §15）
      → 3. 删除悬空 evidence 引用（§15.4）
      → 4. quote 原文回查 + 页码核对 + 表图编号核对 + 置信度封顶（§15.4）
      → 5. 可复现 checklist 确定性交叉校验
      → 6. 缺证据维度按策略降级
      → 7. 重算 overall_score / rating / is_provisional（§10.5，模型的一律丢弃）
      → 8. parse_quality=poor 时强制 is_provisional（解析验收闸门）
      → Report

第 2 步失败时返回 report=None 与 blocking issues，由 app.llm.repair 决定是
做 repair pass 还是整体重跑。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.parsing.base import SourceIndex
from app.schemas.assessment import Warning_
from app.schemas.enums import ParseQuality
from app.schemas.report import OverallAssessment, Report
from app.validation import crosscheck
from app.validation import evidence as ev
from app.validation.degradation import DegradationPolicy, apply_evidence_degradation
from app.validation.errors import Severity, ValidationIssue
from app.validation.scoring import compute_is_provisional, compute_overall_score

_WARNING_CODE_FOR_ISSUE = {
    "EVIDENCE_DANGLING_REF": "EVIDENCE_DROPPED",
    "EVIDENCE_EXCERPT_NOT_FOUND": "EVIDENCE_EXCERPT_UNVERIFIED",
    "EVIDENCE_PAGE_UNKNOWN": "PAGE_NUMBER_UNCERTAIN",
    "CHECKLIST_DOWNGRADED": "CHECKLIST_DOWNGRADED",
    "DIMENSION_DEGRADED": "DIMENSION_DEGRADED",
    "EVIDENCE_LOCATOR_UNKNOWN": "EVIDENCE_LOCATOR_UNVERIFIED",
    "EVIDENCE_LOCATOR_PAGE_FIXED": "PAGE_NUMBER_CORRECTED",
    "EVIDENCE_CHUNK_UNKNOWN": "EVIDENCE_LOCATOR_UNVERIFIED",
    "EVIDENCE_CONFIDENCE_CAPPED": "EVIDENCE_CONFIDENCE_CAPPED",
    "PARSE_QUALITY_POOR": "PARSE_QUALITY_POOR",
}


@dataclass
class ValidationResult:
    report: Report | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report is not None and not self.blocking_issues

    @property
    def blocking_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.BLOCKING]

    def to_warnings(self, stage: str | None = None) -> list[Warning_]:
        """把已自动修正的问题转成契约 §17.1 的 warning 返回给前端。"""
        out: list[Warning_] = []
        for issue in self.issues:
            if issue.severity is not Severity.REPAIRED:
                continue
            code = _WARNING_CODE_FOR_ISSUE.get(issue.code, issue.code)
            out.append(Warning_(code=code, message=issue.message, stage=stage))
        return out


def _issues_from_pydantic(exc: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for err in exc.errors():
        path = ".".join(str(p) for p in err["loc"]) or "<root>"
        issues.append(
            ValidationIssue(
                code=f"SCHEMA_{err['type'].upper()}",
                path=path,
                message=err["msg"],
                severity=Severity.BLOCKING,
            )
        )
    return issues


def _normalize_backend_overall(payload: dict) -> dict:
    """Ignore model values for fields that the backend recomputes after evidence checks.

    Keep judgment fields and unknown keys intact so genuine contract violations
    still reach Pydantic. Copy each changed object instead of mutating caller input.
    """
    overall = payload.get("overall_assessment")
    if not isinstance(overall, dict):
        return payload

    supplied_calculation = overall.get("calculation")
    calculation = dict(supplied_calculation) if isinstance(supplied_calculation, dict) else {}
    calculation.update(
        method="weighted_average",
        assessed_dimension_count=0,
        excluded_dimension_ids=[],
    )
    normalized_overall = dict(overall)
    normalized_overall.update(
        overall_score=None,
        rating="unable_to_assess",
        is_provisional=True,
        calculation=calculation,
    )
    return {**payload, "overall_assessment": normalized_overall}


def _recompute_overall(report: Report) -> None:
    """契约 §10.5 / §10.4：分数、等级、is_provisional 全部由后端重算。

    模型写的 summary / reading_recommendation / recommendation_reason / confidence
    属于自然语言判断，保留；数值部分一律覆盖。
    """
    score, rating, calculation = compute_overall_score(report.dimensions)
    old = report.overall_assessment
    report.overall_assessment = OverallAssessment(
        overall_score=score,
        rating=rating,
        summary=old.summary,
        reading_recommendation=old.reading_recommendation,
        recommendation_reason=old.recommendation_reason,
        confidence=old.confidence,
        is_provisional=compute_is_provisional(report.dimensions),
        calculation=calculation,
    )


def _force_provisional(report: Report) -> None:
    old = report.overall_assessment
    if old.is_provisional:
        return
    report.overall_assessment = OverallAssessment(
        **{**old.model_dump(), "is_provisional": True}
    )


def _apply_rubric_weights(report: Report, weights: dict[str, float]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, dimension in enumerate(report.dimensions):
        expected = weights.get(dimension.dimension_id)
        if expected is None or abs(dimension.weight - expected) <= 1e-9:
            continue
        previous = dimension.weight
        report.dimensions[index] = dimension.model_copy(update={"weight": expected})
        issues.append(
            ValidationIssue(
                code="RUBRIC_WEIGHT_CORRECTED",
                path=f"dimensions[{index}].weight",
                message=(
                    f"Dimension '{dimension.dimension_id}' weight was corrected from the model's {previous} "
                    f"to the active rubric's {expected}"
                ),
                severity=Severity.REPAIRED,
            )
        )
    return issues


def validate_report_payload(
    raw: str | dict,
    source_text: str | None = None,
    known_pages: set[int] | None = None,
    policy: DegradationPolicy = DegradationPolicy.TO_UNCERTAIN,
    source_index: SourceIndex | None = None,
    dimension_weights: dict[str, float] | None = None,
) -> ValidationResult:
    """校验并规范化一份模型输出。

    Args:
        raw: 模型返回的 JSON 字符串或已解析的 dict。
        source_text: 解析后的论文文本，用于 quote 回查与 checklist 交叉校验。
            不传则跳过这两步（例如只做纯 schema 单元测试）。
        known_pages: 解析出的合法页码集合，用于拦截编造的 page_number。
        policy: 缺证据维度的降级策略。
        source_index: 解析产物的索引（`ParsedDocument.build_index()`）。给了它就
            能多做表图编号核对、chunk 核对和置信度封顶；`source_text` 与
            `known_pages` 仅在没给 index 时使用，保留是为了纯 schema 测试方便。
    """
    result = ValidationResult()
    index = source_index
    if index is not None:
        source_text = index.plain_text
        known_pages = index.pages

    # 1. JSON
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            result.issues.append(
                ValidationIssue(
                    code="MODEL_OUTPUT_NOT_JSON",
                    path="<root>",
                    message=f"The model output is not valid JSON: {exc}",
                )
            )
            return result
    else:
        payload = raw

    if not isinstance(payload, dict):
        result.issues.append(
            ValidationIssue(
                code="MODEL_OUTPUT_NOT_OBJECT",
                path="<root>",
                message=f"The report must be a JSON object, got {type(payload).__name__}",
            )
        )
        return result

    # 2. Backend-owned totals must not trigger repairs before they are recomputed.
    # Judgment fields and extra keys remain subject to the unchanged strict schema.
    try:
        report = Report.model_validate(_normalize_backend_overall(payload))
    except ValidationError as exc:
        result.issues.extend(_issues_from_pydantic(exc))
        return result

    # 3–6. 语义与一致性
    result.issues.extend(ev.drop_dangling_links(report))
    result.issues.extend(ev.verify_excerpts(report, source_text))
    # verify_locators 必须排在 verify_pages 前面：编号能查到时它知道正确页码，
    # 可以直接更正；反过来的话页码已经被 verify_pages 置成 null，信息就丢了。
    result.issues.extend(ev.verify_locators(report, index))
    result.issues.extend(ev.verify_pages(report, known_pages))
    result.issues.extend(ev.cap_location_confidence(report, index))
    result.issues.extend(
        crosscheck.crosscheck_reproducibility(
            report, source_text, index.code_links if index else None
        )
    )
    result.issues.extend(apply_evidence_degradation(report, policy))
    if dimension_weights is not None:
        result.issues.extend(_apply_rubric_weights(report, dimension_weights))

    # 剩下的仍然缺证据的（例如 major finding），只报告不改
    result.issues.extend(
        [i for i in ev.check_required_links(report) if i.code == "MAJOR_FINDING_WITHOUT_EVIDENCE"]
    )

    # 7. 总分重算
    _recompute_overall(report)

    # 8. 解析质量闸门。结构化规范：poor 的解析不得静默产出「有把握」的结论。
    #    这里只把结果标成 provisional，不推翻分数 —— 分数由 §10.5 的公式决定，
    #    解析质量差属于「这份结论还不能当定论」，两者是不同的事。
    if index is not None and index.parse_quality is ParseQuality.POOR:
        _force_provisional(report)
        result.issues.append(
            ValidationIssue(
                code="PARSE_QUALITY_POOR",
                path="<root>",
                message="Parse quality is poor; the report is marked provisional (parse acceptance gate)",
                severity=Severity.REPAIRED,
            )
        )

    result.report = report
    return result
