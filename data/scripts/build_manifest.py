#!/usr/bin/env python3
"""Merge curated core samples + fetched source records into papers.csv and dataset.yaml.

Merge semantics, deliberately: this script UPDATES and INSERTS rows, it never
deletes. A row already in papers.csv that has no source record and is not in the
curated list is left exactly as it is (that is how paper_003 survives), so hand
edits are not silently destroyed by a re-run.
"""
from __future__ import annotations
import csv, datetime, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minyaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "data/curated/core_samples.yaml")
SNAPS = os.path.join(ROOT, "data/source_snapshots")
CSV_PATH = os.path.join(ROOT, "data/manifests/papers.csv")
YAML_PATH = os.path.join(ROOT, "data/manifests/dataset.yaml")
RELEASE_PATH = os.path.join(ROOT, "data/manifests/release.json")
COLS = ["paper_id", "title", "arxiv_id", "arxiv_version", "arxiv_url",
        "openreview_forum_id", "openreview_url", "venue", "year", "decision",
        "decision_verified", "sample_quality_band", "challenge_tags",
        "selection_reason", "dataset_split", "data_status", "assigned_to", "retrieved_at"]
BANDS = {"high_consensus", "mixed_or_borderline", "substantial_concerns"}
ACTIVE = {"selected", "sources_verified", "parsed", "annotation_in_progress",
          "double_annotated", "adjudicated", "approved"}


def load_existing():
    if not os.path.exists(CSV_PATH):
        return {}
    return {r["paper_id"]: dict(r) for r in csv.DictReader(open(CSV_PATH, encoding="utf-8"))}


def main():
    release = json.load(open(RELEASE_PATH, encoding="utf-8"))
    rows = load_existing()
    core = {c["paper_id"]: c for c in minyaml.load_list(CORE)}
    touched_core = touched_new = 0

    for path in sorted(glob.glob(os.path.join(SNAPS, "*", "source_record.json"))):
        rec = json.load(open(path, encoding="utf-8"))
        pid = rec["paper_id"]
        row = rows.get(pid, {c: "" for c in COLS})
        row["paper_id"] = pid
        row["title"] = rec.get("title") or row.get("title", "")
        row["arxiv_id"] = rec.get("arxiv_id") or row.get("arxiv_id", "")
        row["arxiv_version"] = rec.get("arxiv_version") or row.get("arxiv_version", "")
        row["arxiv_url"] = rec.get("abs_url") or row.get("arxiv_url", "")
        row["venue"] = rec.get("venue") or row.get("venue", "")
        row["year"] = str(rec.get("publication_year") or row.get("year", "") or "")
        row["retrieved_at"] = rec.get("retrieved_at") or row.get("retrieved_at", "")
        row["dataset_split"] = rec.get("dataset_split") or row.get("dataset_split", "") or "calibration"
        row.setdefault("assigned_to", "")
        if pid in core:                       # curated fields always win
            c = core[pid]
            row["arxiv_version"] = c.get("lock_version") or row["arxiv_version"]
            row["openreview_forum_id"] = c.get("openreview_forum_id") or ""
            row["openreview_url"] = (f"https://openreview.net/forum?id={c['openreview_forum_id']}"
                                     if c.get("openreview_forum_id") else "")
            row["decision"] = c.get("decision") or ""
            row["decision_verified"] = "true" if c.get("decision_verified") else "false"
            row["sample_quality_band"] = c.get("sample_quality_band") or ""
            row["challenge_tags"] = c.get("challenge_tags") or ""
            row["selection_reason"] = c.get("selection_reason") or ""
            # A verified downstream state must not be downgraded by the original
            # curation record when the manifest is rebuilt.
            row["data_status"] = rec.get("data_status") or c.get("data_status") or "selected"
            touched_core += 1
        else:
            row["openreview_forum_id"] = rec.get("openreview_forum_id") or row.get("openreview_forum_id", "") or ""
            row["openreview_url"] = (rec.get("forum_url") or row.get("openreview_url", "") or "")
            row["decision"] = rec.get("decision") or row.get("decision", "") or ""
            verified = rec.get("decision_verified")
            row["decision_verified"] = ("true" if verified is True else "false" if verified is False
                                        else row.get("decision_verified", "") or "false")
            row["sample_quality_band"] = rec.get("sample_quality_band") or row.get("sample_quality_band", "") or ""
            if row["dataset_split"] == "test":
                row["challenge_tags"] = "recent_temporal_holdout"
                row["selection_reason"] = (
                    "Selected before scoring from the official ICLR proceedings as part of the "
                    "2025/2026 temporal holdout; no quality band was assigned post hoc.")
            else:
                row["challenge_tags"] = row.get("challenge_tags", "") or "auto_discovered"
                row["selection_reason"] = row.get("selection_reason", "") or (
                    "Auto-discovered via the arXiv comment field naming the venue. Venue is "
                    "evidenced; decision, reviews and quality band are not. Requires human "
                    "triage before it can be promoted out of candidate.")
            row["data_status"] = rec.get("data_status") or row.get("data_status", "") or "candidate"
            touched_new += 1
        rows[pid] = row

    for pid in rows:
        for c in COLS:
            rows[pid].setdefault(c, "")

    order = sorted(rows, key=lambda p: (len(p), p))
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        for pid in order:
            w.writerow({c: rows[pid].get(c, "") for c in COLS})

    calib = [p for p in order if rows[p]["data_status"] in ACTIVE
             and rows[p]["sample_quality_band"] in BANDS and rows[p]["dataset_split"]=="calibration"]
    tests = [p for p in order if rows[p]["data_status"] in ACTIVE and rows[p]["dataset_split"]=="test"]
    cands = [p for p in order if rows[p]["data_status"] == "candidate" and p not in calib]
    parked = [p for p in order if rows[p]["data_status"] == "candidate"
              and not rows[p]["arxiv_id"]]
    cands = [p for p in cands if p not in parked]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    y = [f"# Generated by scripts/build_manifest.py on {stamp}. Edit the inputs, not this file:",
         "#   data/curated/core_samples.yaml  and  data/source_snapshots/*/source_record.json",
         f"dataset_id: {release['dataset_id']}", f"dataset_version: \"{release['dataset_version']}\"",
         f"schema_version: \"{release['schema_version']}\"", f"rubric_id: {release['rubric_id']}",
         f"rubric_version: \"{release['rubric_version']}\"", f"target_paper_count: {max(30, len(rows))}",
         f"status: {release['status']}", f"gold_status: {release['gold_status']}",
         f"test_set_status: {release['test_set_status']}",
         f"recommended_future_test_count: {release['recommended_future_test_count']}", "",
         "# calibration = the original 30-paper, quality-banded ICLR 2024 reference set.",
         "# test        = 20 later ICLR proceedings papers selected before scoring",
         "#               (15 from 2026 and 5 from 2025) for a temporal holdout check.",
         "splits:", "  calibration:"]
    y += [f"    - {p}" for p in calib] or ["    []"]
    y += ["  test:"] + ([f"    - {p}" for p in tests] or ["    []"])
    y += ["",
          f"# {len(cands)} discovered candidates: venue evidenced from the arXiv comment field,",
          "# decision / reviews / band NOT established. Not scorable, not in any split.",
          "candidate_pool:"]
    y += [f"    - {p}" for p in cands] or ["    []"]
    if parked:
        y += ["", "# No arXiv record and unverified provenance. Excluded from the database.",
              "pending_verification:"] + [f"    - {p}" for p in parked]
    y += ["", "counts:", f"  calibration: {len(calib)}", f"  test: {len(tests)}", f"  candidate_pool: {len(cands)}",
          f"  pending_verification: {len(parked)}", f"  total_rows: {len(order)}"]
    open(YAML_PATH, "w", encoding="utf-8").write("\n".join(y) + "\n")

    print(f"[manifest] papers.csv: {len(order)} rows "
          f"({touched_core} curated, {touched_new} from source records)")
    print(f"[manifest] dataset.yaml: calibration={len(calib)} "
          f"candidate_pool={len(cands)} pending_verification={len(parked)}")


if __name__ == "__main__":
    main()
