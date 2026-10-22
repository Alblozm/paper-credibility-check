#!/usr/bin/env python3
"""Rebuild the locked six-paper dataset from PDFs through validated SQL."""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def run(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True)

for source_path in sorted((ROOT / "data/source_snapshots").glob("paper_*/source_record.json")):
    source = json.loads(source_path.read_text())
    paper_id = source["paper_id"]
    version_id = source["paper_version_id"]
    prefix = paper_id + "_"
    if not version_id.startswith(prefix):
        raise SystemExit(f"invalid paper_version_id in {source_path}: {version_id}")
    version = version_id[len(prefix):]
    pdf = ROOT / source["local_pdf_path"]
    if not pdf.exists():
        raise SystemExit(f"missing locked PDF: {pdf}")
    run(ROOT / "scripts/parse_pdf_pilot.py", "--pdf", pdf,
        "--output", ROOT / "data/parsed" / paper_id / version,
        "--paper-id", paper_id, "--version", version,
        "--source-record", source_path)

for script in ("generate_ai_assessments.py", "sync_parsed_provenance.py",
               "build_manifest.py", "export_all_structured_sql.py",
               "validate_manifest.py", "validate_full_dataset.py"):
    run(ROOT / "scripts" / script)

print("[pipeline] six papers rebuilt and validated")
