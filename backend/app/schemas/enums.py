"""契约 v0.4 中的全部枚举与固定 ID 常量。

注意：Python 3.10 没有 StrEnum，统一使用 `class X(str, Enum)`。
"""
from enum import Enum

SCHEMA_VERSION = "0.4"


class Status(str, Enum):
    """契约 §9.1 任务生命周期。"""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Stage(str, Enum):
    """契约 §9.2 处理阶段。"""
    VALIDATING_INPUT = "validating_input"
    RETRIEVING_PAPER = "retrieving_paper"
    PARSING_PAPER = "parsing_paper"
    ANALYZING = "analyzing"
    LINKING_EVIDENCE = "linking_evidence"
    FINALIZING_REPORT = "finalizing_report"
    REPORT_READY = "report_ready"


class Confidence(str, Enum):
    """契约 §3 通用置信度。全项目只有这一套，不得再造第二套。"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(str, Enum):
    ARXIV = "arxiv"
    PDF = "pdf"


class ParseQuality(str, Enum):
    GOOD = "good"
    PARTIAL = "partial"
    POOR = "poor"


class Applicability(str, Enum):
    """契约 §10.2。"""
    APPLICABLE = "applicable"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


class Rating(str, Enum):
    """契约 §10.3 / §16.1。维度与总分共用同一套等级。"""
    STRONG = "strong"
    GOOD = "good"
    ADEQUATE = "adequate"
    WEAK = "weak"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"
    UNABLE_TO_ASSESS = "unable_to_assess"


class SupportLevel(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"


class ContributionType(str, Enum):
    METHOD = "method"
    THEORY = "theory"
    DATASET = "dataset"
    EMPIRICAL_FINDING = "empirical_finding"
    SYSTEM = "system"
    ANALYSIS = "analysis"
    OTHER = "other"


class Importance(str, Enum):
    MAJOR = "major"
    MINOR = "minor"


class LocatorType(str, Enum):
    TEXT = "text"
    FIGURE = "figure"
    TABLE = "table"
    EQUATION = "equation"
    THEOREM = "theorem"


class EvidenceMode(str, Enum):
    QUOTE = "quote"
    PARAPHRASE = "paraphrase"


class Stance(str, Enum):
    """契约 §15.3。只出现在 EvidenceLink，不出现在 EvidenceReference。"""
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ChecklistStatus(str, Enum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    UNABLE_TO_VERIFY = "unable_to_verify"
    NOT_APPLICABLE = "not_applicable"


class ReadingRecommendation(str, Enum):
    WORTH_DEEP_READING = "worth_deep_reading"
    WORTH_READING_WITH_CHECKS = "worth_reading_with_checks"
    SELECTIVE_READING = "selective_reading"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNABLE_TO_ASSESS = "unable_to_assess"


class WarningCode(str, Enum):
    """契约 §17.1 建议 code。"""
    AUTHORS_MISSING = "AUTHORS_MISSING"
    ABSTRACT_MISSING = "ABSTRACT_MISSING"
    OCR_USED = "OCR_USED"
    SECTION_PARSE_PARTIAL = "SECTION_PARSE_PARTIAL"
    PAGE_NUMBER_UNCERTAIN = "PAGE_NUMBER_UNCERTAIN"
    REFERENCE_PARSE_PARTIAL = "REFERENCE_PARSE_PARTIAL"
    # 后端自有：交叉校验与证据校验产生的告警
    EVIDENCE_DROPPED = "EVIDENCE_DROPPED"
    EVIDENCE_EXCERPT_UNVERIFIED = "EVIDENCE_EXCERPT_UNVERIFIED"
    CHECKLIST_DOWNGRADED = "CHECKLIST_DOWNGRADED"
    DIMENSION_DEGRADED = "DIMENSION_DEGRADED"
    MODEL_OUTPUT_REPAIRED = "MODEL_OUTPUT_REPAIRED"


class ErrorCode(str, Enum):
    """契约 §17.2 建议 code。"""
    INVALID_ARXIV_ID = "INVALID_ARXIV_ID"
    ARXIV_FETCH_FAILED = "ARXIV_FETCH_FAILED"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PDF_PASSWORD_PROTECTED = "PDF_PASSWORD_PROTECTED"
    PDF_PARSE_FAILED = "PDF_PARSE_FAILED"
    EMPTY_TEXT = "EMPTY_TEXT"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    UNABLE_TO_ASSESS = "UNABLE_TO_ASSESS"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 契约 §10.1：五个固定维度，顺序固定，不多不少
DIMENSION_IDS: tuple[str, ...] = (
    "research_value",
    "soundness_evidence",
    "originality_positioning",
    "clarity_usability",
    "reproducibility_transparency",
)

DIMENSION_DISPLAY_NAMES: dict[str, str] = {
    "research_value": "Research Value & Significance",
    "soundness_evidence": "Soundness & Evidence Support",
    "originality_positioning": "Originality & Positioning",
    "clarity_usability": "Clarity & Usability",
    "reproducibility_transparency": "Reproducibility & Transparency",
}

# 契约 §13.1：六项固定检查，顺序固定
CHECKLIST_IDS: tuple[str, ...] = (
    "code_availability",
    "data_availability",
    "random_seed",
    "hyperparameters",
    "hardware_environment",
    "preprocessing_details",
)

CHECKLIST_DISPLAY_NAMES: dict[str, str] = {
    "code_availability": "Code availability",
    "data_availability": "Data availability",
    "random_seed": "Random seed",
    "hyperparameters": "Hyperparameters",
    "hardware_environment": "Hardware and environment",
    "preprocessing_details": "Preprocessing details",
}

# 契约 §10.1 v0.1 等权重
DEFAULT_DIMENSION_WEIGHT = 0.2
