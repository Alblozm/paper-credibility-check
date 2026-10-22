"""契约 §18 创建任务请求。"""
from __future__ import annotations

from app.schemas.assessment import Source
from app.schemas.common import StrictModel
from app.schemas.enums import SCHEMA_VERSION


class CreateAssessmentRequest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    source: Source
    venue_id: str
    model_id: str


class CreateAssessmentResponse(StrictModel):
    schema_version: str = SCHEMA_VERSION
    assessment_id: str
    status: str
    stage: str
    progress_percent: int
    created_at: str
