#!/usr/bin/env python3
"""Import a human-selected, review-verified PDF batch into the pipeline."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from pipeline_common import ROOT, atomic_json, sha256, utc_now
from pypdf import PdfReader


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batch",type=Path,required=True)
    ap.add_argument("--pipeline-manifest",type=Path,
                    default=ROOT/"data/manifests/pipeline_papers.json")
    args=ap.parse_args()
    batch=json.loads(args.batch.read_text())
    manifest=json.loads(args.pipeline_manifest.read_text())
    existing={x["paper_id"]:x for x in manifest["papers"]}
    hashes={}
    for path in (ROOT/"data/source_snapshots").glob("paper_*/source_record.json"):
        r=json.loads(path.read_text());
        if r.get("pdf_sha256"): hashes[r["pdf_sha256"]]=r["paper_id"]
    for item in batch["papers"]:
        pid=item["paper_id"]; source=Path(item["download_path"])
        if not source.exists(): raise FileNotFoundError(source)
        if source.read_bytes()[:5]!=b"%PDF-": raise ValueError(f"{pid}: not a PDF")
        digest=sha256(source)
        if digest in hashes and hashes[digest]!=pid:
            raise ValueError(f"{pid}: duplicate of {hashes[digest]}")
        destination=ROOT/"data/raw_pdfs"/pid/f"{item['openreview_forum_id']}.pdf"
        destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists() or sha256(destination)!=digest:
            shutil.copy2(source,destination)
        pages=len(PdfReader(str(destination)).pages)
        version=item.get("version","openreview_2024_02_11")
        record={"paper_id":pid,"paper_version_id":f"{pid}_{version}",
            "title":item["title"],"authors":item.get("authors",[]),
            "abstract":item.get("abstract"),"language":"en","arxiv_id":item.get("arxiv_id"),
            "arxiv_version":item.get("arxiv_version"),"openreview_forum_id":item["openreview_forum_id"],
            "source_type":"openreview","source_url":f"https://openreview.net/pdf?id={item['openreview_forum_id']}",
            "forum_url":f"https://openreview.net/forum?id={item['openreview_forum_id']}",
            "venue":"ICLR","publication_year":2024,"decision":item["decision"],
            "decision_verified":True,"review_ratings":item["review_ratings"],
            "review_mean":round(sum(item["review_ratings"])/len(item["review_ratings"]),2),
            "sample_quality_band":item["sample_quality_band"],
            "sample_quality_band_note":item["band_note"],"retrieved_at":utc_now(),
            "pdf_sha256":digest,"pdf_sha256_status":"verified","page_count":pages,
            "page_count_status":"verified","local_pdf_path":str(destination.relative_to(ROOT)),
            "parse_status":"pending","data_status":"sources_verified",
            "storage_policy":"PDF excluded from Git; use private object storage in production.",
            "assessment_scope":"Paper-internal evidence only; reviews and decisions excluded from AI scoring.",
            "selection_provenance":batch["selection_provenance"]}
        atomic_json(ROOT/"data/source_snapshots"/pid/"source_record.json",record)
        existing[pid]={"paper_id":pid,"version":version,"source_type":"openreview",
            "source_url":record["source_url"],"title":item["title"],
            "tags":[item["sample_quality_band"],"review_verified","iclr_2024"],"enabled":True}
        hashes[digest]=pid
        print(f"[import] {pid}: {pages} pages, mean review {record['review_mean']}")
    manifest["target_paper_count"]=batch.get("target_total",manifest.get("target_paper_count"))
    manifest["papers"]=[existing[k] for k in sorted(existing)]
    atomic_json(args.pipeline_manifest,manifest)


if __name__=="__main__": main()
