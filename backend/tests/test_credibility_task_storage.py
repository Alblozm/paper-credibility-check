"""Exercise actual SQL transactions, independent process sessions and ownership."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from app.storage.credibility_tasks import (
    CredibilityStageRow,
    CredibilityTaskRow,
    CredibilityTaskStore,
    FingerprintMismatch,
    LeaseConflict,
    StaleLease,
    TaskNotFound,
)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def durable(tmp_path):
    url = f"sqlite:///{tmp_path / 'credibility.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    clock = Clock()
    store = CredibilityTaskStore(sessions, clock=clock)
    yield store, sessions, clock, url
    engine.dispose()


def snapshot(task_id="cred_storage"):
    return {
        "assessment_id": task_id, "schema_version": "credibility-0.1.0-draft",
        "analysis_mode": "credibility", "model_id": "model_standard_v1",
        "source": {"type": "arxiv", "arxiv_id": "2106.09685v2", "upload_id": None},
        "status": "queued", "stage": "queued", "progress_percent": 0,
        "created_at": "2026-10-19T00:00:00Z", "started_at": None, "completed_at": None,
        "paper": None, "report": None, "error": None, "warnings": [],
    }


def create(store, task_id="cred_storage"):
    return store.create_task(snapshot(task_id), "task-fp-v1", {"model": "test", "parser": "generic-v3"})


def test_exact_snapshot_and_checkpoints_survive_engine_restart(durable):
    store, sessions, clock, url = durable
    queued = create(store)
    queued["source"]["arxiv_id"] = "not-the-input"
    assert store.load_task("cred_storage") == snapshot()
    lease = store.claim_task("cred_storage", fingerprint="task-fp-v1")
    store.update_task("cred_storage", lease, stage="checking", progress_percent=60)
    content = {"evidence": [{"quote": "fresh evidence", "page": 2}]}
    store.save_stage("cred_storage", lease, "claim_check", "claim-input-model-prompt-v1", content)
    content["evidence"][0]["quote"] = "changed in caller"
    sessions.kw["bind"].dispose()

    engine2 = create_engine(url)
    fresh = CredibilityTaskStore(sessionmaker(bind=engine2), clock=clock)
    try:
        assert fresh.load_task("cred_storage")["progress_percent"] == 60
        assert fresh.load_stage("cred_storage", "claim_check", "claim-input-model-prompt-v1") == {
            "evidence": [{"quote": "fresh evidence", "page": 2}],
        }
        assert fresh.load_task_metadata("cred_storage")["versions"] == {
            "model": "test", "parser": "generic-v3",
        }
    finally:
        engine2.dispose()


def test_claim_is_atomic_across_independent_workers(durable):
    store, sessions, clock, _ = durable
    create(store)
    workers = 8
    barrier = Barrier(workers)

    def worker(_):
        independent = CredibilityTaskStore(sessions, clock=clock)
        barrier.wait()
        try:
            return independent.claim_task("cred_storage")
        except LeaseConflict:
            return None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(worker, range(workers)))
    assert len([lease for lease in results if lease is not None]) == 1
    assert store.load_task_metadata("cred_storage")["attempt"] == 1


def test_active_lease_cannot_be_stolen_by_resume(durable):
    store, _, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    with pytest.raises(LeaseConflict, match="active lease"):
        store.claim_task("cred_storage", resume=True)
    store.update_task("cred_storage", lease, progress_percent=20)
    assert store.list_recoverable_tasks() == []


def test_expired_lease_needs_explicit_resume_and_preserves_good_stages(durable):
    store, _, clock, _ = durable
    create(store)
    old = store.claim_task("cred_storage", lease_seconds=10)
    store.save_stage("cred_storage", old, "parsing", "parser-input-v1", {"blocks": ["page one"]})
    clock.now += 11
    assert store.list_recoverable_tasks()[0]["assessment_id"] == "cred_storage"
    with pytest.raises(LeaseConflict, match="explicit resume"):
        store.claim_task("cred_storage")
    with pytest.raises(StaleLease):
        store.update_task("cred_storage", old, progress_percent=99)
    with pytest.raises(StaleLease):
        store.renew_lease("cred_storage", old)
    fresh = store.claim_task("cred_storage", resume=True)
    assert fresh.attempt == 2 and fresh.token != old.token
    assert store.load_stage("cred_storage", "parsing", "parser-input-v1") == {"blocks": ["page one"]}
    with pytest.raises(StaleLease):
        store.save_stage("cred_storage", old, "parsing", "parser-input-v1", {"blocks": ["stale result"]})


def test_force_recovery_blocks_old_snapshot_checkpoint_finish_and_failure(durable):
    store, _, _, _ = durable
    create(store)
    old = store.claim_task("cred_storage")
    new = store.claim_task("cred_storage", force=True)
    assert new.attempt == 2
    finished = dict(snapshot(), status="completed", stage="done", progress_percent=100)
    for action in (
        lambda: store.update_task("cred_storage", old, progress_percent=90),
        lambda: store.save_stage("cred_storage", old, "claim_check", "stage-fp", {"old": True}),
        lambda: store.finish_task("cred_storage", old, finished),
        lambda: store.fail_task("cred_storage", old, {"code": "OLD_WORKER"}),
        lambda: store.release_task("cred_storage", old),
    ):
        with pytest.raises(StaleLease):
            action()
    store.update_task("cred_storage", new, progress_percent=10)
    assert store.load_stage("cred_storage", "claim_check", "stage-fp") is None
    assert store.load_task("cred_storage")["progress_percent"] == 10


def test_renewal_uses_current_database_expiry_even_with_original_lease_object(durable):
    store, _, clock, _ = durable
    create(store)
    original = store.claim_task("cred_storage", lease_seconds=10)
    clock.now += 5
    renewed = store.renew_lease("cred_storage", original, lease_seconds=30)
    assert renewed.token == original.token and renewed.attempt == original.attempt
    assert renewed.expires_at == clock.now + 30
    clock.now += 6
    # The original immutable token remains valid after heartbeat renewal.
    store.update_task("cred_storage", original, stage="checking")
    with pytest.raises(LeaseConflict):
        store.claim_task("cred_storage", resume=True)


def test_different_input_or_configuration_cannot_reuse_checkpoint(durable):
    store, sessions, _, _ = durable
    create(store)
    with pytest.raises(FingerprintMismatch):
        store.claim_task("cred_storage", fingerprint="different-paper-or-config")
    lease = store.claim_task("cred_storage")
    store.save_stage("cred_storage", lease, "claim_check", "model-and-prompt-v1", {"claims": [1]})
    assert store.load_stage("cred_storage", "claim_check", "model-and-prompt-v2") is None
    assert store.load_stage("missing-task", "claim_check", "model-and-prompt-v1") is None
    # A second task never inherits a first task's checkpoint.
    create(store, "cred_other")
    assert store.load_stage("cred_other", "claim_check", "model-and-prompt-v1") is None
    # Defensive check against a corrupt/migrated checkpoint lineage.
    with sessions.begin() as session:
        session.execute(update(CredibilityStageRow).values(task_fingerprint="wrong-lineage"))
    assert store.load_stage("cred_storage", "claim_check", "model-and-prompt-v1") is None


def test_failed_stage_does_not_hit_cache_and_success_can_replace_it(durable):
    store, _, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    store.save_stage("cred_storage", lease, "reference_check", "fp", {"error": "429"}, status="failed")
    assert store.load_stage("cred_storage", "reference_check", "fp") is None
    store.save_stage("cred_storage", lease, "reference_check", "fp", {"matched": 3})
    assert store.load_stage("cred_storage", "reference_check", "fp") == {"matched": 3}
    store.save_stage("cred_storage", lease, "reference_check", "fp", {"error": "timeout"}, status="failed")
    assert store.load_stage("cred_storage", "reference_check", "fp") == {"matched": 3}


def test_stage_and_task_revision_roll_back_together_if_checkpoint_insert_fails(durable):
    store, sessions, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    with sessions() as session:
        before = session.get(CredibilityTaskRow, "cred_storage").revision

    def reject_insert(mapper, connection, target):
        raise RuntimeError("simulated checkpoint persistence failure")

    event.listen(CredibilityStageRow, "before_insert", reject_insert)
    try:
        with pytest.raises(RuntimeError, match="persistence failure"):
            store.save_stage("cred_storage", lease, "claim_check", "fp", {"claims": [1]})
    finally:
        event.remove(CredibilityStageRow, "before_insert", reject_insert)
    with sessions() as session:
        assert session.get(CredibilityTaskRow, "cred_storage").revision == before
        assert session.scalars(select(CredibilityStageRow)).all() == []
    store.save_stage("cred_storage", lease, "claim_check", "fp", {"claims": [2]})
    assert store.load_stage("cred_storage", "claim_check", "fp") == {"claims": [2]}


def test_failed_task_resumes_without_discarding_completed_checkpoints(durable):
    store, _, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    store.save_stage("cred_storage", lease, "claim_check", "fp", {"claims": [1]})
    failed = store.fail_task("cred_storage", lease, {"code": "RATE_LIMITED"})
    assert failed["status"] == "failed" and failed["error"]["code"] == "RATE_LIMITED"
    with pytest.raises(LeaseConflict):
        store.claim_task("cred_storage")
    recovered = store.claim_task("cred_storage", resume=True)
    assert recovered.attempt == 2
    assert store.load_task("cred_storage")["error"] is None
    assert store.load_stage("cred_storage", "claim_check", "fp") == {"claims": [1]}


def test_completed_task_is_immutable_and_exact_report_is_durable(durable):
    store, _, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    finished = dict(snapshot(), status="completed", stage="done", progress_percent=100,
                    report={"three_scores": [3, 5.38, 7], "total": 5.13})
    assert store.finish_task("cred_storage", lease, finished) == finished
    finished["report"]["total"] = 0
    assert store.load_task("cred_storage")["report"]["total"] == 5.13
    for kwargs in ({}, {"resume": True}, {"force": True}):
        with pytest.raises(LeaseConflict, match="Completed"):
            store.claim_task("cred_storage", **kwargs)
    with pytest.raises(StaleLease):
        store.update_task("cred_storage", lease, stage="checking")


def test_release_returns_to_queue_and_invalidates_original_worker(durable):
    store, _, _, _ = durable
    create(store)
    old = store.claim_task("cred_storage")
    store.update_task("cred_storage", old, progress_percent=60)
    store.save_stage("cred_storage", old, "parsing", "fp", {"parsed": True})
    released = store.release_task("cred_storage", old)
    assert released["status"] == "queued" and released["progress_percent"] == 60
    assert len(store.list_recoverable_tasks()) == 1
    new = store.claim_task("cred_storage")
    assert new.attempt == 2
    with pytest.raises(StaleLease):
        store.update_task("cred_storage", old, progress_percent=90)
    assert store.load_stage("cred_storage", "parsing", "fp") == {"parsed": True}


def test_task_identity_and_non_json_data_fail_without_partial_write(durable):
    store, sessions, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    before = copy.deepcopy(store.load_task("cred_storage"))
    with pytest.raises(FingerprintMismatch):
        store.update_task("cred_storage", lease, source={"arxiv_id": "other-paper"})
    with pytest.raises(ValueError, match="finish_task"):
        store.update_task("cred_storage", lease, status="completed")
    with pytest.raises(ValueError):
        store.save_stage("cred_storage", lease, "claim_check", "fp", {"score": float("nan")})
    with pytest.raises(TypeError):
        store.save_stage("cred_storage", lease, "claim_check", "fp", {"raw": object()})
    assert store.load_task("cred_storage") == before
    with sessions() as session:
        assert session.scalars(select(CredibilityStageRow)).all() == []


def test_missing_ids_duplicate_creation_and_wrong_task_token(durable):
    store, _, _, _ = durable
    assert store.load_task("missing") is None
    assert store.load_task_metadata("missing") is None
    with pytest.raises(TaskNotFound):
        store.claim_task("missing")
    create(store)
    with pytest.raises(LeaseConflict, match="already exists"):
        create(store)
    first = store.claim_task("cred_storage")
    create(store, "cred_other")
    store.claim_task("cred_other")
    with pytest.raises(StaleLease):
        store.update_task("cred_other", first, progress_percent=100)


def test_concurrent_stage_writes_keep_both_completed_results(durable):
    store, sessions, clock, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    barrier = Barrier(2)

    def write(stage):
        independent = CredibilityTaskStore(sessions, clock=clock)
        barrier.wait()
        independent.save_stage("cred_storage", lease, stage, "fp", {"stage": stage})

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write, ["claim_check", "code_check"]))
    for stage in ("claim_check", "code_check"):
        assert store.load_stage("cred_storage", stage, "fp") == {"stage": stage}


def test_parallel_snapshot_updates_merge_instead_of_losing_fields(durable):
    store, sessions, clock, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    barrier = Barrier(2)

    def write(fields):
        independent = CredibilityTaskStore(sessions, clock=clock)
        barrier.wait()
        independent.update_task("cred_storage", lease, **fields)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write, [{"paper": {"title": "New title"}}, {"progress_percent": 60}]))
    saved = store.load_task("cred_storage")
    assert saved["paper"] == {"title": "New title"}
    assert saved["progress_percent"] == 60


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_lease_duration_cannot_poison_recovery_state(durable, seconds):
    store, _, _, _ = durable
    create(store)
    with pytest.raises(ValueError, match="finite and positive"):
        store.claim_task("cred_storage", lease_seconds=seconds)
    assert store.load_task("cred_storage")["status"] == "queued"
    lease = store.claim_task("cred_storage")
    with pytest.raises(ValueError, match="finite and positive"):
        store.renew_lease("cred_storage", lease, lease_seconds=seconds)
    store.update_task("cred_storage", lease, progress_percent=20)


def test_partial_checkpoint_is_recorded_but_retried_instead_of_reused(durable):
    store, sessions, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    store.save_stage("cred_storage", lease, "code_check", "fp", {"matched": 1}, status="partial")
    assert store.load_stage("cred_storage", "code_check", "fp") is None
    with sessions() as session:
        saved = session.get(CredibilityStageRow, ("cred_storage", "code_check", "fp"))
        assert saved.status == "partial" and saved.payload == {"matched": 1}
    store.save_stage("cred_storage", lease, "code_check", "fp", {"matched": 2})
    assert store.load_stage("cred_storage", "code_check", "fp") == {"matched": 2}


@pytest.mark.parametrize("branch_status", ["failed", "partial"])
def test_explicit_resume_only_reopens_completed_report_with_retryable_branch(durable, branch_status):
    store, _, _, _ = durable
    create(store)
    old = store.claim_task("cred_storage")
    store.save_stage("cred_storage", old, "claim_check", "fp", {"claims": [1]})
    finished = dict(snapshot(), status="completed", stage="done", progress_percent=100, report={
        "execution": {"checks": {"claim_check": {"status": "completed"},
                                  "reference_check": {"status": branch_status}}},
    })
    store.finish_task("cred_storage", old, finished)
    with pytest.raises(LeaseConflict):
        store.claim_task("cred_storage")
    with pytest.raises(LeaseConflict):
        store.claim_task("cred_storage", force=True)
    with pytest.raises(FingerprintMismatch):
        store.claim_task("cred_storage", resume=True, fingerprint="different-versions")
    assert store.load_task("cred_storage")["report"] == finished["report"]
    resumed = store.claim_task("cred_storage", resume=True, fingerprint="task-fp-v1")
    assert resumed.attempt == 2
    assert store.load_task("cred_storage")["report"] is None
    assert store.load_task("cred_storage")["status"] == "processing"
    assert store.load_stage("cred_storage", "claim_check", "fp") == {"claims": [1]}
    with pytest.raises(StaleLease):
        store.update_task("cred_storage", old, progress_percent=99)


@pytest.mark.parametrize("checkpoint_status", ["completed", "partial", "failed"])
def test_stage_record_exposes_status_payload_and_original_attempt_without_promoting_partial(durable, checkpoint_status):
    store, _, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    store.save_stage("cred_storage", lease, "reference_check", "fp", {"items": [{"id": "r1"}]},
                     status=checkpoint_status)
    record = store.load_stage_record("cred_storage", "reference_check", "fp")
    assert record == {"status": checkpoint_status, "payload": {"items": [{"id": "r1"}]}, "attempt": 1}
    record["payload"]["items"][0]["id"] = "caller mutation"
    assert store.load_stage_record("cred_storage", "reference_check", "fp")["payload"] == {"items": [{"id": "r1"}]}
    if checkpoint_status == "completed":
        assert store.load_stage("cred_storage", "reference_check", "fp") == {"items": [{"id": "r1"}]}
    else:
        assert store.load_stage("cred_storage", "reference_check", "fp") is None


def test_stage_record_rejects_wrong_task_stage_fingerprint_and_corrupt_lineage(durable):
    store, sessions, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    store.save_stage("cred_storage", lease, "reference_check", "fp", {"retained": 1}, status="partial")
    assert store.load_stage_record("missing", "reference_check", "fp") is None
    assert store.load_stage_record("cred_storage", "code_check", "fp") is None
    assert store.load_stage_record("cred_storage", "reference_check", "other-fp") is None
    with sessions.begin() as session:
        session.execute(update(CredibilityStageRow).values(task_fingerprint="another-task-lineage"))
    assert store.load_stage_record("cred_storage", "reference_check", "fp") is None


def test_partial_stage_record_survives_new_store_and_records_previous_attempt(durable):
    store, _, clock, url = durable
    create(store)
    old = store.claim_task("cred_storage")
    store.save_stage("cred_storage", old, "reference_check", "fp", {"matched": ["r1"], "not_checked": ["r2"]},
                     status="partial")
    store.release_task("cred_storage", old)
    engine = create_engine(url)
    fresh = CredibilityTaskStore(sessionmaker(bind=engine), clock=clock)
    try:
        new = fresh.claim_task("cred_storage")
        record = fresh.load_stage_record("cred_storage", "reference_check", "fp")
        assert record["attempt"] == old.attempt == 1 and new.attempt == 2
        assert record["status"] == "partial" and record["payload"]["matched"] == ["r1"]
        assert fresh.load_stage("cred_storage", "reference_check", "fp") is None
        fresh.save_stage("cred_storage", new, "reference_check", "fp", {"matched": ["r1", "r2"]})
        assert fresh.load_stage_record("cred_storage", "reference_check", "fp")["attempt"] == new.attempt
        assert fresh.load_stage("cred_storage", "reference_check", "fp") == {"matched": ["r1", "r2"]}
    finally:
        engine.dispose()


def test_recoverable_list_includes_partial_reports_failures_and_excludes_success_and_active_lease(durable):
    store, _, _, _ = durable
    create(store, "queued")
    create(store, "active")
    store.claim_task("active")
    create(store, "failed")
    failed_lease = store.claim_task("failed")
    store.fail_task("failed", failed_lease, {"code": "TEMPORARY_ERROR"})
    for task_id, branch_status in [("partial_report", "partial"), ("failed_branch", "failed"),
                                   ("complete_report", "completed")]:
        create(store, task_id)
        lease = store.claim_task(task_id)
        done = dict(snapshot(task_id), status="completed", report={
            "execution": {"checks": {"reference_check": {"status": branch_status}}},
        })
        store.finish_task(task_id, lease, done)
    assert {row["assessment_id"] for row in store.list_recoverable_tasks()} == {
        "queued", "failed", "partial_report", "failed_branch",
    }


@pytest.mark.parametrize(("status", "applicability", "recoverable"), [
    ("failed", "applicable", True), ("partial", "applicable", False),
    ("not_run", "undetermined", True), ("running", "applicable", True),
    ("completed", "undetermined", False), ("not_run", "not_applicable", False),
])
def test_legacy_report_dimensions_control_recovery_when_execution_flags_are_incorrect(
    durable, status, applicability, recoverable,
):
    store, _, _, _ = durable
    create(store)
    lease = store.claim_task("cred_storage")
    done = dict(snapshot(), status="completed", report={
        "execution": {"checks": {"claim_check": {"status": "completed"}}},
        "dimensions": [{"dimension_id": "claim_evidence", "check_status": status,
                        "applicability": applicability, "score_status": "unable_to_assess"}],
    })
    store.finish_task("cred_storage", lease, done)
    assert bool(store.list_recoverable_tasks()) is recoverable
    if recoverable:
        assert store.claim_task("cred_storage", resume=True).attempt == 2
    else:
        with pytest.raises(LeaseConflict):
            store.claim_task("cred_storage", resume=True)


def test_discarding_legacy_checkpoint_is_fenced_by_current_attempt_lease(durable):
    store, _, _, _ = durable
    create(store)
    old = store.claim_task("cred_storage")
    store.save_stage("cred_storage", old, "reference_check", "fp", {"legacy": True})
    current = store.claim_task("cred_storage", force=True)
    with pytest.raises(StaleLease):
        store.discard_stage("cred_storage", old, "reference_check", "fp")
    assert store.load_stage("cred_storage", "reference_check", "fp") == {"legacy": True}
    store.discard_stage("cred_storage", current, "reference_check", "fp")
    assert store.load_stage_record("cred_storage", "reference_check", "fp") is None
    store.save_stage("cred_storage", current, "reference_check", "fp", {"actual_failure": True}, status="failed")
    assert store.load_stage_record("cred_storage", "reference_check", "fp")["status"] == "failed"


RESTARTED = {"code": "SERVICE_RESTARTED", "message": "restarted", "retryable": True}


def test_restart_fails_orphaned_api_tasks_and_resume_reuses_their_checkpoints(durable):
    store, _, clock, _ = durable
    create(store, "never_started")
    create(store, "api_running")
    lease = store.claim_task("api_running", owner="api", fingerprint="task-fp-v1")
    assert lease.token.startswith("api:")
    store.update_task("api_running", lease, stage="checking", progress_percent=60)
    store.save_stage("api_running", lease, "claim_check", "fp", {"claims": [1]})
    create(store, "expired_untagged")
    store.claim_task("expired_untagged", lease_seconds=10)
    clock.now += 11

    assert store.fail_interrupted_tasks("api", RESTARTED) == ["api_running", "expired_untagged", "never_started"]
    for task_id in ("never_started", "api_running", "expired_untagged"):
        task = store.load_task(task_id)
        assert task["status"] == "failed" and task["stage"] == "failed" and task["report"] is None
        assert task["error"] == {**RESTARTED, "debug_id": task_id}
        assert task["completed_at"] is not None
        metadata = store.load_task_metadata(task_id)
        assert metadata["status"] == "failed" and metadata["lease_until"] is None
    assert store.load_task("api_running")["progress_percent"] == 60
    assert {row["assessment_id"] for row in store.list_recoverable_tasks()} == {
        "never_started", "api_running", "expired_untagged",
    }
    # The dead attempt is fenced off; recovery needs neither --force nor new work.
    with pytest.raises(StaleLease):
        store.update_task("api_running", lease, progress_percent=99)
    with pytest.raises(LeaseConflict, match="explicit resume"):
        store.claim_task("api_running")
    resumed = store.claim_task("api_running", resume=True, fingerprint="task-fp-v1")
    assert resumed.attempt == 2
    assert store.load_task("api_running")["status"] == "processing"
    assert store.load_task("api_running")["error"] is None
    assert store.load_stage("api_running", "claim_check", "fp") == {"claims": [1]}
    assert store.claim_task("never_started", resume=True, force=True).attempt == 1
    # A second startup pass finds nothing new and leaves the resumed worker alone.
    assert store.fail_interrupted_tasks("api", RESTARTED) == []
    store.update_task("api_running", resumed, progress_percent=70)


def test_restart_leaves_live_cli_resume_and_finished_tasks_untouched(durable):
    store, _, _, _ = durable
    create(store, "cli_resume")
    cli = store.claim_task("cli_resume", lease_seconds=60)
    create(store, "other_owner")
    store.claim_task("other_owner", owner="worker")
    create(store, "done")
    done_lease = store.claim_task("done", owner="api")
    store.finish_task("done", done_lease, dict(snapshot("done"), status="completed", stage="done"))
    create(store, "failed")
    failed_lease = store.claim_task("failed", owner="api")
    store.fail_task("failed", failed_lease, {"code": "RATE_LIMITED"})

    assert store.fail_interrupted_tasks("api", RESTARTED) == []
    assert store.load_task("cli_resume")["status"] == "processing"
    assert store.load_task("other_owner")["status"] == "processing"
    assert store.load_task("done")["status"] == "completed"
    assert store.load_task("failed")["error"]["code"] == "RATE_LIMITED"
    store.update_task("cli_resume", cli, progress_percent=30)


@pytest.mark.parametrize("owner", ["", "API", "api:x", "a" * 17])
def test_lease_owner_tag_is_validated(durable, owner):
    store, _, _, _ = durable
    create(store)
    with pytest.raises(ValueError, match="owner"):
        store.claim_task("cred_storage", owner=owner)
    with pytest.raises(ValueError, match="owner"):
        store.fail_interrupted_tasks(owner, RESTARTED)
    assert store.load_task("cred_storage")["status"] == "queued"
