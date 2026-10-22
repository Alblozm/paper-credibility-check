"""Provider-wide SQLite coordination, using fake time and real spawned workers."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sqlite3
import time

import pytest

from app.storage.crossref_state import (
    CorruptCacheEntryError,
    CrossrefStateStore,
    Reservation,
    StaleReservationError,
    Wait,
)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def payload(title="A Paper"):
    return {"status": "ok", "message-type": "work-list", "message": {"items": [{"title": [title]}]}}


def reserve(store, key="query", **kwargs):
    reservation = store.reserve(key, interval_seconds=0, **kwargs)
    assert isinstance(reservation, Reservation), reservation
    return reservation


def _process_queries(path, keys, start, messages, active, peak, counter_lock):
    """Each process has its own store; sleeps simulate HTTP outside transactions."""
    try:
        store = CrossrefStateStore(path, poll_seconds=0.005)
        if not start.wait(10):
            raise RuntimeError("worker start timed out")
        fetched = []
        for key in keys:
            deadline = time.time() + 10
            while store.lookup(key) is None:
                if time.time() > deadline:
                    raise RuntimeError("query coordination timed out")
                decision = store.reserve(key, interval_seconds=0, lease_seconds=5)
                if isinstance(decision, Wait):
                    time.sleep(min(decision.seconds, 0.01))
                    continue
                with counter_lock:
                    active.value += 1
                    peak.value = max(peak.value, active.value)
                time.sleep(0.025)
                with counter_lock:
                    active.value -= 1
                body = payload(key)
                store.finish(decision.token, http_status=200, payload=body, raw=json.dumps(body).encode())
                fetched.append(key)
            assert store.lookup(key).payload == payload(key)
        messages.put(("ok", fetched))
    except BaseException as exc:
        messages.put(("error", repr(exc)))
        raise


def _read_in_new_process(path, messages):
    store = CrossrefStateStore(path)
    cached = store.lookup("query")
    messages.put((cached.payload, cached.raw, cached.normalized_only, cached.origin_snapshot_ref))


def test_real_processes_serialize_different_queries_and_single_flight_duplicates(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    path = str(tmp_path / "provider.sqlite3")
    start, messages = ctx.Event(), ctx.Queue()
    active, peak, lock = ctx.Value("i", 0), ctx.Value("i", 0), ctx.Lock()
    keys = [["shared", f"unique-{index}", "shared"] for index in range(4)]
    processes = [ctx.Process(target=_process_queries, args=(path, group, start, messages, active, peak, lock))
                 for group in keys]
    for process in processes:
        process.start()
    start.set()
    try:
        results = [messages.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(10)
        assert all(process.exitcode == 0 for process in processes)
        assert all(status == "ok" for status, _ in results), results
        fetched = [key for _, group in results for key in group]
        assert sorted(fetched) == ["shared", "unique-0", "unique-1", "unique-2", "unique-3"]
        assert peak.value == 1
        assert active.value == 0
        assert all(CrossrefStateStore(path).lookup(key) is not None for key in fetched)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
        messages.close()


def test_success_is_readable_in_a_new_process_with_original_raw_bytes(tmp_path):
    path = tmp_path / "provider.sqlite3"
    store = CrossrefStateStore(path)
    response = payload()
    raw = json.dumps(response, indent=2).encode()
    store.finish(reserve(store).token, payload=response, raw=raw, http_status=200,
                 snapshot_ref="snapshot:v1:origin", artifact_namespace="artifacts-a")
    ctx = multiprocessing.get_context("spawn")
    messages = ctx.Queue()
    process = ctx.Process(target=_read_in_new_process, args=(str(path), messages))
    process.start()
    try:
        assert messages.get(timeout=15) == (response, raw, False, "snapshot:v1:origin")
        process.join(10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        messages.close()


def test_state_path_is_independent_of_task_database_url(tmp_path, monkeypatch):
    path = tmp_path / "crossref" / "state.sqlite3"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///task-one.sqlite3")
    first = CrossrefStateStore(path)
    first.finish(reserve(first).token, http_status=200, payload=payload())
    monkeypatch.setenv("DATABASE_URL", "sqlite:///different-task.sqlite3")
    second = CrossrefStateStore(path)
    assert second.lookup("query").payload == payload()
    assert not (tmp_path / "task-one.sqlite3").exists()
    assert not (tmp_path / "different-task.sqlite3").exists()


def test_expired_lease_is_recoverable_and_old_owner_cannot_write_or_release(tmp_path):
    clock = Clock()
    first = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    old = reserve(first, "old", lease_seconds=5)
    clock.now += 5
    second = CrossrefStateStore(first.state_path, clock=clock)
    current = reserve(second, "new", lease_seconds=10)
    for attempt in (
        lambda: first.finish(old.token, http_status=200, payload=payload(), retry_after_seconds=900),
        lambda: first.cancel(old.token),
        lambda: first.renew(old.token),
    ):
        with pytest.raises(StaleReservationError):
            attempt()
    assert first.lookup("old") is None
    state = first.snapshot_state()
    assert state["active_token"] == current.token and state["cooldown_until"] == 0
    assert second.finish(current.token, http_status=200, payload=payload()).query_key == "new"


def test_renew_only_extends_live_lease_and_cancel_retains_spacing(tmp_path):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    initial = store.reserve("query", interval_seconds=3, lease_seconds=5)
    clock.now += 2
    extended = store.renew(initial.token, lease_seconds=10)
    assert extended.token == initial.token and extended.expires_at == 112
    assert store.renew(initial.token, lease_seconds=1).expires_at == 112
    store.cancel(initial.token)
    assert store.reserve("other", interval_seconds=0) == Wait(1, "spacing")
    clock.now += 1
    assert isinstance(store.reserve("other", interval_seconds=0), Reservation)


def test_fifo_deduplicates_waiting_keys_and_different_queries_do_not_overlap(tmp_path):
    clock = Clock()
    first = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    second = CrossrefStateStore(first.state_path, clock=clock)
    initial = reserve(first, "running")
    assert second.reserve("first", interval_seconds=0).reason == "queued"
    assert second.reserve("second", interval_seconds=0).reason == "queued"
    assert first.reserve("first", interval_seconds=0).reason == "queued"
    assert first.reserve("running", interval_seconds=0).reason == "queued"
    assert first.snapshot_state()["queued_keys"] == ["first", "second"]
    first.finish(initial.token, http_status=500)
    assert second.reserve("second", interval_seconds=0).reason == "queued"
    first_query = reserve(first, "first")
    assert second.reserve("second", interval_seconds=0).reason == "queued"
    first.finish(first_query.token, http_status=200, payload=payload())
    assert first.reserve("first", interval_seconds=0) == Wait(0, "cached")
    assert reserve(second, "second").query_key == "second"


def test_abandoned_waiter_expires_without_blocking_other_queries(tmp_path):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock, queue_ttl_seconds=5)
    active = reserve(store, "active", lease_seconds=20)
    store.reserve("abandoned", interval_seconds=0)
    clock.now += 4
    store.reserve("live", interval_seconds=0)
    clock.now += 2
    store.cancel(active.token)
    assert reserve(store, "live").query_key == "live"


def test_explicit_abandon_removes_only_shared_queue_position(tmp_path):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    active = reserve(store, "active")
    store.reserve("expired-branch", interval_seconds=0)
    store.reserve("other", interval_seconds=0)
    store.update_limits(interval_seconds=3, retry_after_seconds=2)
    before = store.snapshot_state()
    store.abandon("expired-branch")
    store.abandon("active")
    after = store.snapshot_state()
    assert after["queued_keys"] == ["other"]
    assert after["active_token"] == active.token
    assert after["cooldown_until"] == before["cooldown_until"]
    assert after["interval_seconds"] == before["interval_seconds"]
    store.reserve("expired-branch", interval_seconds=0)
    assert store.snapshot_state()["queued_keys"] == ["other", "expired-branch"]


def test_cooldown_persists_across_instances_and_preserves_fifo_during_long_wait(tmp_path):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock, queue_ttl_seconds=5)
    initial = reserve(store, "limited")
    assert store.finish(initial.token, http_status=429, payload={"error": "rate limited"},
                        raw=b"not success", retry_after_seconds=60) is None
    assert store.lookup("limited") is None
    assert store.reserve("first", interval_seconds=0) == Wait(60, "cooldown")
    other = CrossrefStateStore(store.state_path, clock=clock, queue_ttl_seconds=5)
    assert other.reserve("second", interval_seconds=0) == Wait(60, "cooldown")
    clock.now += 59
    assert other.reserve("third", interval_seconds=0) == Wait(1, "cooldown")
    clock.now += 1
    assert other.reserve("second", interval_seconds=0).reason == "queued"
    assert reserve(store, "first").query_key == "first"


def test_shared_header_limits_only_slow_down_and_do_not_increase_concurrency(tmp_path):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    initial = store.reserve("one", interval_seconds=0.2)
    store.finish(initial.token, http_status=200, payload=payload(), interval_seconds=2)
    other = CrossrefStateStore(store.state_path, clock=clock)
    other.update_limits(interval_seconds=0.1)
    assert other.reserve("two", interval_seconds=0.1) == Wait(2, "spacing")
    clock.now += 2
    second = reserve(other, "two")
    assert other.snapshot_state()["interval_seconds"] == 2
    assert store.reserve("three", interval_seconds=0).reason == "queued"
    other.finish(second.token, http_status=503, retry_after_seconds=8)
    assert store.reserve("three", interval_seconds=0) == Wait(8, "cooldown")
    other.update_limits(retry_after_seconds=1)
    assert store.reserve("three", interval_seconds=0) == Wait(8, "cooldown")


@pytest.mark.parametrize("status", [None, 204, 301, 400, 403, 404, 429, 500, 503])
def test_failure_never_becomes_a_success_cache_and_is_immediately_retryable(tmp_path, status):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    initial = reserve(store)
    assert store.finish(initial.token, http_status=status, payload={"error": "failed"}) is None
    assert store.lookup("query") is None
    assert isinstance(store.reserve("query", interval_seconds=0), Reservation)


def test_empty_invalid_http_200_is_not_cached(tmp_path):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    store.finish(reserve(store).token, http_status=200)
    assert store.lookup("query") is None
    assert isinstance(store.reserve("query", interval_seconds=0), Reservation)


def test_cache_ttl_expiration_and_race_safe_reserve(tmp_path):
    clock = Clock()
    store = CrossrefStateStore(tmp_path / "state.sqlite3", clock=clock)
    result = store.finish(reserve(store).token, http_status=200, payload=payload(), ttl_seconds=10)
    assert result.created_at == 100 and result.expires_at == 110
    assert store.reserve("query", interval_seconds=0) == Wait(0, "cached")
    clock.now = 110
    assert store.lookup("query") is None
    assert isinstance(store.reserve("query", interval_seconds=0), Reservation)


def test_zero_ttl_does_not_cache(tmp_path):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    assert store.finish(reserve(store).token, http_status=200, payload=payload(), ttl_seconds=0) is None
    assert store.lookup("query") is None


def test_origin_snapshot_is_only_usable_in_same_namespace_and_raw_none_is_explicit(tmp_path):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    cached = store.finish(reserve(store).token, http_status=200, payload=payload(),
                          snapshot_ref="snapshot:v1:original", artifact_namespace="root-one")
    assert cached.raw is None and cached.raw_sha256 is None and cached.normalized_only
    assert cached.local_snapshot_ref("root-one") == "snapshot:v1:original"
    assert cached.local_snapshot_ref("root-two") is None
    assert cached.local_snapshot_ref("") is None
    # Portable normalized content can be used to create an honest normalized-only observation.
    assert CrossrefStateStore(store.state_path).lookup("query").payload == payload()


def test_original_raw_is_not_canonicalized_or_replaced(tmp_path):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    response = payload("非 ASCII title")
    raw = (json.dumps(response, indent=3, ensure_ascii=False) + "\n").encode()
    cached = store.finish(reserve(store).token, http_status=200, payload=response, raw=raw,
                          url="https://api.crossref.org/works?query=title",
                          final_url="https://api.crossref.org/works?query=title", retrieved_at="2026-10-19T00:00:00Z")
    assert cached.raw == raw and not cached.normalized_only
    assert cached.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert cached.url == cached.final_url and cached.retrieved_at == "2026-10-19T00:00:00Z"


@pytest.mark.parametrize("body", [b"bad-json", b"[]", b'{"different":true}', b'{"status":NaN}', b"\xff"])
def test_raw_must_be_real_json_matching_the_successful_payload(tmp_path, body):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    initial = reserve(store)
    with pytest.raises(ValueError):
        store.finish(initial.token, http_status=200, payload=payload(), raw=body)
    assert store.lookup("query") is None
    # Invalid save did not release or mutate the current reservation.
    assert store.snapshot_state()["active_token"] == initial.token
    store.cancel(initial.token)


def test_caller_controlled_raw_size_limit(tmp_path):
    store = CrossrefStateStore(tmp_path / "state.sqlite3", max_raw_bytes=2)
    initial = reserve(store)
    with pytest.raises(ValueError, match="max_raw_bytes"):
        store.finish(initial.token, http_status=200, payload=payload(), raw=json.dumps(payload()).encode())
    assert store.lookup("query") is None
    store.cancel(initial.token)


@pytest.mark.parametrize("field", ["payload", "payload_sha256", "raw", "raw_sha256"])
def test_corrupted_persisted_content_is_detected_on_lookup_and_reserve(tmp_path, field):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    store.finish(reserve(store).token, http_status=200, payload=payload(), raw=json.dumps(payload()).encode())
    corrupt = b'{"altered":true}' if field in {"payload", "raw"} else "0" * 64
    with sqlite3.connect(store.state_path) as connection:
        connection.execute(f"UPDATE crossref_cache SET {field} = ? WHERE query_key = ?", (corrupt, "query"))
    with pytest.raises(CorruptCacheEntryError):
        store.lookup("query")
    with pytest.raises(CorruptCacheEntryError):
        store.reserve("query")
    assert store.invalidate("query")
    assert store.lookup("query") is None
    assert isinstance(store.reserve("query", interval_seconds=0), Reservation)


def test_invalidate_never_deletes_valid_or_concurrently_replaced_content(tmp_path):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    assert not store.invalidate("absent")
    store.finish(reserve(store).token, http_status=200, payload=payload())
    assert not store.invalidate("query")
    assert store.lookup("query").payload == payload()


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_invalid_durations_cannot_mutate_shared_state(tmp_path, value):
    store = CrossrefStateStore(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError):
        store.reserve("query", interval_seconds=value)
    with pytest.raises(ValueError):
        store.reserve("query", lease_seconds=value)
    with pytest.raises(ValueError):
        store.update_limits(retry_after_seconds=value)
    assert store.snapshot_state()["active_token"] is None


def test_in_memory_database_is_rejected():
    with pytest.raises(ValueError, match="on-disk"):
        CrossrefStateStore(":memory:")
