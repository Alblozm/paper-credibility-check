"""Independent integration review: no provider network or model calls."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.config import get_settings
from app.credibility import crossref_http, references
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.snapshots import SnapshotError, read_snapshot
from app.storage.crossref_state import Reservation


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(references, "time", SimpleNamespace(
        time=value.time, monotonic=value.monotonic, sleep=value.sleep,
    ))
    return value


def metadata(title="An example paper", doi="10.1000/example"):
    return CredibilityBibliographicMetadata(title=title, authors=[], year=2024, doi=doi, arxiv_id=None)


def successful_response(url):
    return httpx.Response(200, json={"status": "ok", "message": {"DOI": "10.1000/example",
                                                             "title": ["An example paper"]}},
                          request=httpx.Request("GET", url))


@pytest.mark.parametrize("retry_after", [None, "invalid", "NaN"])
def test_unknown_retry_after_uses_persistent_fallback_without_crashing(monkeypatch, clock, retry_after):
    calls = []

    def limited(url, **kwargs):
        calls.append(url)
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        return httpx.Response(429, json={"message": "limited"}, headers=headers,
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(references.httpx, "get", limited)
    lookup = references.CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=0)
    result = lookup.lookup(metadata())
    assert result.fetch_status == "rate_limited" and result.http_status == 429
    assert lookup._state().snapshot_state()["cooldown_until"] >= clock.now + 30
    assert len(calls) == 1


def test_distinct_unicode_queries_never_share_a_cache_key():
    first_key, first_url = references.CrossrefLookup._request_for(metadata("机器学习的可靠性", doi=None))
    second_key, second_url = references.CrossrefLookup._request_for(metadata("气候系统的建模", doi=None))
    assert first_url != second_url
    assert first_key != second_key


def test_cache_rebuilds_honest_local_snapshot_in_another_artifact_root(monkeypatch, tmp_path, clock):
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        return successful_response(url)

    monkeypatch.setattr(references.httpx, "get", getter)
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "first-root"))
    first = references.CrossrefLookup(min_interval_seconds=0).lookup(metadata())
    original = read_snapshot(first.snapshot_ref)
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "second-root"))
    second = references.CrossrefLookup(min_interval_seconds=0).lookup(metadata())
    rebuilt = read_snapshot(second.snapshot_ref)
    assert second.is_cached and len(calls) == 1
    assert second.snapshot_ref != first.snapshot_ref
    assert rebuilt.raw == original.raw
    assert rebuilt.normalized_json == original.normalized_json
    assert rebuilt.manifest["parent_ref"] is None
    assert rebuilt.manifest["metadata"]["origin_snapshot_ref"] == first.snapshot_ref
    assert rebuilt.manifest["metadata"]["normalized_only"] is False
    with pytest.raises(SnapshotError):
        read_snapshot(first.snapshot_ref)


def test_observed_429_cooldown_survives_snapshot_write_failure(monkeypatch, clock):
    def getter(url, **kwargs):
        return httpx.Response(429, json={"message": "limited"}, headers={"Retry-After": "45"},
                              request=httpx.Request("GET", url))

    def broken_snapshot(**kwargs):
        raise SnapshotError("injected evidence store failure")

    monkeypatch.setattr(references.httpx, "get", getter)
    monkeypatch.setattr(references, "capture_snapshot", broken_snapshot)
    lookup = references.CrossrefLookup(min_interval_seconds=0, retry_wait_budget_seconds=0)
    with pytest.raises(SnapshotError, match="injected"):
        lookup.lookup(metadata())
    state = lookup._state().snapshot_state()
    assert state["active_token"] is None
    assert state["cooldown_until"] >= clock.now + 45


def test_cancellation_while_queued_abandons_only_waiter_not_active_lease(monkeypatch, clock):
    lookup = references.CrossrefLookup(min_interval_seconds=0)
    state = lookup._state()
    active = state.reserve("another-query", interval_seconds=0)
    assert isinstance(active, Reservation)

    def cancelled(seconds):
        raise KeyboardInterrupt("injected cancellation while waiting")

    monkeypatch.setattr(references, "time", SimpleNamespace(
        time=clock.time, monotonic=clock.monotonic, sleep=cancelled,
    ))
    with pytest.raises(KeyboardInterrupt, match="injected"):
        lookup.lookup(metadata())
    after = state.snapshot_state()
    assert after["active_token"] == active.token
    assert after["queued_keys"] == []


def test_redirects_do_not_reset_overall_branch_deadline(monkeypatch, clock):
    monkeypatch.setattr(get_settings(), "crossref_branch_deadline_seconds", 1.0)
    monkeypatch.setattr(crossref_http, "time", SimpleNamespace(monotonic=clock.monotonic), raising=False)
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        # Each individual operation completes inside its initial one-second
        # timeout, but the redirect chain's cumulative duration exceeds it.
        clock.now += 0.6
        if len(calls) < 3:
            return httpx.Response(302, headers={"Location": f"/works/10.1000/hop-{len(calls)}"},
                                  request=httpx.Request("GET", url))
        return successful_response(url)

    monkeypatch.setattr(references.httpx, "get", getter)
    result = references.CrossrefLookup(min_interval_seconds=0).lookup(metadata())
    assert result.fetch_status == "timeout"
    assert len(calls) == 2


def test_deadline_is_rechecked_after_waiting_for_sqlite_reservation(monkeypatch, clock):
    monkeypatch.setattr(get_settings(), "crossref_branch_deadline_seconds", 1.0)
    lookup = references.CrossrefLookup(min_interval_seconds=0)
    state = lookup._state()
    original_reserve = state.reserve

    def delayed_reserve(*args, **kwargs):
        reservation = original_reserve(*args, **kwargs)
        clock.now += 2  # A busy SQLite writer can delay BEGIN IMMEDIATE.
        return reservation

    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        return successful_response(url)

    monkeypatch.setattr(state, "reserve", delayed_reserve)
    monkeypatch.setattr(lookup, "_state", lambda: state)
    monkeypatch.setattr(references.httpx, "get", getter)
    result = lookup.lookup(metadata())
    assert result.fetch_status == "timeout"
    assert calls == []
    assert state.snapshot_state()["active_token"] is None
