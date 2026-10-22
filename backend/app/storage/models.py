"""数据库表。

设计取舍：**分数进关系表（便于聚合与评估），模型原始输出进 JSON 列（便于复盘）。**
契约要求历史报告保留当时的 schema / rubric / model 版本，所以这三个字段冗余存一份。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class PaperRow(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)   # 同一篇重复提交可命中缓存
    arxiv_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    storage_uri: Mapped[str | None] = mapped_column(Text)               # 私有路径，不返回给前端
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ParsedDocRow(Base):
    __tablename__ = "parsed_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    parser: Mapped[str] = mapped_column(String(32))
    parse_quality: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict] = mapped_column(JSON)                          # 解析结果全量留档
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AssessmentRow(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[dict] = mapped_column(JSON)
    venue_id: Mapped[str] = mapped_column(String(64))
    rubric_version: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.id"), nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    report: Mapped[ReportRow | None] = relationship(back_populates="assessment", uselist=False)


class AssessmentSnapshotRow(Base):
    """Exact API snapshots for local history, independent of paper parsing rows."""

    __tablename__ = "assessment_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[str] = mapped_column(Text, default="")
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)


class ReportRow(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"), index=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_rating: Mapped[str] = mapped_column(String(24))
    is_provisional: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict] = mapped_column(JSON)          # 校验后的完整报告
    raw_model_output: Mapped[str | None] = mapped_column(Text)  # 未经处理的模型输出，供复盘
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    assessment: Mapped[AssessmentRow] = relationship(back_populates="report")


class DimensionScoreRow(Base):
    """维度分单独成表，方便做评估实验时直接聚合，不用反复解 JSON。"""

    __tablename__ = "dimension_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"), index=True)
    dimension_id: Mapped[str] = mapped_column(String(48), index=True)
    applicability: Mapped[str] = mapped_column(String(16))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[str] = mapped_column(String(24))
    weight: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(8))
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)


class RunRow(Base):
    """契约 §22 的调用追踪。"""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    rubric_version: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str] = mapped_column(String(32))
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    repair_passes: Mapped[int] = mapped_column(Integer, default=0)
    validation_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
