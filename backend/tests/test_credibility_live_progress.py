"""Progress notes are display-only and attributed to the check that made the model call."""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.credibility import live
from app.credibility.models import CredibilityModelCall


def test_notes_inside_a_scope_count_towards_that_check():
    with live.scope("cred_live", "claim_check"):
        live.note("started")
        for purpose in ("claim_extraction", "claim_judgment", "claim_judgment"):
            live.note(purpose)
    live.note("outside_any_scope")
    summary = live.summary("cred_live")
    assert summary["claim_check"]["steps"] == {"claim_extraction": 1, "claim_judgment": 2}
    assert summary["claim_check"]["last_step"] == "claim_judgment"
    assert summary["claim_check"]["started_at"].endswith("Z")
    live.forget("cred_live")
    assert live.summary("cred_live") == {}


def test_replayed_calls_do_not_count_and_scopes_follow_their_thread():
    with live.scope("cred_live_2", "code_check"):
        CredibilityModelCall.model_validate({"purpose": "replayed", "provider": "openai", "model_version": "m"})

    def check(name):
        with live.scope("cred_live_2", name):
            live.note(f"{name}_call")
            # A nested pool sees the check only when the caller's context is carried over.
            with ThreadPoolExecutor(max_workers=1) as inner:
                inner.submit(contextvars.copy_context().run, live.note, "batch").result()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(check, ["claim_check", "reference_check"]))
    summary = live.summary("cred_live_2")
    assert summary["claim_check"]["steps"] == {"claim_check_call": 1, "batch": 1}
    assert summary["reference_check"]["steps"] == {"reference_check_call": 1, "batch": 1}
    assert "code_check" not in summary
    live.forget("cred_live_2")


def test_a_cancelled_assessment_stops_at_its_next_model_call():
    live.cancel("cred_live_3")
    with live.scope("cred_live_3", "code_check"):
        with pytest.raises(live.Cancelled):
            live.note("external_code_prefetch_review")
        with pytest.raises(live.Cancelled):
            live.raise_if_cancelled()
    # Outside its scope nothing is stopped, and forgetting the assessment clears the flag.
    live.raise_if_cancelled()
    live.forget("cred_live_3")
    with live.scope("cred_live_3", "code_check"):
        live.raise_if_cancelled()


def test_a_stopped_check_stops_alone():
    live.stop_check("cred_live_4", "code_check")
    assert live.stop_requested("cred_live_4", "code_check")
    with live.scope("cred_live_4", "code_check"):
        with pytest.raises(live.CheckStopped) as stopped:
            live.note("external_code_prefetch_review")
        assert stopped.value.check == "code_check"
    # The other checks of the same assessment carry on.
    with live.scope("cred_live_4", "claim_check"):
        live.note("claim_judgment")
    live.forget("cred_live_4")
    assert not live.stop_requested("cred_live_4", "code_check")


def test_run_checks_records_a_stopped_check_as_a_fallback_and_finishes_the_others(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from app.credibility.context import CheckContext
    from app.credibility.execution import failed_check, run_checks

    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    ctx = CheckContext(source={"type": "arxiv", "arxiv_id": "2310.07923v5"}, repo_root=Path("."),
                       generated_at="2026-10-24T00:00:00Z", ref=None, doc=None, unresolved_reason=None,
                       assessment_id="cred_live_5")

    def finished(name):
        return lambda branch: failed_check(name, branch, RuntimeError("not under test"))

    def code(branch):
        live.note("external_code_prefetch_review")
        raise AssertionError("the stopped check kept running")

    live.stop_check("cred_live_5", "code_check")
    try:
        results, meta = run_checks(ctx, SimpleNamespace(
            claims=finished("claim_check"), references=finished("reference_check"), code=code))
    finally:
        live.forget("cred_live_5")
    claims, _, stopped = results
    assert "stopped by the user" in stopped.dimension.summary
    assert stopped.dimension.check_status == "failed"
    assert meta["checks"]["code_check"]["stopped_by_user"] is True
    assert meta["checks"]["code_check"]["error"]["code"] == "STOPPED_BY_USER"
    assert "stopped" not in claims.dimension.summary
    assert "stopped_by_user" not in meta["checks"]["claim_check"]
