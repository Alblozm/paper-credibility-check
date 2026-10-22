"""校验层的自测接口：直接把一份模型输出丢进来看校验结果。

不属于契约的对外接口，挂在 /internal 下，前端不需要接。它的用途是让数据集维护者
和孙海晨在调 prompt 时能立刻知道输出哪里不合规，而不用等整条流程。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.validation.report_validator import validate_report_payload

router = APIRouter(prefix="/internal", tags=["internal"])


class ValidateRequest(BaseModel):
    report: dict[str, Any] | str
    source_text: str | None = None


class ValidateResponse(BaseModel):
    ok: bool
    issues: list[dict]
    warnings: list[dict]
    report: dict | None = None


@router.post("/validate-report", response_model=ValidateResponse)
def validate_report(body: ValidateRequest) -> ValidateResponse:
    result = validate_report_payload(
        body.report,
        source_text=body.source_text,
        policy=get_settings().evidence_degradation_policy,
    )
    return ValidateResponse(
        ok=result.ok,
        issues=[i.model_dump(mode="json") for i in result.issues],
        warnings=[w.model_dump(mode="json") for w in result.to_warnings()],
        report=result.report.model_dump(mode="json") if result.report else None,
    )
