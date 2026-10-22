"""A paid live verifier requires explicit execution and pinned fresh inputs."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts.verify_code_usability import load_sample, main


def sample_file(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-original pinned bytes")
    value = {"is_human_gold": False, "samples": [{"sample_id": "paper", "pdf_path": str(pdf),
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}]}
    value["selection_sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()
    path = tmp_path / "samples.json"
    path.write_text(json.dumps(value))
    return path, pdf


def test_paid_execution_requires_explicit_cli_flag(tmp_path):
    path, _ = sample_file(tmp_path)
    with pytest.raises(SystemExit) as error:
        main(["--samples", str(path), "--sample-id", "paper", "--output-dir", str(tmp_path / "run")])
    assert error.value.code == 2 and not (tmp_path / "run").exists()


def test_selection_tampering_is_detected_before_retrieval(tmp_path):
    path, _ = sample_file(tmp_path)
    data = json.loads(path.read_text())
    data["samples"][0]["sample_id"] = "changed"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="selection integrity"):
        load_sample(path, "changed")


def test_changed_original_pdf_cannot_reuse_a_selection(tmp_path):
    path, pdf = sample_file(tmp_path)
    pdf.write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="Original PDF differs"):
        load_sample(path, "paper")


def test_a_sample_must_exist_exactly_once(tmp_path):
    path, _ = sample_file(tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        load_sample(path, "missing")
