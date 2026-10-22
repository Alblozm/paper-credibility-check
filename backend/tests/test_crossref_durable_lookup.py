"""Integration boundaries between durable provider state, snapshots and transport."""
from __future__ import annotations

import json
import sqlite3

import httpx
from pydantic import SecretStr

from app.config import get_settings
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.references import CrossrefLookup
from app.credibility.runtime import request_versions
from app.credibility.snapshots import read_snapshot


def metadata(doi='10.1000/one'):
    return CredibilityBibliographicMetadata(doi=doi, title=None, authors=[], year=None, arxiv_id=None)


def response(url, title='A title'):
    return httpx.Response(200, json={'message': {'DOI': '10.1000/one', 'title': [title]}},
                          request=httpx.Request('GET', url))


def test_success_cache_is_read_by_new_lookup_before_other_query_cooldown(monkeypatch):
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        return response(url) if len(calls) == 1 else httpx.Response(
            429, headers={'retry-after': '120'}, request=httpx.Request('GET', url))

    monkeypatch.setattr('app.credibility.references.httpx.get', get)
    first = CrossrefLookup(retry_wait_budget_seconds=0)
    assert first.lookup(metadata()).fetch_status == 'ok'
    assert first.lookup(metadata('10.1000/two')).fetch_status == 'rate_limited'
    second = CrossrefLookup(retry_wait_budget_seconds=0)
    cached = second.lookup(metadata())
    assert cached.fetch_status == 'ok' and cached.is_cached and len(calls) == 2
    assert read_snapshot(cached.snapshot_ref).raw is not None


def test_corrupt_shared_cache_is_refetched_instead_of_used_as_verified(monkeypatch):
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        return response(url)

    monkeypatch.setattr('app.credibility.references.httpx.get', get)
    first = CrossrefLookup()
    assert first.lookup(metadata()).fetch_status == 'ok'
    with sqlite3.connect(get_settings().crossref_state_path) as conn:
        conn.execute('UPDATE crossref_cache SET raw = ?', (b'{"tampered":true}',))
    fresh = CrossrefLookup().lookup(metadata())
    assert not fresh.is_cached and fresh.fetch_status == 'ok' and len(calls) == 2
    assert json.loads(read_snapshot(fresh.snapshot_ref).raw)['message']['DOI'] == '10.1000/one'


def test_sqlite_wait_cannot_send_request_after_branch_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('app.credibility.references.time.monotonic', lambda: clock[0])
    monkeypatch.setattr(get_settings(), 'crossref_branch_deadline_seconds', 1)
    lookup = CrossrefLookup()
    store = lookup._state()
    reserve = store.reserve

    def slow_reserve(*args, **kwargs):
        result = reserve(*args, **kwargs)
        clock[0] += 2
        return result

    monkeypatch.setattr(store, 'reserve', slow_reserve)
    monkeypatch.setattr(lookup, '_state', lambda: store)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('No HTTP after deadline')

    monkeypatch.setattr('app.credibility.references.httpx.get', forbidden)
    result = lookup.lookup(metadata())
    assert result.fetch_status == 'timeout'
    assert read_snapshot(result.snapshot_ref).manifest['metadata']['request_sent'] is False
    assert store.snapshot_state()['active_token'] is None


def test_contact_is_excluded_from_settings_and_task_fingerprint(monkeypatch):
    monkeypatch.setattr(get_settings(), 'crossref_mailto', SecretStr('project@example.org'))
    assert 'crossref_mailto' not in get_settings().model_dump()
    assert 'project@example.org' not in json.dumps(request_versions())
    assert 'project@example.org' not in repr(get_settings())
