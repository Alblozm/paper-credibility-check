"""校验问题的统一表示。

这些对象有两个去处：
1. 喂回给模型做 repair pass（app.llm.repair）
2. 转成契约 §17.1 的 warning 返回给前端
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    #  必须修复，否则整份报告不合契约
    BLOCKING = "blocking"
    #  已被后端自动修正或降级，报告仍然可用
    REPAIRED = "repaired"
    #  仅提示
    INFO = "info"


class ValidationIssue(BaseModel):
    code: str
    path: str
    message: str
    severity: Severity = Severity.BLOCKING

    def as_prompt_line(self) -> str:
        """给 repair prompt 用的一行描述。"""
        return f"- {self.path}: {self.message}"
