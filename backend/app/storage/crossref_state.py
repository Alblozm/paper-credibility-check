"""Cross-process Crossref scheduling and successful-response cache.

The caller supplies one provider-wide database path, independent of any task's
DATABASE_URL. No network calls, sleeps, application settings, or credentials live
here. A caller loops over ``lookup`` / ``reserve`` and sleeps *outside* SQLite
transactions when it receives ``Wait``. Reservations cover the complete HTTP
request; callers must renew a lease before it expires if a request can outlive it.

Query keys must describe the canonical public query, without transport contact
details. Origin snapshot references are provenance, not portable file handles:
use ``CacheEntry.local_snapshot_ref`` only in the same artifact namespace, or
rebuild a new local snapshot from the verified cached payload and optional raw
bytes. ``raw=None`` explicitly means normalized-only, never original HTTP bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class StaleReservationError(RuntimeError):
    """A lease expired or another owner replaced it; its result cannot be saved."""


class CorruptCacheEntryError(RuntimeError):
    """Persisted response content no longer matches its recorded hashes."""


@dataclass(frozen=True)
class Reservation:
    query_key: str
    token: str
    expires_at: float


@dataclass(frozen=True)
class Wait:
    seconds: float
    reason: Literal["cached", "cooldown", "queued", "spacing"]


@dataclass(frozen=True)
class CacheEntry:
    query_key: str
    payload: dict[str, Any]
    raw: bytes | None
    payload_sha256: str
    raw_sha256: str | None
    normalized_only: bool
    http_status: int
    url: str | None
    final_url: str | None
    retrieved_at: str | None
    created_at: float
    expires_at: float
    origin_snapshot_ref: str | None
    origin_artifact_namespace: str | None

    def local_snapshot_ref(self, artifact_namespace: str) -> str | None:
        """Return an origin reference only when its artifact store is the same.

        The caller still verifies that snapshot's immutable contents before use.
        Different stores must create their own observation from ``raw/payload``.
        """
        if artifact_namespace and artifact_namespace == self.origin_artifact_namespace:
            return self.origin_snapshot_ref
        return None


def _duration(value: float, name: str, *, positive: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}.")
    return result


def _json_bytes(payload: dict[str, Any]) -> bytes:
    if not isinstance(payload, dict):
        raise ValueError("A cached HTTP 200 payload must be a JSON object.")
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("A cached response must contain valid JSON.") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class CrossrefStateStore:
    """A dedicated SQLite FIFO queue, one global expiring lease, and TTL cache.

    ``interval_seconds`` values supplied later can only slow the shared provider
    down; one process cannot undo another process's observed rate limit. The
    concurrency limit is always one. Queue entries use a renewable heartbeat so
    an abandoned waiter cannot block the FIFO forever. No connection survives a
    method call, making instances safe to construct independently in each worker.
    """

    def __init__(
        self,
        state_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        queue_ttl_seconds: float = 30.0,
        poll_seconds: float = 0.1,
        max_raw_bytes: int | None = None,
    ) -> None:
        if str(state_path) == ":memory:":
            raise ValueError("Crossref state requires a shared on-disk database.")
        self.state_path = Path(state_path).expanduser().resolve()
        self._clock = clock
        self._queue_ttl = _duration(queue_ttl_seconds, "queue_ttl_seconds", positive=True)
        self._poll_seconds = _duration(poll_seconds, "poll_seconds", positive=True)
        if max_raw_bytes is not None and (not isinstance(max_raw_bytes, int) or max_raw_bytes < 0):
            raise ValueError("max_raw_bytes must be a nonnegative integer or None.")
        self._max_raw_bytes = max_raw_bytes
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS crossref_control (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    active_token TEXT, active_query_key TEXT, lease_expires_at REAL,
                    last_started_at REAL, next_allowed_at REAL NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    interval_seconds REAL NOT NULL DEFAULT 0
                )
            """)
            connection.execute("INSERT OR IGNORE INTO crossref_control(singleton) VALUES (1)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS crossref_queue (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key TEXT NOT NULL UNIQUE, expires_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS crossref_cache (
                    query_key TEXT PRIMARY KEY, payload BLOB NOT NULL, raw BLOB,
                    payload_sha256 TEXT NOT NULL, raw_sha256 TEXT,
                    http_status INTEGER NOT NULL CHECK (http_status = 200),
                    url TEXT, final_url TEXT, retrieved_at TEXT,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    origin_snapshot_ref TEXT, origin_artifact_namespace TEXT
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS crossref_cache_expiry ON crossref_cache(expires_at)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.state_path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _now(self) -> float:
        now = float(self._clock())
        if not math.isfinite(now):
            raise ValueError("clock must return a finite Unix timestamp.")
        return now

    @staticmethod
    def _key(query_key: str) -> str:
        if not isinstance(query_key, str) or not query_key.strip():
            raise ValueError("query_key must be a nonempty canonical query key.")
        return query_key

    @staticmethod
    def _read_cache(row: sqlite3.Row) -> CacheEntry:
        try:
            encoded = bytes(row["payload"])
            raw = bytes(row["raw"]) if row["raw"] is not None else None
            payload = json.loads(encoded)
            if _json_bytes(payload) != encoded or _sha256(encoded) != row["payload_sha256"]:
                raise ValueError("payload hash mismatch")
            if raw is None:
                if row["raw_sha256"] is not None:
                    raise ValueError("missing raw response")
            elif _sha256(raw) != row["raw_sha256"] or _json_bytes(json.loads(raw)) != encoded:
                raise ValueError("raw response mismatch")
            if row["http_status"] != 200:
                raise ValueError("unsuccessful cached response")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise CorruptCacheEntryError("Crossref cached response failed content verification.") from exc
        return CacheEntry(
            query_key=row["query_key"], payload=payload, raw=raw,
            payload_sha256=row["payload_sha256"], raw_sha256=row["raw_sha256"], normalized_only=raw is None,
            http_status=row["http_status"], url=row["url"], final_url=row["final_url"],
            retrieved_at=row["retrieved_at"], created_at=row["created_at"], expires_at=row["expires_at"],
            origin_snapshot_ref=row["origin_snapshot_ref"], origin_artifact_namespace=row["origin_artifact_namespace"],
        )

    def lookup(self, query_key: str) -> CacheEntry | None:
        """Read a verified, unexpired HTTP 200 cache entry from any process."""
        key = self._key(query_key)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM crossref_cache WHERE query_key = ? AND expires_at > ?", (key, self._now()),
            ).fetchone()
        return self._read_cache(row) if row is not None else None

    def reserve(
        self, query_key: str, *, interval_seconds: float = 0.2, lease_seconds: float = 30.0,
    ) -> Reservation | Wait:
        """Join the FIFO and atomically acquire the sole provider-wide lease.

        ``Wait('cached', 0)`` closes the lookup/reserve race: call ``lookup`` again
        instead of sending another request. Queued waits are short polling hints;
        cooldown and spacing waits expose their actual remaining durations.
        """
        key = self._key(query_key)
        interval = _duration(interval_seconds, "interval_seconds")
        lease = _duration(lease_seconds, "lease_seconds", positive=True)
        with self._transaction() as connection:
            now = self._now()
            connection.execute("DELETE FROM crossref_cache WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM crossref_queue WHERE expires_at <= ?", (now,))
            connection.execute("""
                UPDATE crossref_control SET active_token = NULL, active_query_key = NULL, lease_expires_at = NULL
                WHERE singleton = 1 AND lease_expires_at <= ?
            """, (now,))
            self._set_limits(connection, now, interval, 0)
            cached = connection.execute("SELECT * FROM crossref_cache WHERE query_key = ?", (key,)).fetchone()
            if cached is not None:
                self._read_cache(cached)
                connection.execute("DELETE FROM crossref_queue WHERE query_key = ?", (key,))
                return Wait(0.0, "cached")
            control = connection.execute("SELECT * FROM crossref_control WHERE singleton = 1").fetchone()
            if control["active_query_key"] != key:
                connection.execute("""
                    INSERT INTO crossref_queue(query_key, expires_at) VALUES (?, ?)
                    ON CONFLICT(query_key) DO UPDATE SET expires_at = excluded.expires_at
                """, (key, max(now, control["cooldown_until"], control["next_allowed_at"]) + self._queue_ttl))
            if control["cooldown_until"] > now:
                return Wait(control["cooldown_until"] - now, "cooldown")
            if control["active_token"] is not None:
                return Wait(min(self._poll_seconds, control["lease_expires_at"] - now), "queued")
            head = connection.execute("SELECT query_key FROM crossref_queue ORDER BY sequence LIMIT 1").fetchone()
            if head is None or head["query_key"] != key:
                return Wait(self._poll_seconds, "queued")
            if control["next_allowed_at"] > now:
                return Wait(control["next_allowed_at"] - now, "spacing")
            token = uuid.uuid4().hex
            expires_at = now + lease
            connection.execute("""
                UPDATE crossref_control SET active_token = ?, active_query_key = ?, lease_expires_at = ?,
                    last_started_at = ?, next_allowed_at = ? WHERE singleton = 1
            """, (token, key, expires_at, now, now + control["interval_seconds"]))
            connection.execute("DELETE FROM crossref_queue WHERE query_key = ?", (key,))
            return Reservation(key, token, expires_at)

    @staticmethod
    def _set_limits(connection: sqlite3.Connection, now: float, interval: float, retry_after: float) -> None:
        connection.execute("""
            UPDATE crossref_control SET
                interval_seconds = MAX(interval_seconds, ?),
                next_allowed_at = CASE WHEN last_started_at IS NULL THEN next_allowed_at
                    ELSE MAX(next_allowed_at, last_started_at + MAX(interval_seconds, ?)) END,
                cooldown_until = MAX(cooldown_until, ?) WHERE singleton = 1
        """, (interval, interval, now + retry_after if retry_after else 0))

    @staticmethod
    def _assert_owner(connection: sqlite3.Connection, token: str, now: float) -> sqlite3.Row:
        row = connection.execute("""
            SELECT * FROM crossref_control WHERE singleton = 1 AND active_token = ? AND lease_expires_at > ?
        """, (token, now)).fetchone()
        if row is None:
            raise StaleReservationError("Crossref reservation is no longer owned by this token.")
        return row

    def finish(
        self,
        token: str,
        *,
        payload: dict[str, Any] | None = None,
        raw: bytes | None = None,
        http_status: int | None = None,
        url: str | None = None,
        final_url: str | None = None,
        retrieved_at: str | None = None,
        ttl_seconds: float = 600.0,
        retry_after_seconds: float = 0.0,
        interval_seconds: float | None = None,
        snapshot_ref: str | None = None,
        artifact_namespace: str | None = None,
    ) -> CacheEntry | None:
        """CAS-release a live lease, optionally caching a successful JSON response.

        Failure responses (including 429) never enter the success cache. Their
        raw evidence belongs in the caller's snapshot store. Retry-After still
        updates the persistent global cooldown. A stale owner changes nothing.
        """
        ttl = _duration(ttl_seconds, "ttl_seconds")
        retry_after = _duration(retry_after_seconds, "retry_after_seconds")
        interval = _duration(interval_seconds, "interval_seconds") if interval_seconds is not None else 0.0
        encoded = None
        if http_status == 200 and payload is not None:
            encoded = _json_bytes(payload)
            if raw is not None:
                if not isinstance(raw, bytes):
                    raise ValueError("raw must contain actual response bytes or None.")
                if self._max_raw_bytes is not None and len(raw) > self._max_raw_bytes:
                    raise ValueError("Raw Crossref response exceeds max_raw_bytes.")
                try:
                    if _json_bytes(json.loads(raw)) != encoded:
                        raise ValueError("raw and normalized JSON disagree")
                except (ValueError, UnicodeError) as exc:
                    raise ValueError("Raw Crossref response must match the normalized JSON payload.") from exc
        with self._transaction() as connection:
            now = self._now()
            control = self._assert_owner(connection, token, now)
            self._set_limits(connection, now, interval, retry_after)
            key = control["active_query_key"]
            if encoded is not None and ttl > 0:
                connection.execute("""
                    INSERT OR REPLACE INTO crossref_cache (
                        query_key, payload, raw, payload_sha256, raw_sha256, http_status,
                        url, final_url, retrieved_at, created_at, expires_at,
                        origin_snapshot_ref, origin_artifact_namespace
                    ) VALUES (?, ?, ?, ?, ?, 200, ?, ?, ?, ?, ?, ?, ?)
                """, (key, encoded, raw, _sha256(encoded), _sha256(raw) if raw is not None else None,
                      url, final_url, retrieved_at, now, now + ttl, snapshot_ref, artifact_namespace))
            connection.execute("""
                UPDATE crossref_control SET active_token = NULL, active_query_key = NULL, lease_expires_at = NULL
                WHERE singleton = 1 AND active_token = ? AND lease_expires_at > ?
            """, (token, now))
            row = connection.execute("SELECT * FROM crossref_cache WHERE query_key = ? AND expires_at > ?",
                                     (key, now)).fetchone() if encoded is not None and ttl > 0 else None
            return self._read_cache(row) if row is not None else None

    def renew(self, token: str, *, lease_seconds: float = 30.0) -> Reservation:
        """Extend an owned, unexpired lease; never resurrect an expired owner."""
        lease = _duration(lease_seconds, "lease_seconds", positive=True)
        with self._transaction() as connection:
            now = self._now()
            control = self._assert_owner(connection, token, now)
            expires_at = max(control["lease_expires_at"], now + lease)
            connection.execute("UPDATE crossref_control SET lease_expires_at = ? WHERE active_token = ?",
                               (expires_at, token))
            return Reservation(control["active_query_key"], token, expires_at)

    def cancel(self, token: str) -> None:
        """Release a live lease after cancellation, retaining shared rate limits."""
        with self._transaction() as connection:
            self._assert_owner(connection, token, self._now())
            connection.execute("""
                UPDATE crossref_control SET active_token = NULL, active_query_key = NULL, lease_expires_at = NULL
                WHERE active_token = ?
            """, (token,))

    def update_limits(self, *, interval_seconds: float = 0.0, retry_after_seconds: float = 0.0) -> None:
        """Persist provider header limits conservatively without acquiring a lease."""
        interval = _duration(interval_seconds, "interval_seconds")
        retry_after = _duration(retry_after_seconds, "retry_after_seconds")
        with self._transaction() as connection:
            self._set_limits(connection, self._now(), interval, retry_after)

    def abandon(self, query_key: str) -> None:
        """Withdraw a waiting key after a branch deadline, leaving active work alone.

        Waiters for an identical query share one queue position. If one abandons,
        remaining waiters can join again on their next ``reserve`` call.
        """
        key = self._key(query_key)
        with self._transaction() as connection:
            connection.execute("DELETE FROM crossref_queue WHERE query_key = ?", (key,))

    def invalidate(self, query_key: str) -> bool:
        """Remove a corrupt cache row, rechecking under the write lock.

        A concurrent worker may already have replaced the damaged row with a
        successful response. Verified content is never evicted by this method.
        """
        key = self._key(query_key)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM crossref_cache WHERE query_key = ?", (key,)).fetchone()
            if row is None:
                return False
            try:
                self._read_cache(row)
            except CorruptCacheEntryError:
                connection.execute("DELETE FROM crossref_cache WHERE query_key = ?", (key,))
                return True
            return False

    def snapshot_state(self) -> dict[str, Any]:
        """Read scheduling metadata for diagnostics, without response contents."""
        with self._connect() as connection:
            control = dict(connection.execute("SELECT * FROM crossref_control WHERE singleton = 1").fetchone())
            control["queued_keys"] = [row[0] for row in connection.execute(
                "SELECT query_key FROM crossref_queue WHERE expires_at > ? ORDER BY sequence", (self._now(),),
            )]
        return control
