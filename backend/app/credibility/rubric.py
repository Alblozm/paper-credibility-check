"""Versioned credibility rules, including the delivered 0.2 provisional policy.

未填的项保持 None：draft 下维度分全部清空；approved 但没有类别分界时 category 为 null；
没有低分阈值时不发 low_dimension_score 提醒。0.2 试验分不代表正式批准。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

Category = Literal["no_significant_concerns", "some_concerns", "significant_concerns", "critical_concerns"]
RubricVersion = Literal["0.1-draft", "0.2-provisional-complete"]
PROVISIONAL_VERSION = "0.2-provisional-complete"


@dataclass(frozen=True)
class CredibilityRubric:
    rubric_id: Literal["paper_credibility"] = "paper_credibility"
    rubric_version: RubricVersion = "0.1-draft"
    status: Literal["draft", "provisional", "approved"] = "draft"
    # 从高到低的 (下限, 类别)，总分 >= 下限即落入该类别。
    category_thresholds: tuple[tuple[float, Category], ...] | None = None
    low_dimension_threshold: float | None = None
    claim_scores: dict[str, float] = field(default_factory=dict)
    reference_scores: dict[str, float] = field(default_factory=dict)
    code_scores: dict[str, float] = field(default_factory=dict)
    technical_fallback_score: float = 3
    code_score_cap: float = 9
    content_sha256: str | None = None

    @property
    def can_score(self) -> bool:
        return self.status in {"approved", "provisional"}

    def category_for(self, score: float) -> Category | None:
        if not self.can_score or not self.category_thresholds:
            return None
        for floor, category in self.category_thresholds:
            if score >= floor:
                return category
        return None


DRAFT_RUBRIC = CredibilityRubric()


@lru_cache
def load_credibility_rubric(version: str = PROVISIONAL_VERSION) -> CredibilityRubric:
    """Load the delivered 0.2 trial policy; an explicit 0.1 request stays unscored."""
    if version == "0.1-draft":
        return DRAFT_RUBRIC
    if version != PROVISIONAL_VERSION:
        raise ValueError(f"Unsupported credibility rubric: {version}")
    raw = (Path(__file__).with_name("rubrics") / "rubric.provisional-ai-batch.json").read_bytes()
    data = json.loads(raw)
    if (data.get("rubric_id"), data.get("version"), data.get("status")) != (
        "paper_credibility", PROVISIONAL_VERSION, "provisional",
    ):
        raise ValueError("Invalid provisional credibility rubric identity")
    expected = {
        "claim_scores": {"supported", "partially_supported", "evidence_not_found", "contradicted"},
        "reference_scores": {"matched", "metadata_conflict", "unverified"},
        "code_scores": {
            "official_code_available", "independent_success_report", "failure_or_data_problem_report",
            "discussion_inconclusive", "no_relevant_public_feedback", "no_public_code_found",
        },
    }
    for name, keys in expected.items():
        mapping = data.get(name)
        if not isinstance(mapping, dict) or set(mapping) != keys:
            raise ValueError(f"Incomplete credibility rule mapping: {name}")
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 10 for v in mapping.values()):
            raise ValueError(f"Invalid credibility rule scores: {name}")
    if data.get("weights") != {
        "claim_evidence_consistency": 1, "reference_verification": 1, "code_reproduction_signals": 1,
    } or data.get("reference_aggregation") != "mean" or data.get("code_aggregation") != "min":
        raise ValueError("Unsupported credibility aggregation policy")
    boundaries = data.get("categories", [])
    categories = {"critical_concerns", "significant_concerns", "some_concerns", "no_significant_concerns"}
    if [row.get("lower_bound") for row in boundaries] != [0, 3, 6, 8] or {
        row.get("category") for row in boundaries
    } != categories or data.get("code_score_cap") != 9 or data.get("low_score_threshold") != 6:
        raise ValueError("Invalid provisional credibility thresholds")
    return CredibilityRubric(
        rubric_version=PROVISIONAL_VERSION, status="provisional",
        category_thresholds=tuple((row["lower_bound"], row["category"]) for row in reversed(boundaries)),
        low_dimension_threshold=data["low_score_threshold"],
        claim_scores=data["claim_scores"], reference_scores=data["reference_scores"],
        code_scores=data["code_scores"], code_score_cap=data["code_score_cap"],
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
