"""Durable credibility tasks and checkpoints, separate from venue assessments.

Every worker holds an expiring attempt token. Mutations compare that token, the
attempt and a row revision in one SQL UPDATE; a worker replaced by a recovery
attempt can no longer publish either a task snapshot or a stage checkpoint.
Completed checkpoints are reused only for their exact stage fingerprint and
immutable task fingerprint. An interrupted external call that did not commit a
checkpoint may run again: this store does not promise exactly-once API calls.
"""
from __future__ import annotations

import copy
import json
import math
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from app.storage import db
from app.storage.models import Base

_SCHEMA_LOCK = threading.Lock()
_DEFAULT_LEASE_SECONDS = 1800
_OWNER = re.compile(r"[a-z][a-z0-9_]{0,15}")


class TaskNotFound(LookupError):
    """The durable assessment does not exist."""


class LeaseConflict(RuntimeError):
    """A task is already owned, terminal, or cannot be claimed in this mode."""


class StaleLease(LeaseConflict):
    """The attempt was replaced, released, completed, or expired."""


class FingerprintMismatch(ValueError):
    """Recovery used a different input/configuration identity."""


class _RevisionConflict(Exception):
    pass


@dataclass(frozen=True)
class TaskLease:
    assessment_id: str
    attempt: int
    token: str
    expires_at: float


class CredibilityTaskRow(Base):
    __tablename__ = "credibility_tasks"

    assessment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(256))
    versions: Mapped[dict] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class CredibilityStageRow(Base):
    __tablename__ = "credibility_stage_checkpoints"

    assessment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("credibility_tasks.assessment_id"), primary_key=True,
    )
    stage: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(256), primary_key=True)
    task_fingerprint: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)



def _has_retryable_branch(payload: dict) -> bool:
    report = payload.get("report") or {}
    checks = (report.get("execution") or {}).get("checks", {})
    if isinstance(checks, dict) and any(
        isinstance(event, dict) and event.get("status") in {"failed", "partial", "not_run", "running"}
        for event in checks.values()
    ):
        return True
    # Compatibility with older execution summaries that incorrectly labelled
    # an empty failed/never-run check completed. Do not confuse an unscored
    # dimension with an unexecuted check or a legitimately inapplicable one.
    return any(
        isinstance(dimension, dict) and (
            dimension.get("check_status") in {"failed", "running"}
            or (dimension.get("check_status") == "not_run"
                and dimension.get("applicability") != "not_applicable")
        )
        for dimension in report.get("dimensions", [])
    )


def _json_copy(value: dict) -> dict:
    """Fail before touching a transaction if a checkpoint is not portable JSON."""
    if not isinstance(value, dict):
        raise TypeError("Snapshots, versions and checkpoint payloads must be dictionaries")
    return json.loads(json.dumps(value, allow_nan=False))


def _identity(value: str, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value


def _iso_time(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CredibilityTaskStore:
    """Use existing SQLAlchemy sessions; a separate factory is useful for tests.

    No public snapshot contains lease tokens or internal database metadata. Call
    ``load_task_metadata`` only inside the scheduler/recovery implementation.
    """

    def __init__(self, session_factory=None, *, clock: Callable[[], float] = time.time):
        self._session_factory = session_factory
        self._clock = clock

    @property
    def sessions(self):
        return self._session_factory or db.SessionLocal

    def _ensure_schema(self) -> None:
        # Registration uses the existing Base. Restrict creation to these new
        # tables and serialize first-use DDL within this process.
        with _SCHEMA_LOCK, self.sessions() as session:
            Base.metadata.create_all(
                session.get_bind(), tables=[CredibilityTaskRow.__table__, CredibilityStageRow.__table__],
            )

    def create_task(self, snapshot: dict, fingerprint: str, versions: dict) -> dict:
        payload = _json_copy(snapshot)
        version_payload = _json_copy(versions)
        assessment_id = _identity(payload.get("assessment_id"), "assessment_id", 64)
        _identity(fingerprint, "fingerprint")
        if payload.get("status") != "queued":
            raise ValueError("New credibility tasks must start queued")
        now = self._clock()
        self._ensure_schema()
        try:
            with self.sessions.begin() as session:
                session.add(CredibilityTaskRow(
                    assessment_id=assessment_id, fingerprint=fingerprint, versions=version_payload,
                    payload=payload, status="queued", attempt=0, revision=0,
                    lease_token=None, lease_until=None, created_at=now, updated_at=now,
                ))
        except IntegrityError as exc:
            raise LeaseConflict(f"Credibility task {assessment_id} already exists") from exc
        return copy.deepcopy(payload)

    def load_task(self, assessment_id: str) -> dict | None:
        self._ensure_schema()
        with self.sessions() as session:
            row = session.get(CredibilityTaskRow, assessment_id)
            return copy.deepcopy(row.payload) if row else None

    @staticmethod
    def _metadata(row: CredibilityTaskRow) -> dict:
        return {
            "assessment_id": row.assessment_id,
            "fingerprint": row.fingerprint,
            "versions": copy.deepcopy(row.versions),
            "status": row.status,
            "attempt": row.attempt,
            "lease_until": row.lease_until,
            "updated_at": row.updated_at,
        }

    def load_task_metadata(self, assessment_id: str) -> dict | None:
        self._ensure_schema()
        with self.sessions() as session:
            row = session.get(CredibilityTaskRow, assessment_id)
            return self._metadata(row) if row else None

    def list_tasks(self, statuses: list[str]) -> list[dict]:
        """Task snapshots in the given states, newest first, for the local history list."""
        self._ensure_schema()
        with self.sessions() as session:
            rows = session.scalars(select(CredibilityTaskRow).where(
                CredibilityTaskRow.status.in_(statuses),
            ).order_by(CredibilityTaskRow.created_at.desc(), CredibilityTaskRow.assessment_id)).all()
            return [copy.deepcopy(row.payload) for row in rows]

    def list_recoverable_tasks(self) -> list[dict]:
        """List explicit recovery candidates without stealing active worker leases."""
        self._ensure_schema()
        now = self._clock()
        with self.sessions() as session:
            rows = session.scalars(select(CredibilityTaskRow).where(
                CredibilityTaskRow.status.in_(["queued", "processing", "failed", "completed"]),
                or_(CredibilityTaskRow.lease_until.is_(None), CredibilityTaskRow.lease_until <= now),
            ).order_by(CredibilityTaskRow.created_at, CredibilityTaskRow.assessment_id)).all()
            return [self._metadata(row) for row in rows
                    if row.status != "completed" or _has_retryable_branch(row.payload)]

    def claim_task(
        self, assessment_id: str, *, resume: bool = False, force: bool = False,
        lease_seconds: float = _DEFAULT_LEASE_SECONDS, fingerprint: str | None = None,
        owner: str | None = None,
    ) -> TaskLease:
        """``owner`` tags the token so a restarted owner can find its own orphans."""
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        if owner is not None and not _OWNER.fullmatch(owner):
            raise ValueError("owner must be a short lowercase identifier")
        self._ensure_schema()
        for _ in range(8):
            now = self._clock()
            token = f"{owner}:{uuid.uuid4().hex}" if owner else uuid.uuid4().hex
            with self.sessions.begin() as session:
                row = session.get(CredibilityTaskRow, assessment_id)
                if row is None:
                    raise TaskNotFound(assessment_id)
                if fingerprint is not None and row.fingerprint != fingerprint:
                    raise FingerprintMismatch("The task input/configuration fingerprint changed")
                if row.status == "completed":
                    if not resume or not _has_retryable_branch(row.payload):
                        raise LeaseConflict("Completed tasks without failed/partial checks cannot be resumed")
                if row.lease_token and row.lease_until is not None and row.lease_until > now and not force:
                    raise LeaseConflict("Another worker holds an active lease")
                if row.status != "queued" and not (resume or force):
                    raise LeaseConflict("An interrupted or failed task requires explicit resume")
                payload = copy.deepcopy(row.payload)
                payload.update(status="processing", error=None, completed_at=None, report=None)
                if not payload.get("started_at"):
                    payload["started_at"] = _iso_time(now)
                result = session.execute(update(CredibilityTaskRow).where(
                    CredibilityTaskRow.assessment_id == assessment_id,
                    CredibilityTaskRow.revision == row.revision,
                ).values(
                    status="processing", payload=payload, attempt=row.attempt + 1,
                    revision=row.revision + 1, lease_token=token, lease_until=now + lease_seconds,
                    updated_at=now,
                ), execution_options={"synchronize_session": False})
                if result.rowcount == 1:
                    lease = TaskLease(assessment_id, row.attempt + 1, token, now + lease_seconds)
                    # The context manager commits before this return escapes.
                    return lease
        raise LeaseConflict("The task changed concurrently; retry the claim")

    def fail_interrupted_tasks(self, owner: str, error: dict) -> list[str]:
        """Fail queued/processing tasks whose worker cannot be alive any more.

        Call once at ``owner`` process startup, before it claims anything. A task
        is orphaned when it has no lease (queued, never started), an expired
        lease, or a lease whose token carries ``owner``: a single-process owner
        that just started holds no leases yet. Untagged active leases, such as an
        explicit ``scripts/credibility_tasks.py resume`` in another process, are
        left alone. Checkpoints stay, and the cleared lease lets a later
        ``claim_task(resume=True)`` take the failed task without ``force``.
        """
        if not _OWNER.fullmatch(owner):
            raise ValueError("owner must be a short lowercase identifier")
        content = _json_copy(error)
        self._ensure_schema()
        now = self._clock()
        failed = []
        with self.sessions.begin() as session:
            rows = session.scalars(select(CredibilityTaskRow).where(
                CredibilityTaskRow.status.in_(["queued", "processing"]),
                or_(
                    CredibilityTaskRow.lease_token.is_(None),
                    CredibilityTaskRow.lease_until.is_(None),
                    CredibilityTaskRow.lease_until <= now,
                    CredibilityTaskRow.lease_token.startswith(f"{owner}:", autoescape=True),
                ),
            ).order_by(CredibilityTaskRow.created_at, CredibilityTaskRow.assessment_id)).all()
            for row in rows:
                payload = self._merge_payload(row, {
                    "status": "failed", "stage": "failed", "paper": None, "report": None,
                    "error": {**content, "debug_id": row.assessment_id}, "completed_at": _iso_time(now),
                })
                result = session.execute(update(CredibilityTaskRow).where(
                    CredibilityTaskRow.assessment_id == row.assessment_id,
                    CredibilityTaskRow.revision == row.revision,
                ).values(
                    status="failed", payload=payload, revision=row.revision + 1,
                    lease_token=None, lease_until=None, updated_at=now,
                ), execution_options={"synchronize_session": False})
                # A concurrent claim or write won; that worker owns the task now.
                if result.rowcount == 1:
                    failed.append(row.assessment_id)
        return failed

    @staticmethod
    def _assert_owner(row: CredibilityTaskRow, assessment_id: str, lease: TaskLease, now: float) -> None:
        if (
            lease.assessment_id != assessment_id or row.attempt != lease.attempt
            or row.lease_token != lease.token or row.lease_until is None
            or row.lease_until <= now or row.status != "processing"
        ):
            raise StaleLease("The worker lease is expired or no longer owns this attempt")

    def _mutate(self, assessment_id: str, lease: TaskLease, change: Callable) -> dict:
        self._ensure_schema()
        for _ in range(8):
            now = self._clock()
            try:
                with self.sessions.begin() as session:
                    row = session.get(CredibilityTaskRow, assessment_id)
                    if row is None:
                        raise TaskNotFound(assessment_id)
                    self._assert_owner(row, assessment_id, lease, now)
                    values, after_update = change(row, now)
                    values.update(revision=row.revision + 1, updated_at=now)
                    result = session.execute(update(CredibilityTaskRow).where(
                        CredibilityTaskRow.assessment_id == assessment_id,
                        CredibilityTaskRow.revision == row.revision,
                        CredibilityTaskRow.attempt == lease.attempt,
                        CredibilityTaskRow.lease_token == lease.token,
                        CredibilityTaskRow.lease_until > now,
                        CredibilityTaskRow.status == "processing",
                    ).values(**values), execution_options={"synchronize_session": False})
                    if result.rowcount != 1:
                        raise _RevisionConflict
                    # Checkpoint writes occur in the same transaction as the
                    # ownership guard. A forced claimant waits for this commit.
                    if after_update is not None:
                        after_update(session, row, now)
                    payload = copy.deepcopy(values.get("payload", row.payload))
                return payload
            except _RevisionConflict:
                continue
        raise LeaseConflict("The task changed concurrently; retry the write")

    @staticmethod
    def _merge_payload(row: CredibilityTaskRow, fields: dict) -> dict:
        payload = copy.deepcopy(row.payload)
        payload.update(_json_copy(fields))
        # Identity and provenance belong to the creation, not a recovery worker.
        for key in ("assessment_id", "source", "created_at", "model_id", "schema_version", "analysis_mode"):
            if payload.get(key) != row.payload.get(key):
                raise FingerprintMismatch(f"Task identity field {key} cannot change")
        return payload

    def update_task(self, assessment_id: str, lease: TaskLease, **fields: Any) -> dict:
        if "status" in fields and fields["status"] != "processing":
            raise ValueError("Use finish_task, fail_task or release_task for terminal/release transitions")
        _json_copy(fields)

        def change(row, now):
            return {"payload": self._merge_payload(row, fields)}, None

        return self._mutate(assessment_id, lease, change)

    def renew_lease(
        self, assessment_id: str, lease: TaskLease, *, lease_seconds: float = _DEFAULT_LEASE_SECONDS,
    ) -> TaskLease:
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        expires_at = self._clock() + lease_seconds
        self._mutate(assessment_id, lease, lambda row, now: ({"lease_until": expires_at}, None))
        return TaskLease(assessment_id, lease.attempt, lease.token, expires_at)

    def save_stage(
        self, assessment_id: str, lease: TaskLease, stage: str, fingerprint: str, payload: dict,
        *, status: str = "completed", note: str | None = None,
    ) -> None:
        _identity(stage, "stage", 64)
        _identity(fingerprint, "fingerprint")
        content = _json_copy(payload)
        if status not in {"completed", "partial", "failed"}:
            raise ValueError("Checkpoint status must be completed, partial or failed")

        def change(task, now):
            def write(session, row, timestamp):
                key = {"assessment_id": assessment_id, "stage": stage, "fingerprint": fingerprint}
                checkpoint = session.get(CredibilityStageRow, key)
                if checkpoint is None:
                    session.add(CredibilityStageRow(
                        **key, task_fingerprint=row.fingerprint, status=status, payload=content,
                        note=note, attempt=lease.attempt, created_at=timestamp, updated_at=timestamp,
                    ))
                elif checkpoint.status != "completed" or status == "completed":
                    checkpoint.status = status
                    checkpoint.payload = content
                    checkpoint.note = note
                    checkpoint.attempt = lease.attempt
                    checkpoint.updated_at = timestamp
            return {}, write

        self._mutate(assessment_id, lease, change)

    def discard_stage(self, assessment_id: str, lease: TaskLease, stage: str, fingerprint: str) -> None:
        """Invalidate a known-bad legacy checkpoint under the current attempt lease."""
        def change(task, now):
            def discard(session, row, timestamp):
                checkpoint = session.get(CredibilityStageRow, {
                    "assessment_id": assessment_id, "stage": stage, "fingerprint": fingerprint,
                })
                if checkpoint is not None and checkpoint.task_fingerprint == row.fingerprint:
                    session.delete(checkpoint)
            return {}, discard

        self._mutate(assessment_id, lease, change)

    def load_stage_record(self, assessment_id: str, stage: str, fingerprint: str) -> dict | None:
        """Read an exact checkpoint including incomplete state, without promoting it.

        Recovery may reuse individual successful observations inside a partial
        payload. The caller must inspect ``status``; ``load_stage`` remains the
        completed-only shortcut and is intentionally unchanged.
        """
        self._ensure_schema()
        with self.sessions() as session:
            task = session.get(CredibilityTaskRow, assessment_id)
            if task is None:
                return None
            checkpoint = session.get(CredibilityStageRow, {
                "assessment_id": assessment_id, "stage": stage, "fingerprint": fingerprint,
            })
            if checkpoint is None or checkpoint.task_fingerprint != task.fingerprint:
                return None
            return {
                "status": checkpoint.status,
                "payload": copy.deepcopy(checkpoint.payload),
                "attempt": checkpoint.attempt,
            }

    def load_stage(self, assessment_id: str, stage: str, fingerprint: str) -> dict | None:
        self._ensure_schema()
        with self.sessions() as session:
            task = session.get(CredibilityTaskRow, assessment_id)
            if task is None:
                return None
            checkpoint = session.get(CredibilityStageRow, {
                "assessment_id": assessment_id, "stage": stage, "fingerprint": fingerprint,
            })
            if (
                checkpoint is None or checkpoint.status != "completed"
                or checkpoint.task_fingerprint != task.fingerprint
            ):
                return None
            return copy.deepcopy(checkpoint.payload)

    def finish_task(self, assessment_id: str, lease: TaskLease, snapshot: dict) -> dict:
        content = _json_copy(snapshot)
        if content.get("status") != "completed":
            raise ValueError("finish_task requires a completed snapshot")

        def change(row, now):
            payload = self._merge_payload(row, content)
            return {"payload": payload, "status": "completed", "lease_token": None, "lease_until": None}, None

        return self._mutate(assessment_id, lease, change)

    def fail_task(self, assessment_id: str, lease: TaskLease, error: dict, **fields: Any) -> dict:
        content = _json_copy(error)

        def change(row, now):
            updates = dict(fields)
            updates.update(status="failed", stage="failed", error=content, report=None, completed_at=_iso_time(now))
            payload = self._merge_payload(row, updates)
            return {"payload": payload, "status": "failed", "lease_token": None, "lease_until": None}, None

        return self._mutate(assessment_id, lease, change)

    def release_task(self, assessment_id: str, lease: TaskLease, **fields: Any) -> dict:
        """Relinquish this attempt without discarding successful checkpoints."""
        def change(row, now):
            updates = dict(fields)
            updates.update(status="queued", error=None, completed_at=None)
            payload = self._merge_payload(row, updates)
            return {"payload": payload, "status": "queued", "lease_token": None, "lease_until": None}, None

        return self._mutate(assessment_id, lease, change)


_default_store = CredibilityTaskStore()
create_task = _default_store.create_task
load_task = _default_store.load_task
load_task_metadata = _default_store.load_task_metadata
list_recoverable_tasks = _default_store.list_recoverable_tasks
claim_task = _default_store.claim_task
update_task = _default_store.update_task
renew_lease = _default_store.renew_lease
save_stage = _default_store.save_stage
discard_stage = _default_store.discard_stage
load_stage = _default_store.load_stage
load_stage_record = _default_store.load_stage_record
finish_task = _default_store.finish_task
fail_task = _default_store.fail_task
release_task = _default_store.release_task
fail_interrupted_tasks = _default_store.fail_interrupted_tasks
