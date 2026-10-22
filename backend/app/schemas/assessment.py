"""契约 §4–§7、§17 顶层 Assessment 与其子对象。"""
from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.common import StrictModel
from app.schemas.enums import SCHEMA_VERSION, ErrorCode, SourceType, Stage, Status, WarningCode
from app.schemas.paper import Paper
from app.schemas.report import Report


class Source(StrictModel):
    """契约 §5。arXiv 与 PDF 互斥。"""

    type: SourceType
    arxiv_id: str | None = None
    original_url: str | None = None
    upload_id: str | None = None
    original_filename: str | None = None

    @model_validator(mode="after")
    def _exclusive(self) -> Source:
        if self.type is SourceType.ARXIV:
            if not self.arxiv_id:
                raise ValueError("arxiv source requires arxiv_id")
            if self.upload_id is not None:
                raise ValueError("arxiv source must have upload_id = null")
        else:
            if not self.upload_id:
                raise ValueError("pdf source requires upload_id")
        return self


class VenueRef(StrictModel):
    """报告里回填的 venue 快照。历史报告必须保留当时的 rubric_version。"""

    venue_id: str
    code: str
    name: str
    year: int
    track: str
    rubric_id: str
    rubric_version: str


class VenueOption(VenueRef):
    """契约 §6.1 选项接口返回的对象。"""

    is_available: bool = True


class ModelRef(StrictModel):
    """契约 §7。前端只看得到产品级信息，看不到供应商密钥或内部路由。"""

    model_id: str
    display_name: str
    provider: str
    model_version: str


class ModelOption(ModelRef):
    """契约 §7.1。"""

    description: str = ""
    speed_tier: str = "standard"
    is_available: bool = False
    is_default: bool = False


class Warning_(StrictModel):
    """契约 §17.1。类名带下划线避免遮蔽内置 Warning。"""

    code: WarningCode | str
    message: str
    stage: Stage | None = None


class ErrorInfo(StrictModel):
    """契约 §17.2。message 可直接展示给用户，不含堆栈、密钥或内部路径。"""

    code: ErrorCode | str
    message: str
    stage: Stage | None = None
    retryable: bool = False
    debug_id: str | None = None


class Assessment(StrictModel):
    """契约 §4 顶层对象。"""

    schema_version: str = SCHEMA_VERSION
    assessment_id: str
    status: Status
    stage: Stage
    progress_percent: int = Field(ge=0, le=100)
    source: Source
    venue: VenueRef
    model: ModelRef
    paper: Paper | None = None
    report: Report | None = None
    warnings: list[Warning_] = []
    error: ErrorInfo | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> Assessment:
        """契约 §9.3。"""
        if self.status is Status.COMPLETED:
            if self.stage is not Stage.REPORT_READY:
                raise ValueError("completed requires stage = 'report_ready'")
            if self.progress_percent != 100:
                raise ValueError("completed requires progress_percent = 100")
            if self.report is None:
                raise ValueError("completed requires a report")
            if self.error is not None:
                raise ValueError("completed must have error = null")
        if self.status is Status.FAILED and self.error is None:
            raise ValueError("failed requires an error object")
        return self
