#!/usr/bin/env python3
"""Materialize figure/table source pages at their declared asset_object_key.

Git contains source URLs, PDF hashes, page numbers and deterministic object
keys. This command downloads a locked PDF when necessary, checks its SHA-256,
and renders only pages referenced by figure/table elements.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pdfplumber

from pipeline_common import ROOT, download_pdf, sha256


def materialize(paper_id: str, resolution: int, force: bool) -> tuple[int, int]:
    source_path = ROOT / "data/source_snapshots" / paper_id / "source_record.json"
    source = json.loads(source_path.read_text())
    pdf_path = ROOT / source["local_pdf_path"]
    if not pdf_path.exists():
        download_pdf(source["source_url"], pdf_path)
    actual = sha256(pdf_path)
    if actual != source["pdf_sha256"]:
        raise ValueError(f"{paper_id}: PDF SHA-256 mismatch")

    version = source["paper_version_id"].removeprefix(f"{paper_id}_")
    elements_path = ROOT / "data/parsed" / paper_id / version / "elements.json"
    elements = json.loads(elements_path.read_text())
    targets: dict[int, Path] = {}
    for element in elements:
        key = element.get("asset_object_key")
        page = element.get("page_number")
        if key and page and element.get("element_type") in {"figure", "table"}:
            targets.setdefault(int(page), ROOT / key)

    written = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, output in sorted(targets.items()):
            if page_number > len(pdf.pages):
                raise ValueError(f"{paper_id}: page {page_number} exceeds PDF length")
            if output.exists() and not force:
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            pdf.pages[page_number - 1].to_image(resolution=resolution).save(output)
            written += 1
    return len(targets), written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper-id", action="append", help="Repeatable; default is all papers")
    parser.add_argument("--resolution", type=int, default=150)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    paper_ids = args.paper_id or sorted(p.name for p in (ROOT / "data/source_snapshots").glob("paper_*"))
    total = written = 0
    for paper_id in paper_ids:
        paper_total, paper_written = materialize(paper_id, args.resolution, args.force)
        total += paper_total
        written += paper_written
        print(f"[assets] {paper_id}: required_pages={paper_total} written={paper_written}")
    print(f"[assets] complete: required_pages={total} written={written}")


if __name__ == "__main__":
    main()
