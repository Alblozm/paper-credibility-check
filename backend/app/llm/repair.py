"""两级重试中的第一级——repair pass。

思路：整份报告重跑很贵而且不稳定；大多数失败只是几个字段写错。把
Pydantic 的报错逐条喂回模型，只要求它输出修正后的完整 JSON，通常一次就好。
两次修不好再交给上层做整体重跑。
"""
from __future__ import annotations

import json
import logging

from app.llm.base import LLMClient
from app.llm.prompts import REPORT_SCHEMA_GUIDE
from app.parsing.base import SourceIndex
from app.validation.degradation import DegradationPolicy
from app.validation.errors import ValidationIssue
from app.validation.report_validator import ValidationResult, validate_report_payload

logger = logging.getLogger(__name__)

REPAIR_SYSTEM_PROMPT = (
    "You fix malformed JSON reports so they satisfy a strict schema. "
    "Return ONLY the corrected JSON object. Do not add commentary, markdown fences, "
    "or new factual content. Never invent evidence, page numbers, quotes, or figure labels: "
    "if a required field cannot be filled from the previous output, use the schema's "
    "uncertain / not_applicable / null form instead."
)


def build_repair_prompt(previous_output: str, issues: list[ValidationIssue]) -> str:
    """把校验报错组装成一条修复指令。"""
    lines = "\n".join(issue.as_prompt_line() for issue in issues)
    return (
        "The following JSON failed schema validation. Use the exact contract below; "
        "do not guess field names or use aliases.\n\n"
        f"--- EXACT REPORT CONTRACT ---\n{REPORT_SCHEMA_GUIDE}\n--- END CONTRACT ---\n\n"
        f"--- PREVIOUS OUTPUT ---\n{previous_output}\n--- END ---\n\n"
        f"Validation errors:\n{lines}\n\n"
        "Return the corrected JSON object in full. Fix only the listed problems."
    )


def validate_with_repair(
    client: LLMClient,
    raw_output: str,
    *,
    source_text: str | None = None,
    known_pages: set[int] | None = None,
    policy: DegradationPolicy = DegradationPolicy.TO_UNCERTAIN,
    source_index: SourceIndex | None = None,
    dimension_weights: dict[str, float] | None = None,
    max_repairs: int = 2,
) -> tuple[ValidationResult, int]:
    """校验 → 失败则修复 → 再校验，最多 max_repairs 轮。

    Returns:
        (最终的 ValidationResult, 实际用掉的修复轮数)
    """
    result = validate_report_payload(
        raw_output, source_text, known_pages, policy, source_index, dimension_weights
    )
    if result.ok:
        return result, 0

    current = raw_output
    for attempt in range(1, max_repairs + 1):
        blocking = result.blocking_issues
        if not blocking:
            break
        logger.info("repair pass %s/%s for %s blocking issue(s)", attempt, max_repairs, len(blocking))
        response = client.complete_json(
            REPAIR_SYSTEM_PROMPT,
            build_repair_prompt(current, blocking),
            temperature=0.0,
        )
        current = response.text
        result = validate_report_payload(
            current, source_text, known_pages, policy, source_index, dimension_weights
        )
        if result.ok:
            return result, attempt

    if result.blocking_issues:
        logger.warning(
            "report remains invalid after %s repair pass(es): %s",
            max_repairs,
            json.dumps(
                [
                    {"code": issue.code, "path": issue.path, "message": issue.message}
                    for issue in result.blocking_issues
                ],
                ensure_ascii=False,
            ),
        )
    return result, max_repairs
