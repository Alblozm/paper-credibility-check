#!/usr/bin/env python3
"""Synchronise verified PDF/parse facts back into source records and the manifest."""
import csv, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
for metadata_path in sorted((ROOT/"data/parsed").glob("paper_*/*/metadata.json")):
    metadata=json.loads(metadata_path.read_text())
    pid=metadata["paper_id"]; source_path=ROOT/"data/source_snapshots"/pid/"source_record.json"
    if not source_path.exists():
        continue
    source=json.loads(source_path.read_text())
    source.update({
        "paper_version_id":metadata["paper_version_id"],
        "pdf_sha256":metadata["pdf_sha256"],"pdf_sha256_status":"verified",
        "page_count":metadata["page_count"],"page_count_status":"verified",
        "parse_status":metadata["parse_status"],"parse_quality":metadata["parse_quality"],
        "data_status":"parsed",
    })
    source_path.write_text(json.dumps(source,ensure_ascii=False,indent=2)+"\n")

csv_path=ROOT/"data/manifests/papers.csv"
rows=list(csv.DictReader(csv_path.open()))
fields=list(rows[0]) if rows else []
parsed={p.parent.parent.name for p in (ROOT/"data/parsed").glob("paper_*/*/metadata.json")}
for row in rows:
    if row["paper_id"] in parsed:
        row["data_status"]="parsed"
with csv_path.open("w",newline="") as handle:
    writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
print(f"[sync] {len(parsed)} parsed papers synchronised")
