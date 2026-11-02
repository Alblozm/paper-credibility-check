"""契约 §18 创建任务请求。"""
from __future__ import annotations

from pydantic import Field

from app.schemas.assessment import Source
from app.schemas.common import StrictModel
from app.schemas.enums import SCHEMA_VERSION


class CreateAssessmentRequest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    source: Source
    venue_id: str
    model_id: str
    # The credibility assessment this quality assessment was started beside, so every client shows them as one run.
    credibility_assessment_id: str | None = Field(default=None, pattern=r"^cred_[0-9a-f]{32}$")


class CreateAssessmentResponse(StrictModel):
    schema_version: str = SCHEMA_VERSION
    assessment_id: str
    status: str
    stage: str
    progress_percent: int
    created_at: str
