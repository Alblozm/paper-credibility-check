#!/usr/bin/env python3
"""Download, validate, hash, deduplicate and lock one manifest paper."""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path

from pypdf import PdfReader
from pipeline_common import ROOT, atomic_json, download_pdf, sha256, utc_now


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-json", required=True, help="One manifest paper as JSON")
    ap.add_argument("--force-download", action="store_true")
    args = ap.parse_args()
    paper = json.loads(args.paper_json)
    pid, version = paper["paper_id"], paper["version"]
    record_path = ROOT / "data/source_snapshots" / pid / "source_record.json"
    previous = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    previous_pdf = ROOT / previous["local_pdf_path"] if previous.get("local_pdf_path") else None
    filename = Path(paper["source_url"].split("?", 1)[0]).name
    if not filename.lower().endswith(".pdf"):
        filename = f"{pid}_{version}.pdf"
    pdf = previous_pdf if previous_pdf and previous_pdf.exists() else ROOT / "data/raw_pdfs" / pid / filename
    if args.force_download or not pdf.exists():
        download_pdf(paper["source_url"], pdf)
    digest = sha256(pdf)
    page_count = len(PdfReader(str(pdf)).pages)
    if page_count < 1:
        raise ValueError("PDF has no pages")

    record = dict(previous)
    record.update({
        "paper_id": pid, "paper_version_id": f"{pid}_{version}",
        "title": paper.get("title") or previous.get("title") or pid,
        "authors": paper.get("authors", previous.get("authors", [])),
        # The assessed OpenReview PDF may legitimately be anonymous. Keep this
        # distinct from metadata that is missing and still needs review.
        "authors_status": ("verified" if paper.get("authors", previous.get("authors", []))
                           else paper.get("authors_status", previous.get(
                               "authors_status", "missing_requires_review"))),
        "abstract": paper.get("abstract", previous.get("abstract")),
        "language": paper.get("language", previous.get("language", "en")),
        "source_type": paper["source_type"], "source_url": paper["source_url"],
        "tags": paper.get("tags", []), "venue": paper.get("venue", previous.get("venue")),
        "publication_year": paper.get("publication_year", previous.get("publication_year")),
        "dataset_split": paper.get("dataset_split", previous.get("dataset_split", "calibration")),
        "decision": paper.get("decision", previous.get("decision")),
        "decision_verified": paper.get("decision_verified", previous.get("decision_verified", False)),
        "landing_page_url": paper.get("landing_page_url", previous.get("landing_page_url")),
        "retrieved_at": utc_now(), "pdf_sha256": digest, "pdf_sha256_status": "verified",
        "page_count": page_count, "page_count_status": "verified",
        "local_pdf_path": str(pdf.relative_to(ROOT)), "parse_status": "pending",
        "data_status": "sources_verified",
        "storage_policy": "PDF excluded from Git; use private object storage in production.",
        "assessment_scope": "Paper-internal evidence only; external reviews and decisions excluded."
    })
    # The lock closes the race where two parallel workers download identical
    # PDFs before either source record exists.
    lock_path = ROOT / "data/source_snapshots/.dedup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        duplicate_of = None
        for other_path in sorted((ROOT / "data/source_snapshots").glob("paper_*/source_record.json")):
            other = json.loads(other_path.read_text(encoding="utf-8"))
            if other.get("paper_id") != pid and other.get("pdf_sha256") == digest:
                duplicate_of = other["paper_id"]
                break
        if duplicate_of:
            raise ValueError(f"duplicate PDF content: {pid} equals {duplicate_of} ({digest})")
        atomic_json(record_path, record)
    print(json.dumps({"paper_id": pid, "version": version, "pdf": str(pdf),
                      "sha256": digest, "pages": page_count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
