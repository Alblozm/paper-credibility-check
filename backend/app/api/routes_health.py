from fastapi import APIRouter

from app.config import get_settings
from app.llm.prompts import get_active_rubric
from app.schemas.enums import SCHEMA_VERSION

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict:
    s = get_settings()
    rubric = get_active_rubric()
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "rubric_version": rubric.rubric_version,
        "rubric_sha256": rubric.content_sha256,
        "llm_provider": s.llm_provider,
        "parser_provider": s.parser_provider,
        "task_runner": s.task_runner,
    }
