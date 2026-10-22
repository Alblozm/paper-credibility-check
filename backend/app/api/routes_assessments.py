"""契约 §18 / §19 的对外接口。

已接入统一编排；默认 fixture/stub 仅用于接口联调，真实解析仍待完成。
路由、请求/响应模型和状态语义已经按契约定好，前端可以先照着 /docs 开发。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query, UploadFile

from app.api.routes_options import list_models, list_venues
from app.config import get_settings
from app.parsing.arxiv import ArxivSourceError, normalize_arxiv_id
from app.pipeline.orchestrator import run_assessment
from app.schemas.assessment import Assessment, ModelRef, VenueRef
from app.schemas.enums import SCHEMA_VERSION, Stage, Status
from app.schemas.requests import CreateAssessmentRequest, CreateAssessmentResponse
from app.storage.assessment_history import (
    list_assessments as list_saved_assessments,
)
from app.storage.assessment_history import (
    load_assessment,
    save_assessment,
)
from app.storage.uploads import reject, resolve_upload, save_upload

router = APIRouter(prefix="/v1", tags=["assessments"])

# Active tasks are kept in memory for polling and also snapshotted to local SQLite.
_STORE: dict[str, dict] = {}


class _PersistentState(dict):
    def update(self, *args, **kwargs) -> None:
        super().update(*args, **kwargs)
        save_assessment(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@router.post("/uploads", status_code=201)
async def create_upload(file: UploadFile) -> dict:
    return await save_upload(file)


@router.post("/assessments", response_model=CreateAssessmentResponse, status_code=202)
def create_assessment(
    body: CreateAssessmentRequest,
    background: BackgroundTasks,
) -> CreateAssessmentResponse:
    """创建任务并立刻返回 assessment_id。

    评分是长任务（30 秒到数分钟），绝不能放在同步请求里——Nginx、浏览器和
    云平台都会中途超时。前端拿 assessment_id 轮询下面的状态接口。
    """
    if body.schema_version != SCHEMA_VERSION:
        reject(422, "SCHEMA_VERSION_UNSUPPORTED", "Only Field Contract v0.4 is supported.")
    venue = next((v for v in list_venues() if v.venue_id == body.venue_id and v.is_available), None)
    model = next((m for m in list_models() if m.model_id == body.model_id and m.is_available), None)
    if venue is None or model is None:
        reject(400, "OPTION_UNAVAILABLE", "The selected venue or model does not exist or is unavailable.")
    pdf_path = None
    if body.source.type.value == "arxiv":
        try:
            arxiv_id = normalize_arxiv_id(body.source.arxiv_id or body.source.original_url or "")
        except ArxivSourceError:
            reject(400, "INVALID_ARXIV_ID", "Enter a valid arXiv ID or link.")
        body.source.arxiv_id = arxiv_id
        body.source.original_url = f"https://arxiv.org/abs/{arxiv_id}"
    else:
        if body.source.arxiv_id is not None or body.source.original_url is not None:
            reject(400, "INVALID_SOURCE", "A PDF source must not include arXiv fields.")
        pdf_path = str(resolve_upload(body.source.upload_id or ""))
    assessment_id = f"asmt_{uuid.uuid4().hex[:12]}"
    created_at = _now()
    _STORE[assessment_id] = _PersistentState({
        "schema_version": SCHEMA_VERSION,
        "assessment_id": assessment_id,
        "status": Status.QUEUED.value,
        "stage": Stage.VALIDATING_INPUT.value,
        "progress_percent": 0,
        "created_at": created_at,
        "source": body.source.model_dump(mode="json"),
        "venue": VenueRef(**{k: v for k, v in venue.model_dump().items()
                            if k in VenueRef.model_fields}).model_dump(mode="json"),
        "model": ModelRef(**{k: v for k, v in model.model_dump().items()
                            if k in ModelRef.model_fields}).model_dump(mode="json"),
        "paper": None,
        "report": None,
        "warnings": ([{"code": "DEMO_MODE", "stage": "validating_input",
                       "message": ("Demo assessment: this is a fixed example report, "
                                   "not an analysis of the submitted paper.")}]
                     if get_settings().llm_provider == "fixture" or get_settings().parser_provider == "stub" else []),
        "error": None,
        "started_at": None,
        "completed_at": None,
    })
    save_assessment(_STORE[assessment_id])
    background.add_task(run_assessment, assessment_id, body, _STORE, pdf_path=pdf_path)
    return CreateAssessmentResponse(
        assessment_id=assessment_id,
        status=Status.QUEUED.value,
        stage=Stage.VALIDATING_INPUT.value,
        progress_percent=0,
        created_at=created_at,
    )


@router.get("/assessments")
def get_assessment_history(
    search: str = Query(default="", max_length=200),
    sort: Literal["date", "title", "author"] = "date",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """List local completed and failed reviews; never include raw uploaded PDFs."""
    return list_saved_assessments(search=search, sort=sort, offset=offset, limit=limit)


@router.get("/assessments/{assessment_id}", response_model=Assessment)
def get_assessment(assessment_id: str) -> dict:
    """轮询任务状态与结果。契约 §9.3：completed 必须对应 report_ready + 100%。"""
    item = _STORE.get(assessment_id) or load_assessment(assessment_id)
    if item is None:
        reject(404, "NOT_FOUND", "Assessment not found.")
    return item
