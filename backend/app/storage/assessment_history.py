"""Local assessment history. The payload is the exact public Assessment snapshot."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.storage.db import SessionLocal, init_db
from app.storage.models import AssessmentSnapshotRow


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def save_assessment(snapshot: dict) -> None:
    init_db()
    paper = snapshot.get("paper") or {}
    source = snapshot.get("source") or {}
    title = paper.get("title") or source.get("original_filename") or source.get("arxiv_id") or "Untitled paper"
    authors = ", ".join(paper.get("authors") or [])
    venue = snapshot.get("venue") or {}
    search_text = " ".join(filter(None, [
        title, authors, paper.get("abstract") or "", venue.get("code") or "",
        str(venue.get("year") or ""), source.get("arxiv_id") or "",
    ])).lower()
    with SessionLocal.begin() as session:
        row = session.get(AssessmentSnapshotRow, snapshot["assessment_id"])
        if row is None:
            row = AssessmentSnapshotRow(id=snapshot["assessment_id"])
            session.add(row)
        row.status = snapshot["status"]
        row.title = title
        row.authors = authors
        row.search_text = search_text
        row.created_at = _parse_time(snapshot["created_at"])
        row.completed_at = _parse_time(snapshot.get("completed_at"))
        row.payload = dict(snapshot)


def load_assessment(assessment_id: str) -> dict | None:
    init_db()
    with SessionLocal() as session:
        row = session.get(AssessmentSnapshotRow, assessment_id)
        return row.payload if row else None


def list_assessments(
    *, search: str = "", sort: str = "date", offset: int = 0, limit: int = 20,
) -> dict:
    init_db()
    query = select(AssessmentSnapshotRow).where(
        AssessmentSnapshotRow.status.in_(["completed", "failed"])
    )
    if search.strip():
        query = query.where(AssessmentSnapshotRow.search_text.contains(search.strip().lower()))
    ordering = {
        "date": AssessmentSnapshotRow.created_at.desc(),
        "title": func.lower(AssessmentSnapshotRow.title).asc(),
        "author": func.lower(AssessmentSnapshotRow.authors).asc(),
    }[sort]
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        page_query = query.order_by(ordering, AssessmentSnapshotRow.id.desc()).offset(offset).limit(limit)
        rows = session.scalars(page_query).all()
        items = []
        for row in rows:
            value = row.payload
            report = value.get("report") or {}
            overall = report.get("overall_assessment") or {}
            paper = value.get("paper") or {}
            items.append({
                "assessment_id": row.id,
                "status": row.status,
                "title": row.title,
                "authors": paper.get("authors") or [],
                "source_type": (value.get("source") or {}).get("type"),
                "venue": value.get("venue"),
                "model": value.get("model"),
                "overall_score": overall.get("overall_score"),
                "rating": overall.get("rating"),
                "is_demo": any(warning.get("code") == "DEMO_MODE" for warning in value.get("warnings") or []),
                "created_at": value.get("created_at"),
                "completed_at": value.get("completed_at"),
            })
        return {"items": items, "total": total, "offset": offset, "limit": limit}


def fail_interrupted_assessments() -> None:
    """A restarted single-process backend cannot resume its former background tasks."""
    init_db()
    with SessionLocal.begin() as session:
        rows = session.scalars(select(AssessmentSnapshotRow).where(
            AssessmentSnapshotRow.status.in_(["queued", "processing"])
        )).all()
        for row in rows:
            value = dict(row.payload)
            value.update(
                status="failed",
                report=None,
                error={
                    "code": "SERVICE_RESTARTED",
                    "message": "The local service restarted before this review finished. Please submit it again.",
                    "stage": value.get("stage"),
                    "retryable": True,
                    "debug_id": row.id,
                },
                completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            row.status = "failed"
            row.completed_at = _parse_time(value["completed_at"])
            row.payload = value
