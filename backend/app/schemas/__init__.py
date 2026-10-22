"""Unified Field Contract v0.4 的可执行版本。

契约文档是唯一事实来源；这里的每个类都对应契约中的一节，改动必须同步升级
SCHEMA_VERSION 并在契约 §26 记录变更。
"""
from app.schemas.assessment import (  # noqa: F401
    Assessment,
    ErrorInfo,
    ModelOption,
    ModelRef,
    Source,
    VenueOption,
    VenueRef,
    Warning_,
)
from app.schemas.common import EvidenceLink, EvidenceReference  # noqa: F401
from app.schemas.enums import (  # noqa: F401
    CHECKLIST_IDS,
    DIMENSION_IDS,
    SCHEMA_VERSION,
    Applicability,
    ChecklistStatus,
    Confidence,
    ContributionType,
    ErrorCode,
    EvidenceMode,
    Importance,
    LocatorType,
    ParseQuality,
    Rating,
    ReadingRecommendation,
    SourceType,
    Stage,
    Stance,
    Status,
    SupportLevel,
    WarningCode,
)
from app.schemas.paper import Paper, Section  # noqa: F401
from app.schemas.report import (  # noqa: F401
    ClaimCheck,
    ClaimedContribution,
    DimensionAssessment,
    Finding,
    OverallAssessment,
    OverallCalculation,
    Report,
    ReproducibilityCheck,
    UnresolvedQuestion,
)
from app.schemas.requests import CreateAssessmentRequest  # noqa: F401
