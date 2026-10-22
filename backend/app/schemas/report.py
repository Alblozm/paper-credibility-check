"""契约 §10–§16 报告结构。

这里只做「结构合法性」校验；跨对象的一致性（悬空 evidence、总分计算、
降级规则）在 app.validation 里做，因为那些需要整份报告的上下文。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.common import EvidenceLink, EvidenceReference, StrictModel
from app.schemas.enums import (
    CHECKLIST_IDS,
    DIMENSION_IDS,
    Applicability,
    ChecklistStatus,
    Confidence,
    ContributionType,
    Importance,
    Rating,
    ReadingRecommendation,
    SupportLevel,
)

_NUMERIC_RATINGS = {Rating.STRONG, Rating.GOOD, Rating.ADEQUATE, Rating.WEAK}


class DimensionAssessment(StrictModel):
    """契约 §10.2。applicability 三态与 score / rating / uncertainty_reason 的组合是硬约束。"""

    dimension_id: str
    display_name: str
    applicability: Applicability
    score: int | None = None
    rating: Rating
    weight: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)
    confidence: Confidence
    uncertainty_reason: str | None = None
    evidence_links: list[EvidenceLink] = []

    @model_validator(mode="after")
    def _check_combination(self) -> DimensionAssessment:
        if self.dimension_id not in DIMENSION_IDS:
            raise ValueError(f"unknown dimension_id: {self.dimension_id}")

        if self.applicability is Applicability.APPLICABLE:
            if self.score is None or not (1 <= self.score <= 10):
                raise ValueError("applicable dimension requires an integer score in 1..10")
            if self.rating not in _NUMERIC_RATINGS:
                raise ValueError("applicable dimension requires a numeric rating")
            if self.uncertainty_reason is not None:
                raise ValueError("applicable dimension must have uncertainty_reason = null")

        elif self.applicability is Applicability.UNCERTAIN:
            if self.score is not None:
                raise ValueError("uncertain dimension must have score = null")
            if self.rating is not Rating.UNCERTAIN:
                raise ValueError("uncertain dimension must have rating = 'uncertain'")
            if not (self.uncertainty_reason or "").strip():
                raise ValueError("uncertain dimension requires a non-empty uncertainty_reason")

        else:  # NOT_APPLICABLE
            if self.score is not None:
                raise ValueError("not_applicable dimension must have score = null")
            if self.rating is not Rating.NOT_APPLICABLE:
                raise ValueError("not_applicable dimension must have rating = 'not_applicable'")
            if self.uncertainty_reason is not None:
                raise ValueError("not_applicable dimension must have uncertainty_reason = null")
            if not self.summary.strip():
                raise ValueError("not_applicable dimension must explain why in summary")

        return self


class ClaimedContribution(StrictModel):
    """契约 §12.1。"""

    contribution_id: str
    statement: str = Field(min_length=1)
    contribution_type: ContributionType
    evidence_links: list[EvidenceLink] = []


class ClaimCheck(StrictModel):
    """契约 §12.2。作者的 SOTA 声称也走这里，不设独立模块。"""

    claim_id: str
    claim: str = Field(min_length=1)
    support_level: SupportLevel
    reasoning: str = Field(min_length=1)
    confidence: Confidence
    evidence_links: list[EvidenceLink] = []


class ReproducibilityCheck(StrictModel):
    """契约 §13.2。"""

    check_id: str
    display_name: str
    status: ChecklistStatus
    detail: str = Field(min_length=1)
    evidence_links: list[EvidenceLink] = []

    @model_validator(mode="after")
    def _known_check(self) -> ReproducibilityCheck:
        if self.check_id not in CHECKLIST_IDS:
            raise ValueError(f"unknown check_id: {self.check_id}")
        return self


class Finding(StrictModel):
    """契约 §14.1。strengths 与 concerns 共用同一结构。"""

    finding_id: str
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    importance: Importance
    confidence: Confidence
    evidence_links: list[EvidenceLink] = []


class UnresolvedQuestion(StrictModel):
    """契约 §14.2。"""

    question_id: str
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    evidence_links: list[EvidenceLink] = []


class OverallCalculation(StrictModel):
    method: str = "weighted_average"
    assessed_dimension_count: int = Field(ge=0, le=5)
    excluded_dimension_ids: list[str] = []


class OverallAssessment(StrictModel):
    """契约 §16。overall_score / rating / is_provisional 一律由后端计算，不采信模型。"""

    overall_score: float | None = None
    rating: Rating
    summary: str = Field(min_length=1)
    reading_recommendation: ReadingRecommendation
    recommendation_reason: str = Field(min_length=1)
    confidence: Confidence
    is_provisional: bool
    calculation: OverallCalculation

    @model_validator(mode="after")
    def _score_rating_consistency(self) -> OverallAssessment:
        if self.overall_score is None and self.rating is not Rating.UNABLE_TO_ASSESS:
            raise ValueError("null overall_score requires rating = 'unable_to_assess'")
        if self.overall_score is not None and not (1.0 <= self.overall_score <= 10.0):
            raise ValueError("overall_score must be within 1.0..10.0 or null")
        return self


class Report(StrictModel):
    """契约 §11 报告顶层结构。"""

    paper_summary: str = Field(min_length=1)
    claimed_contributions: list[ClaimedContribution] = []
    claim_checks: list[ClaimCheck] = []
    dimensions: list[DimensionAssessment]
    reproducibility_checklist: list[ReproducibilityCheck]
    strengths: list[Finding] = Field(max_length=5)
    concerns: list[Finding] = Field(max_length=5)
    unresolved_questions: list[UnresolvedQuestion] = Field(default=[], max_length=3)
    overall_assessment: OverallAssessment
    evidence: list[EvidenceReference] = []
    limitations: list[str] = []
    generated_at: str

    @model_validator(mode="after")
    def _fixed_arrays(self) -> Report:
        """契约 §11：dimensions 恒为 5、checklist 恒为 6。

        缺的用 uncertain / unable_to_verify 填，不允许少给——这样下游只有一条规则。
        """
        got_dims = [d.dimension_id for d in self.dimensions]
        if got_dims != list(DIMENSION_IDS):
            raise ValueError(
                f"dimensions must be exactly {list(DIMENSION_IDS)} in order, got {got_dims}"
            )
        got_checks = [c.check_id for c in self.reproducibility_checklist]
        if sorted(got_checks) != sorted(CHECKLIST_IDS):
            raise ValueError(
                f"reproducibility_checklist must contain exactly {list(CHECKLIST_IDS)}, "
                f"got {got_checks}"
            )
        return self
