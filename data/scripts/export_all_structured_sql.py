#!/usr/bin/env python3
"""Export every fully parsed and assessed paper into one PostgreSQL seed."""
import subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"sql/020_seed_all_structured_papers.sql"
blocks=["-- Generated complete structured-paper seed. Run after 001_initial_schema.sql.\n"]
with tempfile.TemporaryDirectory() as td:
    for metadata in sorted((ROOT/"data/parsed").glob("paper_*/*/metadata.json")):
        pid=metadata.parents[1].name; version=metadata.parent.name
        assessment=ROOT/"data/annotations/ai_trial"/f"{pid}.json"
        if not assessment.exists():
            print(f"[skip] {pid}: no assessment"); continue
        temp=Path(td)/f"{pid}.sql"
        subprocess.run([sys.executable,str(ROOT/"scripts/export_paper_to_sql.py"),
            "--project-root",str(ROOT),"--paper-id",pid,"--version",version,
            "--output",str(temp)],check=True)
        blocks.append(temp.read_text())
        print(f"[export] {pid}_{version}")
OUT.write_text("\n".join(blocks))
print(f"[done] {OUT}")
