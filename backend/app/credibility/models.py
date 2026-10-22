"""可信度核查草稿契约。

独立于五维评分（schema 0.4）的 Report / DimensionAssessment。
字段对齐前端 credibility.ts 的请求、任务、报告、三项检查、证据和总分。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.credibility.rubric import PROVISIONAL_VERSION, RubricVersion
from app.schemas.common import StrictModel

CREDIBILITY_SCHEMA_VERSION = "credibility-0.5.0-draft"
CREDIBILITY_RUBRIC_VERSION = "0.1-draft"
CREDIBILITY_RUBRIC_ID = "paper_credibility"

CredibilityDimensionId = Literal["claim_evidence", "reference_verification", "code_reproduction"]
_DIMENSION_ORDER = ("claim_evidence", "reference_verification", "code_reproduction")


class CredibilityArxivSource(StrictModel):
    type: Literal["arxiv"]
    arxiv_id: str = Field(min_length=1)
    upload_id: None


class CredibilityPdfSource(StrictModel):
    type: Literal["pdf"]
    arxiv_id: None
    upload_id: str = Field(min_length=1)


CredibilitySource = Annotated[
    CredibilityArxivSource | CredibilityPdfSource,
    Field(discriminator="type"),
]


class CredibilityArxivSourceInput(StrictModel):
    """Request shape shared with the five-dimension route: an ID or an arxiv.org link.

    The route normalizes it to CredibilityArxivSource, so stored task identities are unchanged.
    """

    type: Literal["arxiv"]
    arxiv_id: str | None = Field(default=None, min_length=1)
    original_url: str | None = None
    upload_id: None = None

    @model_validator(mode="after")
    def require_identifier(self) -> CredibilityArxivSourceInput:
        if not self.arxiv_id and not self.original_url:
            raise ValueError("An arXiv source needs arxiv_id or original_url")
        return self


class CredibilityPdfSourceInput(StrictModel):
    type: Literal["pdf"]
    arxiv_id: None = None
    original_url: None = None
    upload_id: str = Field(min_length=1)


CredibilitySourceInput = Annotated[
    CredibilityArxivSourceInput | CredibilityPdfSourceInput,
    Field(discriminator="type"),
]


class CreateCredibilityRequest(StrictModel):
    schema_version: Literal[
        "credibility-0.1.0-draft", "credibility-0.2.0-draft", "credibility-0.3.0-draft", "credibility-0.4.0-draft",
        "credibility-0.5.0-draft",
    ] = CREDIBILITY_SCHEMA_VERSION
    analysis_mode: Literal["credibility"]
    source: CredibilitySourceInput
    model_id: str = Field(min_length=1)
    rubric_version: RubricVersion = PROVISIONAL_VERSION


class CredibilityPaper(StrictModel):
    """论文标识。

    前端草稿把 source_sha256 和 page_count 写成必填。外壳在未知时仍允许 null，
    不编造哈希、页数或标题。
    """

    paper_id: str
    title: str | None
    arxiv_id: str | None
    source_sha256: str | None
    page_count: int | None
    language: str | None
    parse_quality: Literal["good", "partial", "poor"]


class CredibilityError(StrictModel):
    code: str
    message: str
    retryable: bool
    debug_id: str | None


class CredibilityNotice(StrictModel):
    code: str
    message: str
    dimension_id: CredibilityDimensionId | None


class CredibilityModelCall(StrictModel):
    purpose: str
    provider: str
    model_version: str


class CredibilityVersions(StrictModel):
    pipeline_version: str
    parser_version: str
    prompt_version: str
    model_calls: list[CredibilityModelCall]


class CredibilityDimension(StrictModel):
    dimension_id: CredibilityDimensionId
    applicability: Literal["applicable", "not_applicable", "undetermined"]
    applicability_reason: str
    check_status: Literal["not_run", "running", "completed", "partial", "failed"]
    score_status: Literal["pending", "scored", "unable_to_assess", "not_applicable"]
    score: float | None = Field(ge=0, le=10)
    weight: float = Field(gt=0)
    score_rule_ids: list[str]
    summary: str
    unassessed_reason: str | None
    evidence_ids: list[str]
    finding_ids: list[str]


class CredibilityPaperLocator(StrictModel):
    page_start: int
    page_end: int
    chunk_id: str
    section_title: str | None
    element_label: str | None


class CredibilityEvidence(StrictModel):
    evidence_id: str
    origin: Literal["paper", "external"]
    mode: Literal["quote", "paraphrase"]
    excerpt: str
    paper_locator: CredibilityPaperLocator | None
    source_id: str | None
    location_confidence: Literal["high", "medium", "low"]


class CredibilityExternalSource(StrictModel):
    source_id: str
    kind: Literal[
        "metadata",
        "supplement",
        "project_page",
        "repository",
        "discussion_listing",
        "issue",
        "comment",
    ]
    url: str
    fetch_status: Literal["ok", "not_found", "access_denied", "timeout", "rate_limited", "error"]
    http_status: int | None
    retrieved_at: str
    observed_at: str
    is_cached: bool
    snapshot_ref: str | None
    association_reason: str


class CredibilityFinding(StrictModel):
    finding_id: str
    dimension_id: CredibilityDimensionId
    item_ids: list[str]
    kind: Literal["concern", "verification_gap", "context"]
    severity: Literal["info", "minor", "moderate", "major", "critical"]
    certainty: Literal["confirmed", "reported", "unresolved"]
    title: str
    detail: str
    evidence_ids: list[str]
    source_ids: list[str]
    limitations: list[str]


class CredibilityAlert(StrictModel):
    type: Literal["low_dimension_score", "material_finding", "assessment_gap"]
    dimension_id: CredibilityDimensionId
    finding_ids: list[str]
    message: str


class CredibilityClaimReconciliation(StrictModel):
    """Original judgment retained when code evidence resolves an artifact claim."""

    outcome: Literal["partially_supported", "not_found", "unable_to_determine"]
    reason: str
    comparison_evidence_ids: list[str]
    search_scope: str
    score_rule_id: str | None
    item_score: float | None = Field(ge=0, le=10)
    findings: list[CredibilityFinding] = Field(default_factory=list)
    repository_ids: list[str] = Field(min_length=1)


class CredibilityClaimItem(StrictModel):
    claim_id: str
    statement: str
    importance: Literal["core", "supporting"]
    conditions: str
    claim_evidence_ids: list[str]
    comparison_evidence_ids: list[str]
    search_scope: str
    outcome: Literal[
        "supported",
        "partially_supported",
        "not_found",
        "contradicted",
        "unable_to_determine",
        "not_checked",
    ]
    reason: str
    score_rule_id: str | None
    item_score: float | None = Field(ge=0, le=10)
    reconciled_from: CredibilityClaimReconciliation | None = None


class CredibilityClaimChecks(StrictModel):
    selection_scope: str
    scope_complete: bool
    scope_limitations: list[str]
    items: list[CredibilityClaimItem]


class CredibilityBibliographicMetadata(StrictModel):
    title: str | None
    authors: list[str]
    year: int | None
    doi: str | None
    arxiv_id: str | None


class CredibilityReferenceCandidate(StrictModel):
    candidate_id: str
    metadata: CredibilityBibliographicMetadata
    source_id: str
    evidence_ids: list[str]
    match_basis: str


class CredibilityReferenceItem(StrictModel):
    reference_id: str
    raw_text: str
    paper_evidence_ids: list[str]
    parsed: CredibilityBibliographicMetadata
    outcome: Literal["matched", "metadata_conflict", "unverified", "not_checked"]
    reason: str
    source_ids: list[str]
    candidates: list[CredibilityReferenceCandidate]
    selected_candidate_id: str | None
    score_rule_id: str | None
    item_score: float | None = Field(ge=0, le=10)


class CredibilityReferenceChecks(StrictModel):
    """引用核查。

    extraction_status 与前端一致，只有 complete、partial、failed、not_applicable。
    未运行时维度 check_status 仍是 not_run。extraction_status 用 failed，
    extraction_reason 以 “Not run:” 开头，说明这不是查询失败。
    """

    extraction_status: Literal["complete", "partial", "failed", "not_applicable"]
    extraction_reason: str
    original_total: int | None
    extracted_count: int
    checked_count: int
    matched_count: int
    conflict_count: int
    unverified_count: int
    not_checked_count: int
    query_completion_ratio: float | None
    extraction_coverage_ratio: float | None
    gaps: list[str]
    items: list[CredibilityReferenceItem]


ReferenceUnresolvedReasonCode = Literal[
    "target_not_found_in_scope", "year_mismatch", "author_mismatch", "version_evidence_missing",
    "source_fetch_failed", "insufficient_metadata", "personal_communication", "extraction_gap",
    "metadata_conflict", "budget_exhausted", "identifier_mismatch", "invalid_arxiv_id", "title_mismatch",
    "not_checked", "undetermined_reason",
]


class CredibilityUnresolvedReference(StrictModel):
    reference_id: str
    outcome: Literal["metadata_conflict", "unverified", "not_checked"]
    reason_code: ReferenceUnresolvedReasonCode
    reason: str
    source_ids: list[str]


class CredibilityReferenceVerificationSummary(StrictModel):
    verification_rate: float | None = Field(ge=0, le=1)
    denominator: Literal["extracted_references"] = "extracted_references"
    extracted_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    unverified_count: int = Field(ge=0)
    not_checked_count: int = Field(ge=0)
    metadata_conflict_count: int = Field(ge=0)
    extraction_complete: bool
    unresolved_items: list[CredibilityUnresolvedReference]


class CredibilityCodeSearch(StrictModel):
    scope_id: str
    scope_type: Literal[
        "paper_body",
        "appendix",
        "supplement",
        "project_page",
        "repository",
        "discussion_list",
    ]
    outcome: Literal["checked", "not_present", "not_applicable", "failed", "not_checked"]
    reason: str
    evidence_ids: list[str]
    source_ids: list[str]


class CredibilityRepository(StrictModel):
    repository_id: str
    source_id: str
    association: Literal["confirmed", "uncertain", "rejected"]
    association_reason: str
    association_evidence_ids: list[str]
    availability: Literal["accessible", "inaccessible", "unknown"]
    implementation_evidence_ids: list[str]
    inspected_revision: str | None


class CredibilityDiscussion(StrictModel):
    discussion_id: str
    repository_id: str
    source_ids: list[str]
    evidence_ids: list[str]
    signal: Literal["success_reported", "failure_reported", "discussion_only"]
    relevance: Literal["relevant", "irrelevant", "undetermined"]
    independence: Literal["independent", "author", "unknown"]
    conditions: str
    resolution: Literal["open", "resolved", "disputed", "unknown"]
    author_response_source_ids: list[str]
    scoring_eligible: bool
    reason: str


class CredibilityCodeChecks(StrictModel):
    scope_complete: bool
    scope_limitations: list[str]
    searches: list[CredibilityCodeSearch]
    code_availability: Literal["available", "not_found_in_scope", "unable_to_verify", "not_applicable"]
    reproduction_signal: Literal[
        "success_reported",
        "failure_reported",
        "discussion_only",
        "no_feedback_found",
        "unable_to_verify",
        "not_applicable",
    ]
    repositories: list[CredibilityRepository]
    discussions: list[CredibilityDiscussion]
    base_score_rule_id: str | None
    score_rule_id: str | None


class CredibilityCodeDiscoveryBudget(StrictModel):
    """Discovery-only usage; the shared source-read cap also covers later checks."""

    max_queries: int = Field(default=0, ge=0)
    queries_used: int = Field(default=0, ge=0)
    max_candidates: int = Field(default=0, ge=0)
    candidates_inspected: int = Field(default=0, ge=0)
    max_source_reads: int = Field(default=0, ge=0)
    source_reads_used: int = Field(default=0, ge=0)
    max_model_rounds: int = Field(default=0, ge=0)
    model_rounds_used: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)


class CredibilityCodeDiscoveryQuery(StrictModel):
    query_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    query: str = Field(min_length=1)
    status: Literal["completed", "partial", "failed", "not_run"]
    result_count: int = Field(default=0, ge=0)
    source_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class CredibilityCodeDiscoveryCandidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str | None = None
    resource_kind: Literal[
        "implementation_candidate", "official_implementation", "third_party_reproduction",
        "third_party_related", "reading_list", "undetermined",
    ] = "undetermined"
    status: Literal["candidate", "verified", "rejected"] = "candidate"
    repository_id: str | None = None
    query_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def _verified_official_needs_proof(self) -> CredibilityCodeDiscoveryCandidate:
        if self.resource_kind == "official_implementation" and self.status == "verified":
            if not self.repository_id or not self.source_ids or not self.evidence_ids:
                raise ValueError("Verified official implementation needs a repository and actual source/evidence IDs")
        return self


class CredibilityCodeDiscovery(StrictModel):
    """Search results are candidates, kept outside the frozen code score."""

    status: Literal["not_run", "disabled", "not_needed", "completed", "partial", "failed"] = "not_run"
    reason: str = "Automatic external code discovery was not run in this task."
    scope_complete: Literal[False] = False
    queries: list[CredibilityCodeDiscoveryQuery] = Field(default_factory=list)
    candidates: list[CredibilityCodeDiscoveryCandidate] = Field(default_factory=list)
    budget: CredibilityCodeDiscoveryBudget = Field(default_factory=CredibilityCodeDiscoveryBudget)
    limitations: list[str] = Field(default_factory=list)
    audit_ref: str | None = None


class CredibilityOverallCalculation(StrictModel):
    method: Literal["equal_weight_applicable_dimensions", "equal_weight_trial_dimensions"]
    included_dimension_ids: list[CredibilityDimensionId]
    excluded_dimension_ids: list[CredibilityDimensionId]
    blocking_dimension_ids: list[CredibilityDimensionId]
    assessed_dimension_count: int
    fallback_dimension_ids: list[CredibilityDimensionId] = []


class CredibilityOverall(StrictModel):
    status: Literal["assessed", "unable_to_assess"]
    score: float | None = Field(ge=0, le=10)
    category: (
        Literal[
            "no_significant_concerns",
            "some_concerns",
            "significant_concerns",
            "critical_concerns",
        ]
        | None
    )
    summary: str
    unassessed_reason: str | None
    calculation: CredibilityOverallCalculation


class CredibilityChecks(StrictModel):
    claims: CredibilityClaimChecks
    references: CredibilityReferenceChecks
    code: CredibilityCodeChecks


class CredibilityClaimItemBeforeReconciliation(CredibilityClaimItem):
    reconciled_from: CredibilityClaimReconciliation | None = Field(default=None, exclude=True)


class CredibilityClaimChecksBeforeReconciliation(CredibilityClaimChecks):
    items: list[CredibilityClaimItemBeforeReconciliation]


class CredibilityChecksBeforeReconciliation(CredibilityChecks):
    claims: CredibilityClaimChecksBeforeReconciliation


class CredibilityCoverage(StrictModel):
    """Execution quality, kept separate from the frozen trial score."""

    dimension_id: CredibilityDimensionId
    check_status: str
    scope_complete: bool
    population: Literal["selected_claims", "extracted_references", "bounded_repository_search"]
    inspected_count: int = Field(ge=0)
    total_count: int | None = Field(default=None, ge=0)
    completion_ratio: float | None = Field(default=None, ge=0, le=1)
    extraction_coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    outcomes: dict[str, int]
    confirmed_concern_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    technical_failure_count: int = Field(ge=0)
    fallback_rule_ids: list[str]
    limitations: list[str]


class CredibilityReport(StrictModel):
    report_id: str
    rubric_id: Literal["paper_credibility"]
    rubric_version: RubricVersion
    rubric_status: Literal["draft", "provisional", "approved"]
    rubric_sha256: str | None = None
    scope_complete: bool = False
    is_provisional: bool
    versions: CredibilityVersions
    generated_at: str
    dimensions: tuple[CredibilityDimension, CredibilityDimension, CredibilityDimension]
    checks: CredibilityChecks
    evidence: list[CredibilityEvidence]
    sources: list[CredibilityExternalSource]
    findings: list[CredibilityFinding]
    overall: CredibilityOverall
    alerts: list[CredibilityAlert]
    limitations: list[str]
    coverage: list[CredibilityCoverage] = []
    execution: dict = {}
    external_review: dict | None = None
    reference_verification_summary: CredibilityReferenceVerificationSummary
    code_discovery: CredibilityCodeDiscovery = Field(default_factory=CredibilityCodeDiscovery)

    @model_validator(mode="before")
    @classmethod
    def _derive_reference_summary_for_saved_reports(cls, value):
        """Read old snapshots without fetching sources or changing their scores."""
        if isinstance(value, dict) and "reference_verification_summary" not in value:
            checks = value.get("checks")
            references = checks.references if isinstance(checks, CredibilityChecks) else (
                checks.get("references") if isinstance(checks, dict) else None
            )
            if references is not None:
                from app.credibility.coverage import build_reference_summary

                value = dict(value)
                value["reference_verification_summary"] = build_reference_summary(
                    CredibilityReferenceChecks.model_validate(references),
                )
        return value

    @model_validator(mode="after")
    def _dimension_order(self) -> CredibilityReport:
        actual = tuple(item.dimension_id for item in self.dimensions)
        if actual != _DIMENSION_ORDER:
            raise ValueError("dimensions must be claim_evidence, reference_verification, code_reproduction")
        return self


class CredibilityAssessment(StrictModel):
    schema_version: Literal[
        "credibility-0.1.0-draft", "credibility-0.2.0-draft", "credibility-0.3.0-draft", "credibility-0.4.0-draft",
        "credibility-0.5.0-draft",
    ]
    analysis_mode: Literal["credibility"]
    assessment_id: str
    data_origin: Literal["live", "synthetic"]
    source: CredibilitySource
    model_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    stage: Literal["queued", "retrieving", "parsing", "checking", "finalizing", "done", "failed"]
    progress_percent: int
    created_at: str
    started_at: str | None
    completed_at: str | None
    paper: CredibilityPaper | None
    report: CredibilityReport | None
    warnings: list[CredibilityNotice]
    error: CredibilityError | None


class CredibilityReportBeforeDiscovery(CredibilityReport):
    """Preserve explicit 0.1–0.4 JSON shapes while retaining internal metadata."""

    code_discovery: CredibilityCodeDiscovery = Field(default_factory=CredibilityCodeDiscovery, exclude=True)
    checks: CredibilityChecksBeforeReconciliation


class LegacyCredibilityReport(CredibilityReportBeforeDiscovery):
    """Serialize the original report shape for explicit 0.1 API consumers."""

    coverage: list[CredibilityCoverage] = Field(default=[], exclude=True)
    execution: dict = Field(default={}, exclude=True)
    external_review: dict | None = Field(default=None, exclude=True)
    reference_verification_summary: CredibilityReferenceVerificationSummary = Field(exclude=True)


class CredibilityReportV2(CredibilityReportBeforeDiscovery):
    """Serialize the original 0.2 report shape, without the 0.3 summary."""

    reference_verification_summary: CredibilityReferenceVerificationSummary = Field(exclude=True)


class LegacyCredibilityAssessment(CredibilityAssessment):
    schema_version: Literal["credibility-0.1.0-draft"]
    report: LegacyCredibilityReport | None


class CredibilityAssessmentV2(CredibilityAssessment):
    schema_version: Literal["credibility-0.2.0-draft"]
    report: CredibilityReportV2 | None


class CredibilityAssessmentV3(CredibilityAssessment):
    schema_version: Literal["credibility-0.3.0-draft"]
    report: CredibilityReportBeforeDiscovery | None


class CredibilityAssessmentV4(CredibilityAssessment):
    schema_version: Literal["credibility-0.4.0-draft"]
    report: CredibilityReportBeforeDiscovery | None


class CredibilityAssessmentV5(CredibilityAssessment):
    schema_version: Literal["credibility-0.5.0-draft"]


CredibilityAssessmentResponse = Annotated[
    LegacyCredibilityAssessment | CredibilityAssessmentV2 | CredibilityAssessmentV3 | CredibilityAssessmentV4
    | CredibilityAssessmentV5,
    Field(discriminator="schema_version"),
]


class ClaimCheckResult(StrictModel):
    """任务一主张检查的返回值。任务二、任务三不改这个结果的组装方式。"""

    applies: bool
    anchors_matched: bool
    dimension: CredibilityDimension
    checks: CredibilityClaimChecks
    evidence: list[CredibilityEvidence]
    findings: list[CredibilityFinding]
    # Internal pipeline metadata, copied into the existing report.versions fields.
    model_calls: list[CredibilityModelCall] = []
    prompt_version: str = "not_run"
    generation_origin: Literal["prepared", "model", "not_run"] = "not_run"


class ReferenceCheckResult(StrictModel):
    """引用检查（任务二）的返回值。

    evidence 里论文内证据（参考文献条目原文）用 origin=paper，经 locate_paper_span 定位；
    外部元数据用 origin=external，并在 sources 里登记 URL、抓取时间和 fetch_status。
    """

    dimension: CredibilityDimension
    checks: CredibilityReferenceChecks
    evidence: list[CredibilityEvidence] = []
    sources: list[CredibilityExternalSource] = []
    findings: list[CredibilityFinding] = []
    model_calls: list[CredibilityModelCall] = []
    # Snapshot of the bounded reference agent's attempts and tool calls, when it ran.
    agent_audit_ref: str | None = None


class CodeCheckResult(StrictModel):
    """代码与复现线索（任务三）的返回值。约定同 ReferenceCheckResult。"""

    dimension: CredibilityDimension
    checks: CredibilityCodeChecks
    evidence: list[CredibilityEvidence] = []
    sources: list[CredibilityExternalSource] = []
    findings: list[CredibilityFinding] = []
    model_calls: list[CredibilityModelCall] = []
    prompt_version: str = "not_run"
    external_review: dict | None = None
    external_request_count: int = Field(default=0, ge=0)
    # Internal continuation state, saved in checkpoints but never copied into
    # public report.checks.code/repository fields.
    followup_state: dict[str, dict] = Field(default_factory=dict)
    discovery: CredibilityCodeDiscovery = Field(default_factory=CredibilityCodeDiscovery)


class CredibilityScoreResult(StrictModel):
    """计分结果：草稿 Rubric 清空后的三个维度，以及总分。"""

    dimensions: tuple[CredibilityDimension, CredibilityDimension, CredibilityDimension]
    overall: CredibilityOverall

    @model_validator(mode="after")
    def _dimension_order(self) -> CredibilityScoreResult:
        actual = tuple(item.dimension_id for item in self.dimensions)
        if actual != _DIMENSION_ORDER:
            raise ValueError("dimensions must be claim_evidence, reference_verification, code_reproduction")
        return self
