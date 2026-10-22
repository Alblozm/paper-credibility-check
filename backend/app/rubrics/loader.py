"""Load one immutable, versioned scoring rubric from configuration."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas.enums import DIMENSION_IDS


class RubricLoadError(RuntimeError):
    """The configured rubric is absent, malformed, or inconsistent."""


class RubricDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: str
    weight: float = Field(gt=0, le=1)


class RubricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_id: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    score_type: Literal["integer"]
    score_min: int
    score_max: int
    dimensions: list[RubricDimension]
    applicability_values: list[str]
    confidence_values: list[str]
    notes: list[str] = []

    @model_validator(mode="after")
    def validate_contract(self) -> RubricDefinition:
        ids = [dimension.dimension_id for dimension in self.dimensions]
        if ids != list(DIMENSION_IDS):
            raise ValueError(f"dimensions must be exactly {list(DIMENSION_IDS)} in order")
        if abs(sum(dimension.weight for dimension in self.dimensions) - 1.0) > 1e-9:
            raise ValueError("dimension weights must sum to 1.0")
        if (self.score_min, self.score_max) != (1, 10):
            raise ValueError("the v0.4 report contract requires score range 1..10")
        if self.applicability_values != ["applicable", "uncertain", "not_applicable"]:
            raise ValueError("applicability values do not match the report contract")
        if self.confidence_values != ["high", "medium", "low"]:
            raise ValueError("confidence values do not match the report contract")
        return self


@dataclass(frozen=True)
class LoadedRubric:
    definition: RubricDefinition
    scoring_guide: str
    content_sha256: str
    directory: Path

    @property
    def rubric_id(self) -> str:
        return self.definition.rubric_id

    @property
    def rubric_version(self) -> str:
        return self.definition.rubric_version


@lru_cache(maxsize=16)
def load_rubric(
    directory: str,
    expected_id: str | None = None,
    expected_version: str | None = None,
) -> LoadedRubric:
    root = Path(directory).expanduser().resolve()
    definition_path = root / "rubric.yaml"
    guide_path = root / "scoring_guide.md"
    try:
        definition_bytes = definition_path.read_bytes()
        guide_bytes = guide_path.read_bytes()
    except OSError as exc:
        raise RubricLoadError(f"Rubric 文件缺失或不可读：{root}") from exc

    try:
        raw = yaml.safe_load(definition_bytes)
        definition = RubricDefinition.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise RubricLoadError(f"rubric.yaml 校验失败：{exc}") from exc

    guide = guide_bytes.decode("utf-8").strip()
    if not guide:
        raise RubricLoadError("scoring_guide.md 不能为空")
    if expected_id and definition.rubric_id != expected_id:
        raise RubricLoadError(
            f"Rubric ID 不一致：配置为 {expected_id}，文件为 {definition.rubric_id}"
        )
    if expected_version and definition.rubric_version != expected_version:
        raise RubricLoadError(
            f"Rubric 版本不一致：配置为 {expected_version}，文件为 {definition.rubric_version}"
        )

    digest = hashlib.sha256(definition_bytes + b"\0" + guide_bytes).hexdigest()
    return LoadedRubric(definition, guide, digest, root)
