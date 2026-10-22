"""契约 §6 / §7：venue 与模型选项由后端提供，前端不得写死第二份列表。"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.llm.prompts import get_active_rubric
from app.schemas.assessment import ModelOption, VenueOption

router = APIRouter(prefix="/v1", tags=["options"])


@router.get("/venues", response_model=list[VenueOption])
def list_venues() -> list[VenueOption]:
    """MVP 只返回一个可用的 ICLR 选项，但保留可扩展结构。"""
    rubric = get_active_rubric()
    return [
        VenueOption(
            venue_id="iclr_2027_main",
            code="ICLR",
            name="International Conference on Learning Representations",
            year=2027,
            track="main",
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            is_available=True,
        )
    ]


@router.get("/models", response_model=list[ModelOption])
def list_models() -> list[ModelOption]:
    """契约 §7.3：未验证 API、权限和最小样本调用前，不得返回 is_available=true。"""
    s = get_settings()
    deepseek_ready = s.deepseek_enabled and bool(s.deepseek_api_key)
    openai_ready = bool(s.openai_api_key)

    options = [
        ModelOption(
            model_id="model_standard_v1",
            display_name="DeepSeek Review Model",
            provider="deepseek",
            model_version=s.deepseek_model,
            description="Lower-cost model used for internal testing.",
            speed_tier="standard",
            is_available=deepseek_ready,
            is_default=deepseek_ready and s.llm_provider == "deepseek",
        ),
        ModelOption(
            model_id="model_gpt_v1",
            display_name="GPT Review Model",
            provider="openai",
            model_version=s.openai_model,
            description="OpenAI GPT analysis and tool-using agents.",
            speed_tier="standard",
            is_available=openai_ready,
            is_default=openai_ready and s.llm_provider == "openai",
        ),
    ]
    if s.llm_provider == "fixture":
        options.append(
            ModelOption(
                model_id="model_fixture_v1",
                display_name="Demo Model (fixture)",
                provider="internal",
                model_version="fixture-2026-08-30",
                description="Returns a fixed sample report. Development only.",
                speed_tier="instant",
                is_available=True,
                is_default=True,
            )
        )
    return options
