"""In-process progress notes for running checks, read by the progress route, and the user's stop flags.

Display only: nothing here feeds the report, and the notes are dropped when a check ends.
Each check runs inside ``scope`` so a model call made anywhere below it is attributed to that check.

The reader can stop a whole assessment (``cancel``) or one check of it (``stop_check``). Either takes effect within
a second: ``note`` and ``raise_if_cancelled`` raise at the next model call, and a model call already in flight is
abandoned (``app.llm.interrupt``). ``Cancelled`` and ``CheckStopped`` are BaseExceptions on purpose, so the many
``except Exception`` fallbacks in the checks cannot swallow them and carry on.
"""
from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from app.llm import interrupt

_scope: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("credibility_live_scope", default=None)
_lock = threading.Lock()
_events: dict[str, list[tuple[str, str, float]]] = {}
_cancelled: set[str] = set()
_stopped: set[tuple[str, str]] = set()
_LIMIT = 500


class Cancelled(BaseException):  # noqa: N818 - a control-flow signal, like KeyboardInterrupt
    """Raised inside a running check after its assessment was cancelled."""


class CheckStopped(BaseException):  # noqa: N818 - a control-flow signal, like KeyboardInterrupt
    """Raised inside one check that the reader stopped; the other checks carry on."""

    def __init__(self, assessment_id: str, check: str):
        super().__init__(assessment_id, check)
        self.check = check


def cancel(assessment_id: str) -> None:
    with _lock:
        _cancelled.add(assessment_id)


def stop_check(assessment_id: str, check: str) -> None:
    with _lock:
        _stopped.add((assessment_id, check))


def stop_requested(assessment_id: str, check: str) -> bool:
    with _lock:
        return (assessment_id, check) in _stopped


def raise_if_cancelled() -> None:
    current = _scope.get()
    if current is not None:
        with _lock:
            cancelled = current[0] in _cancelled
            stopped = current in _stopped
        if cancelled:
            raise Cancelled(current[0])
        if stopped:
            raise CheckStopped(*current)


@contextmanager
def scope(assessment_id: str | None, check: str) -> Iterator[None]:
    token = _scope.set((assessment_id, check) if assessment_id else None)
    try:
        with interrupt.watch(raise_if_cancelled):
            yield
    finally:
        _scope.reset(token)


def note(step: str) -> None:
    raise_if_cancelled()
    current = _scope.get()
    if current is None:
        return
    assessment_id, check = current
    with _lock:
        rows = _events.setdefault(assessment_id, [])
        rows.append((check, step, time.time()))
        del rows[:-_LIMIT]


def forget(assessment_id: str) -> None:
    with _lock:
        _events.pop(assessment_id, None)
        _cancelled.discard(assessment_id)
        _stopped.difference_update({key for key in _stopped if key[0] == assessment_id})


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def summary(assessment_id: str) -> dict[str, dict]:
    """Per check: when it started, how many times each step ran, and the latest step."""
    with _lock:
        rows = list(_events.get(assessment_id, []))
    out: dict[str, dict] = {}
    for check, step, at in rows:
        entry = out.setdefault(check, {"started_at": None, "steps": {}, "last_step": None, "last_at": None})
        if step == "started":
            entry["started_at"] = _iso(at)
            continue
        entry["steps"][step] = entry["steps"].get(step, 0) + 1
        entry["last_step"], entry["last_at"] = step, _iso(at)
    return out
