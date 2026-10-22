"""结构化输出校验与错误处理。

入口：`validate_report_payload`。
"""
from app.validation.errors import Severity, ValidationIssue  # noqa: F401
from app.validation.report_validator import ValidationResult, validate_report_payload  # noqa: F401
