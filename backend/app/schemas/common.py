"""契约 §15 Evidence 两层结构。

关键点：`stance` 只属于 EvidenceLink，不属于 EvidenceReference。
同一条证据可以被多个判断引用，并在不同判断中取不同 stance。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import Confidence, EvidenceMode, LocatorType, Stance


class StrictModel(BaseModel):
    """全项目基类：禁止契约之外的字段，避免同义不同名悄悄混进来。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=False, validate_assignment=True)


class EvidenceReference(StrictModel):
    """证据是什么、位于论文哪里。契约 §15.1。"""

    evidence_id: str
    page_number: int | None = None
    section_id: str | None = None
    section_title: str | None = None
    locator_type: LocatorType
    locator_label: str | None = None
    evidence_mode: EvidenceMode
    excerpt: str = Field(min_length=1)
    location_confidence: Confidence

    @field_validator("page_number")
    @classmethod
    def _page_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("page_number must be >= 1 or null")
        return v


class EvidenceLink(StrictModel):
    """判断与证据之间的关系。契约 §15.2。"""

    evidence_id: str
    stance: Stance
    reason: str = Field(min_length=1)
