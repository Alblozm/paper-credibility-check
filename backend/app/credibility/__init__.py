"""可信度核查（analysis_mode=credibility）。导入本包不会运行模型、解析器或外部查询。"""

from app.credibility.claims import check_claims
from app.credibility.code_checks import check_code
from app.credibility.context import CheckContext, build_context
from app.credibility.references import check_references
from app.credibility.scoring import score_dimensions
from app.credibility.shell import assemble_report, build_completed_assessment

__all__ = [
    "CheckContext",
    "assemble_report",
    "build_completed_assessment",
    "build_context",
    "check_claims",
    "check_code",
    "check_references",
    "score_dimensions",
]
