#!/usr/bin/env python3
"""Rebuild all round-two reports/SQL from preserved evidence cases.

This is intentionally offline: it never changes extraction or external-query
evidence.  It applies the current provisional rubric, exports one seed per
paper, then creates a single-transaction backend seed and human-readable CSV.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from credibility_r2.core import build_report, load_json, save_json
from credibility_r2.presentation import export_seed


def main():
    case_dir = ROOT / "data/credibility_cases/ai_batch_v0.1"
    report_dir = ROOT / "data/credibility_reports/ai_batch_v0.1"
    rubric = load_json(ROOT / "rubrics/credibility_v0.1/rubric.provisional-ai-batch.json")
    report_dir.mkdir(parents=True, exist_ok=True)

    rows, bodies = [], []
    with tempfile.TemporaryDirectory(prefix="pcc-credibility-sql-") as temp_dir:
      for case_path in sorted(case_dir.glob("paper_*.json")):
        case = load_json(case_path)
        report = build_report(case, rubric)
        paper_id = case["paper_version"]["paper_id"]
        save_json(report_dir / f"{paper_id}.report.json", report)
        seed_path = Path(temp_dir) / f"{paper_id}.sql"
        export_seed(case, report, seed_path)
        seed_lines = seed_path.read_text(encoding="utf-8").splitlines()
        bodies.extend(line for line in seed_lines if line not in ("BEGIN;", "COMMIT;") and not line.startswith("-- Run after") and not line.startswith("-- Synthetic"))
        dimensions = {d["dimension_id"]: d for d in report["dimensions"]}
        rows.append({
            "paper_id": paper_id,
            "paper_version_id": report["paper_version_id"],
            "claim_score": dimensions["claim_evidence_consistency"]["score"],
            "reference_score": dimensions["reference_verification"]["score"],
            "code_score": dimensions["code_reproduction_signals"]["score"],
            "overall_score": report["overall_score"],
            "overall_category": report["overall_category"],
            "scope_complete": report["scope_complete"],
            "rubric_status": report["rubric_status"],
            "failure_reasons": {d["dimension_id"]: d["failure_reasons"] for d in report["dimensions"]},
        })

    if len(rows) != 50:
        raise RuntimeError(f"expected 50 cases, found {len(rows)}")
    for row in rows:
        required = (row["claim_score"], row["reference_score"], row["code_score"], row["overall_score"])
        if any(value is None for value in required):
            raise RuntimeError(f"incomplete provisional score: {row['paper_id']}")

    save_json(report_dir / "summary.json", rows)
    csv_fields = [key for key in rows[0] if key != "failure_reasons"]
    with (report_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row[key] for key in csv_fields} for row in rows)

    combined = ["-- Provisional AI credibility batch. Run after 001, 020 and 030.", "-- Technical failures are low-verifiability signals, not findings of misconduct.", "BEGIN;", *bodies, "COMMIT;"]
    (ROOT / "sql/032_credibility_seed_all_ai_batch.sql").write_text("\n".join(combined) + "\n", encoding="utf-8")
    print(json.dumps({"reports": len(rows), "all_scored": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
