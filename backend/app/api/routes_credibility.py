"""可信度核查的独立入口（analysis_mode=credibility），不进入旧五维评估管线。

POST 返回 202 排队快照，后台推进 retrieving → parsing → checking → finalizing → done；
GET 返回当前快照。每次状态变化都换一份新快照，已返回给客户端的对象不会被改写。
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.api.routes_options import list_models
from app.config import get_settings
from app.credibility import runtime
from app.credibility.models import CreateCredibilityRequest, CredibilityAssessment, CredibilityAssessmentResponse
from app.credibility.rubric import load_credibility_rubric
from app.credibility.shell import PaperIneligible, PaperUnavailable, build_completed_assessment
from app.llm.retry import LLMError
from app.parsing.arxiv import ArxivSourceError, normalize_arxiv_id
from app.parsing.benchmark import BenchmarkParseError
from app.storage.uploads import reject, resolve_upload

router = APIRouter(tags=["credibility"])
logger = logging.getLogger(__name__)

# Compatibility cache; the database is authoritative across processes/restarts.
_TASKS: dict[str, dict] = {}
# Lease tag for workers started by this API process. CLI recovery stays untagged,
# so an API restart never fails a resume that is still running elsewhere.
API_OWNER = "api"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _replace(assessment_id: str, lease, **updates: object) -> None:
    snapshot = runtime.get_task_store().update_task(assessment_id, lease, **updates)
    _TASKS[assessment_id] = CredibilityAssessment.model_validate(snapshot).model_dump(mode="json")


def _run(
    assessment_id: str, model_id: str, source: dict, created_at: str, rubric_version: str = "0.1-draft",
    *, resume: bool = False, force: bool = False, owner: str | None = None,
) -> None:
    from app.storage.credibility_tasks import LeaseConflict

    store = runtime.get_task_store()
    fingerprint = runtime.request_fingerprint(source, model_id, rubric_version, runtime.request_versions())
    lease = store.claim_task(
        assessment_id, resume=resume, force=force,
        lease_seconds=get_settings().credibility_lease_seconds, fingerprint=fingerprint, owner=owner,
    )
    execution = runtime.StageExecution(store, assessment_id, lease, fingerprint)
    execution.start_heartbeat()
    started_at = _now()
    _replace(assessment_id, lease, stage="retrieving", progress_percent=10, started_at=started_at)
    try:
        completed = build_completed_assessment(
            assessment_id=assessment_id, model_id=model_id, source=source, created_at=created_at,
            started_at=started_at, completed_at=_now(), rubric=load_credibility_rubric(rubric_version),
            on_stage=lambda stage, progress: _replace(assessment_id, lease, stage=stage, progress_percent=progress),
            execution=execution,
            schema_version=store.load_task(assessment_id)["schema_version"],
        )
        finished_at = _now()
        completed["completed_at"] = finished_at
        if completed.get("report"):
            completed["report"]["generated_at"] = finished_at
        execution.save("report", fingerprint, {"assessment": completed})
        _TASKS[assessment_id] = store.finish_task(assessment_id, lease, completed)
    except LeaseConflict:
        logger.warning("credibility assessment %s lost its execution lease", assessment_id)
    except LLMError as exc:
        logger.exception("credibility assessment %s model call failed", assessment_id)
        _fail(assessment_id, lease, exc.error_code.value,
              "The model could not produce a validated result. Please try again.", exc.retryable)
    except PaperUnavailable as exc:
        logger.info("credibility assessment %s could not use its paper: %s", assessment_id, exc)
        _fail(assessment_id, lease, exc.code, str(exc), exc.retryable)
    except PaperIneligible as exc:
        logger.info("credibility assessment %s is out of scope: %s", assessment_id, exc)
        _fail(assessment_id, lease, "UNABLE_TO_ASSESS", str(exc), False, paper=exc.paper)
    except BenchmarkParseError:
        logger.exception("credibility assessment %s could not read the parsed paper", assessment_id)
        _fail(assessment_id, lease, "PARSE_FAILED",
              "The parsed paper could not be read, so no assessment was produced.", False)
    except Exception:
        logger.exception("credibility assessment %s crashed", assessment_id)
        _fail(assessment_id, lease, "INTERNAL_ERROR",
              "The credibility check failed unexpectedly. Try again later.", True)
    finally:
        execution.stop_heartbeat()


def _fail(
    assessment_id: str, lease, code: str, message: str, retryable: bool, *, paper: dict | None = None,
) -> None:
    _TASKS[assessment_id] = runtime.get_task_store().fail_task(
        assessment_id, lease,
        {"code": code, "message": message, "retryable": retryable, "debug_id": assessment_id},
        paper=paper, report=None,
    )


def _normalize_source(source: dict, *, validate: bool) -> dict:
    """Store {type, arxiv_id, upload_id}; reject inputs that can never succeed before queueing."""
    if source["type"] == "arxiv":
        raw = source.get("arxiv_id") or source.get("original_url") or ""
        try:
            arxiv_id = normalize_arxiv_id(raw)
        except ArxivSourceError:
            if validate:
                reject(400, "INVALID_ARXIV_ID", "Enter a valid arXiv ID or link.")
            arxiv_id = raw
        return {"type": "arxiv", "arxiv_id": arxiv_id, "upload_id": None}
    if validate:
        resolve_upload(source.get("upload_id") or "")
    return {"type": "pdf", "arxiv_id": None, "upload_id": source["upload_id"]}


def fail_interrupted_credibility_tasks() -> list[str]:
    """Background tasks do not survive a restart, so their pollers must see an end."""
    failed = runtime.get_task_store().fail_interrupted_tasks(API_OWNER, {
        "code": "SERVICE_RESTARTED",
        "message": "The local service restarted before this check finished. Please submit it again.",
        "retryable": True,
    })
    for assessment_id in failed:
        _TASKS.pop(assessment_id, None)
    if failed:
        logger.warning("marked %d interrupted credibility assessment(s) failed", len(failed))
    return failed


@router.post("/v1/credibility/assessments", status_code=202, response_model=CredibilityAssessmentResponse)
def create_credibility_assessment(body: CreateCredibilityRequest, background: BackgroundTasks) -> dict:
    offline_demo = body.model_id == "not_called" and get_settings().llm_provider == "fixture"
    model = next(
        (item for item in list_models() if item.model_id == body.model_id and item.is_available), None
    )
    if not offline_demo and (model is None or model.provider not in {"deepseek", "openai"}):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "OPTION_UNAVAILABLE",
                "message": "Select an available real model for live claim analysis.",
                "debug_id": None,
            },
        )
    created_at = _now()
    assessment_id = f"cred_{uuid.uuid4().hex}"
    source = _normalize_source(body.source.model_dump(mode="json"), validate=not offline_demo)
    queued = CredibilityAssessment.model_validate(
        {
            "schema_version": body.schema_version,
            "analysis_mode": "credibility",
            "assessment_id": assessment_id,
            "data_origin": "synthetic" if offline_demo else "live",
            "source": source,
            "model_id": body.model_id,
            "status": "queued",
            "stage": "queued",
            "progress_percent": 0,
            "created_at": created_at,
            "started_at": None,
            "completed_at": None,
            "paper": None,
            "report": None,
            "warnings": [],
            "error": None,
        }
    ).model_dump(mode="json")
    versions = runtime.request_versions()
    fingerprint = runtime.request_fingerprint(source, body.model_id, body.rubric_version, versions)
    runtime.get_task_store().create_task(queued, fingerprint, {**versions, "rubric_version": body.rubric_version})
    _TASKS[assessment_id] = copy.deepcopy(queued)
    background.add_task(
        _run, assessment_id, body.model_id, copy.deepcopy(source), created_at, body.rubric_version, owner=API_OWNER,
    )
    return queued


@router.get("/v1/credibility/assessments")
def list_credibility_assessments(
    search: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Local completed and failed checks, newest first; same paging shape as /v1/assessments."""
    needle = search.strip().casefold()
    items = []
    for task in runtime.get_task_store().list_tasks(["completed", "failed"]):
        paper = task.get("paper") or {}
        overall = (task.get("report") or {}).get("overall") or {}
        source = task.get("source") or {}
        title = paper.get("title")
        if needle and needle not in " ".join(
            str(value or "") for value in (title, paper.get("arxiv_id"), source.get("arxiv_id"))
        ).casefold():
            continue
        items.append({
            "assessment_id": task["assessment_id"],
            "status": task["status"],
            "title": title,
            "arxiv_id": paper.get("arxiv_id") or source.get("arxiv_id"),
            "source_type": source.get("type"),
            "model_id": task.get("model_id"),
            "overall_score": overall.get("score"),
            "category": overall.get("category"),
            "error_code": (task.get("error") or {}).get("code"),
            "created_at": task.get("created_at"),
            "completed_at": task.get("completed_at"),
        })
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}


@router.get("/v1/credibility/assessments/{assessment_id}", response_model=CredibilityAssessmentResponse)
def get_credibility_assessment(assessment_id: str) -> dict:
    task = runtime.get_task_store().load_task(assessment_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ASSESSMENT_NOT_FOUND", "message": "Credibility assessment not found.", "debug_id": None},
        )
    return copy.deepcopy(task)
