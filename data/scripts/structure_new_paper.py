#!/usr/bin/env python3
"""Backend-facing, API-key-free entry point for one new paper.

Accepts either a PDF URL or an uploaded/local PDF, locks the bytes with SHA-256,
then emits exactly the same structural files used by the released 50-paper set.
It deliberately does not call an AI scorer or write an assessment.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

from pipeline_common import ROOT, atomic_json, download_pdf, sha256, utc_now


def run(args: list[str]) -> None:
    completed = subprocess.run([sys.executable, *args], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Structure one paper without AI or API keys")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--version", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path, help="Uploaded/local PDF")
    source.add_argument("--url", help="Direct PDF URL")
    parser.add_argument("--provenance-url",
                        help="Public HTTP(S) source for --pdf; required for auditable delivery")
    parser.add_argument("--title", required=True)
    parser.add_argument("--authors", nargs="*", default=[])
    parser.add_argument("--authors-status", choices=(
        "verified", "anonymous_at_assessed_version", "missing_requires_review"))
    parser.add_argument("--source-type", choices=("arxiv", "openreview", "pdf"), default="pdf")
    parser.add_argument("--venue")
    parser.add_argument("--publication-year", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not re.fullmatch(r"paper_[0-9]{3,}", args.paper_id):
        parser.error("--paper-id must match paper_[0-9]{3,}, for example paper_051")
    if args.pdf and not args.provenance_url:
        parser.error("--pdf requires --provenance-url so the uploaded bytes retain a public source")

    authors_status = args.authors_status or ("verified" if args.authors else "missing_requires_review")
    raw = ROOT / "data/raw_pdfs" / args.paper_id / f"{args.paper_id}_{args.version}.pdf"
    if args.pdf:
        raw.parent.mkdir(parents=True, exist_ok=True)
        if args.pdf.resolve() != raw.resolve():
            shutil.copy2(args.pdf, raw)
        source_url = args.provenance_url
    else:
        download_pdf(args.url, raw)
        source_url = args.url

    pages = len(PdfReader(str(raw)).pages)
    digest = sha256(raw)
    record = {
        "paper_id": args.paper_id,
        "paper_version_id": f"{args.paper_id}_{args.version}",
        "title": args.title,
        "authors": args.authors,
        "authors_status": authors_status,
        "abstract": None,
        "language": "en",
        "source_type": args.source_type,
        "source_url": source_url,
        "venue": args.venue,
        "publication_year": args.publication_year,
        "retrieved_at": utc_now(),
        "pdf_sha256": digest,
        "pdf_sha256_status": "verified",
        "page_count": pages,
        "page_count_status": "verified",
        "local_pdf_path": str(raw.relative_to(ROOT)),
        "parse_status": "pending",
        "data_status": "selected",
        "storage_policy": "PDF excluded from Git; use private object storage in production.",
        "assessment_scope": "Paper-internal evidence only."
    }
    source_record = ROOT / "data/source_snapshots" / args.paper_id / "source_record.json"
    atomic_json(source_record, record)
    output = args.output or ROOT / "data/parsed" / args.paper_id / args.version
    run([str(ROOT / "scripts/parse_pdf_pilot.py"), "--pdf", str(raw), "--output", str(output),
         "--paper-id", args.paper_id, "--version", args.version,
         "--source-record", str(source_record)])
    print(json.dumps({"paper_id": args.paper_id, "paper_version_id": record["paper_version_id"],
                      "output": str(output), "pdf_sha256": digest, "page_count": pages,
                      "ai_scoring_performed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
