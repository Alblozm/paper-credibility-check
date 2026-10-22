"""Offline safeguards for the paid corpus runner and its comparison arithmetic."""
from __future__ import annotations

import argparse
import fcntl
import math
import socket
import sys
from collections import Counter
from pathlib import Path

import pytest

from scripts import run_full_benchmark as benchmark

DATA_ROOT = benchmark.BACKEND.parent / "data"


@pytest.fixture(autouse=True)
def no_network_or_paid_client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("These benchmark tests must remain offline.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(benchmark, "DeepSeekClient", forbidden)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            {"status": "failed", "reference_total": 7.0, "program_total": 8.0},
            {"status": "pending", "reference_total": 7.0, "program_total": None},
            {"status": "completed", "reference_total": 7.0, "program_total": None},
        ],
    ],
)
def test_metrics_does_not_treat_missing_or_failed_scores_as_zero(rows):
    assert benchmark.metrics(rows) == {"total": len(rows), "paired_count": 0}


def test_metrics_one_pair_has_error_but_no_correlation():
    result = benchmark.metrics([
        {"status": "completed", "reference_total": 7.6, "program_total": 7.2},
    ])

    assert result["total"] == result["paired_count"] == 1
    assert result["mae"] == pytest.approx(0.4)
    assert result["rmse"] == pytest.approx(0.4)
    assert result["mean_signed_difference"] == pytest.approx(-0.4)
    assert result["exact_match_count"] == 0
    assert result["within_0_5_count"] == result["within_1_0_count"] == 1
    assert result["pearson_r"] is None


def test_metrics_reports_paired_accuracy_and_ignores_unusable_rows():
    rows = [
        {"status": "completed", "reference_total": 7.0, "program_total": 7.0},
        {"status": "completed", "reference_total": 8.0, "program_total": 7.5},
        {"status": "completed", "reference_total": 6.0, "program_total": 7.0},
        {"status": "completed", "reference_total": 9.0, "program_total": None},
        {"status": "failed", "reference_total": 1.0, "program_total": 10.0},
        {"status": "pending", "reference_total": 1.0, "program_total": 10.0},
    ]

    result = benchmark.metrics(rows)

    assert result["total"] == 6
    assert result["paired_count"] == 3
    assert result["mae"] == pytest.approx(0.5)
    assert result["rmse"] == pytest.approx(math.sqrt(1.25 / 3))
    assert result["mean_signed_difference"] == pytest.approx(1 / 6)
    assert result["exact_match_count"] == 1
    assert result["within_0_5_count"] == 2
    assert result["within_1_0_count"] == 3
    assert result["pearson_r"] == pytest.approx(math.sqrt(3) / 2)


@pytest.mark.parametrize("pairs", [[(7.0, 6.0), (7.0, 8.0)], [(6.0, 7.0), (8.0, 7.0)]])
def test_metrics_constant_scores_have_undefined_correlation(pairs):
    rows = [
        {"status": "completed", "reference_total": reference, "program_total": score}
        for reference, score in pairs
    ]

    result = benchmark.metrics(rows)

    assert result["paired_count"] == 2
    assert result["mae"] == result["rmse"] == 1.0
    assert result["pearson_r"] is None


def test_main_rejects_a_second_run_before_loading_inputs_or_creating_client(tmp_path, monkeypatch):
    def should_not_prepare(*args, **kwargs):
        raise AssertionError("A locked directory must be rejected before preflight.")

    monkeypatch.setattr(benchmark, "prepare", should_not_prepare)
    monkeypatch.setattr(sys, "argv", ["run_full_benchmark.py", "--output", str(tmp_path), "--execute"])
    with (tmp_path / ".run.lock").open("a") as existing_lock:
        fcntl.flock(existing_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(SystemExit, match="Another process is already using this run directory"):
                benchmark.main()
        finally:
            fcntl.flock(existing_lock.fileno(), fcntl.LOCK_UN)

    assert not (tmp_path / "run_config.json").exists()
    assert not (tmp_path / "papers").exists()


def test_real_corpus_preflight_covers_all_locked_full_text_without_generating_scores(tmp_path, monkeypatch):
    if not (DATA_ROOT / "data/manifests/pipeline_papers.json").is_file():
        pytest.skip("The benchmark corpus (../data) is not installed in this checkout.")

    observed = []
    original_prompt = benchmark.build_analysis_prompt

    def inspect_full_prompt(doc, anchored_text, rubric):
        prompt = original_prompt(doc, anchored_text, rubric)
        assert all(block.text in prompt for block in doc.blocks)
        observed.append(doc.paper.paper_id)
        return prompt

    monkeypatch.setattr(benchmark, "build_analysis_prompt", inspect_full_prompt)
    rows, config = benchmark.prepare(DATA_ROOT, tmp_path, argparse.Namespace(thinking=False))

    expected_ids = {f"paper_{number:03d}" for number in range(1, 51)}
    assert set(observed) == {row["paper_id"] for row in rows} == expected_ids
    assert len(observed) == len(rows) == config["paper_count"] == 50
    assert Counter(row["subset"] for row in rows) == {"calibration_2024": 30, "temporal_2025_2026": 20}
    assert sum(row["page_count"] for row in rows) == 1348
    assert sum(row["chunk_count"] for row in rows) == 2198
    assert config["runner_sha256"] == benchmark.digest(Path(benchmark.__file__).read_bytes())
    assert config["input_mode"] == "data_team_locked_full_structured_text_no_truncation"
    assert all(row["status"] == "pending" and row["program_total"] is None for row in rows)
    assert all(row["program_dimensions"] == {} for row in rows)
    assert all(set(row["reference_dimensions"]) == set(benchmark.DIMENSIONS) for row in rows)
    assert not (tmp_path / "papers").exists()


def test_runner_identity_change_rejects_old_checkpoint_without_overwriting_it(tmp_path, monkeypatch):
    if not (DATA_ROOT / "data/manifests/pipeline_papers.json").is_file():
        pytest.skip("The benchmark corpus (../data) is not installed in this checkout.")

    runner_copy = tmp_path / "runner_identity.py"
    runner_copy.write_bytes(Path(benchmark.__file__).read_bytes())
    monkeypatch.setattr(benchmark, "__file__", str(runner_copy))
    output = tmp_path / "run"
    args = argparse.Namespace(thinking=False)
    _, first_config = benchmark.prepare(DATA_ROOT, output, args)
    original_config_bytes = (output / "run_config.json").read_bytes()
    original_plan_bytes = (output / "input_plan.json").read_bytes()
    _, resumed_config = benchmark.prepare(DATA_ROOT, output, args)
    assert resumed_config == first_config

    runner_copy.write_bytes(runner_copy.read_bytes() + b"\n# Different runner identity for the test.\n")
    assert benchmark.digest(runner_copy.read_bytes()) != first_config["runner_sha256"]

    with pytest.raises(ValueError, match="Existing run has different inputs/settings"):
        benchmark.prepare(DATA_ROOT, output, args)

    assert (output / "run_config.json").read_bytes() == original_config_bytes
    assert (output / "input_plan.json").read_bytes() == original_plan_bytes
    assert not (output / "papers").exists()
