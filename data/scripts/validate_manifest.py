#!/usr/bin/env python3
"""Acceptance gate for the dataset manifest. Exit code 1 on any ERROR.

Enforces the rules in docs/data_dictionary.md and docs/workflow.md that a human
would otherwise have to remember. WARN = needs attention, ERROR = do not ship.
"""
from __future__ import annotations
import csv, glob, json, os, sys
from jsonschema import Draft202012Validator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "data/manifests/papers.csv")
SNAPS = os.path.join(ROOT, "data/source_snapshots")
BANDS = {"high_consensus", "mixed_or_borderline", "substantial_concerns"}
LIFECYCLE = {"candidate", "selected", "sources_verified", "parsed",
             "annotation_in_progress", "double_annotated", "adjudicated",
             "approved", "excluded"}
NEEDS_SOURCES = {"sources_verified", "parsed", "annotation_in_progress",
                 "double_annotated", "adjudicated", "approved"}
errors, warns = [], []


def main():
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    recs = {}
    paper_schema=json.load(open(os.path.join(ROOT,"schemas/paper.schema.json"),encoding="utf-8"))
    for p in glob.glob(os.path.join(SNAPS, "*", "source_record.json")):
        r = json.load(open(p, encoding="utf-8"))
        recs[r["paper_id"]] = r
        for issue in Draft202012Validator(paper_schema).iter_errors(r):
            errors.append(f"{r.get('paper_id','unknown')}: source schema error at {list(issue.path)}: {issue.message}")

    seen_ax, seen_or = {}, {}
    for r in rows:
        pid = r["paper_id"]
        if r["data_status"] not in LIFECYCLE:
            errors.append(f"{pid}: data_status '{r['data_status']}' not in workflow.md lifecycle")
        if r["sample_quality_band"] and r["sample_quality_band"] not in BANDS:
            errors.append(f"{pid}: sample_quality_band '{r['sample_quality_band']}' illegal")
        if r["decision_verified"] not in ("true", "false"):
            errors.append(f"{pid}: decision_verified must be true/false")
        if r["decision"] and r["decision_verified"] == "true" and pid not in recs:
            errors.append(f"{pid}: decision_verified=true but no source record backs it")
        if r["arxiv_id"]:
            if not r["arxiv_version"]:
                errors.append(f"{pid}: arxiv_id present but arxiv_version empty "
                              "(data_dictionary.md: version locking is mandatory)")
            seen_ax.setdefault(r["arxiv_id"], []).append(pid)
        if r["openreview_forum_id"]:
            seen_or.setdefault(r["openreview_forum_id"], []).append(pid)
        if r["data_status"] in NEEDS_SOURCES:
            rec = recs.get(pid)
            if not rec:
                errors.append(f"{pid}: status '{r['data_status']}' requires a source record")
            elif not rec.get("pdf_sha256"):
                errors.append(f"{pid}: status '{r['data_status']}' requires pdf_sha256 "
                              "(paper_structuring_spec.md acceptance gate, item 1)")
        if (r["data_status"] != "candidate" and r.get("dataset_split") != "test"
                and not r["sample_quality_band"]):
            warns.append(f"{pid}: promoted past candidate without a sample_quality_band")
        if r["decision"] and r["decision_verified"] == "false":
            warns.append(f"{pid}: decision '{r['decision']}' recorded but NOT verified — "
                         "must not be used for any decision-based analysis")
        if not r["title"]:
            errors.append(f"{pid}: empty title")

    for k, v in seen_ax.items():
        if len(v) > 1:
            errors.append(f"duplicate arxiv_id {k} on {v} (violates papers_arxiv_id_unique)")
    for k, v in seen_or.items():
        if len(v) > 1:
            errors.append(f"duplicate openreview_forum_id {k} on {v}")
    for pid, rec in recs.items():
        if pid not in {r["paper_id"] for r in rows}:
            errors.append(f"{pid}: source record exists but no manifest row")

    print(f"[validate] {len(rows)} manifest rows, {len(recs)} source records")
    for w in warns:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    print(f"[validate] {len(errors)} ERROR, {len(warns)} WARN")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
