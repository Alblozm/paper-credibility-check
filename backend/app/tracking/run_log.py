"""契约 §22：内部日志必须能追踪 model、rubric、prompt/config 版本、关键参数和时间。

顺带一个好处：期末写报告要的 token 消耗、耗时、重试率、修复率，全部来自这张表，
不需要临时补数据。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("pcc.runs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class RunRecord:
    assessment_id: str
    stage: str
    model_id: str
    model_version: str
    rubric_version: str
    prompt_version: str
    rubric_sha256: str | None = None
    policy_version: str | None = None
    outcome: str | None = None
    reason_code: str | None = None
    temperature: float = 0.2
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1
    repair_passes: int = 0
    validation_issue_count: int = 0
    error_code: str | None = None
    created_at: str = field(default_factory=_now)

    def as_dict(self) -> dict:
        return asdict(self)


def log_run(record: RunRecord) -> None:
    """先写结构化日志；接了数据库后在这里同时落 runs 表。

    TODO：storage.repository.save_run(record)
    """
    logger.info("run %s", json.dumps(record.as_dict(), ensure_ascii=False))
