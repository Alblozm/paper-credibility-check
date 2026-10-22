#!/usr/bin/env python3
"""Manifest-driven PDF -> structure -> optional AI score -> SQL pipeline.

Each paper is isolated, state is persisted after every stage, and --resume skips
stages whose input fingerprint and output checks still match.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline_common import ROOT, atomic_json, load_pipeline_manifest, sha256, utc_now

STATE_PATH = ROOT / "data/quality_reports/pipeline_state.json"


def run_command(args: list[str]) -> str:
    done = subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True)
    if done.returncode:
        detail=(done.stderr or done.stdout).strip()
        raise RuntimeError(f"command failed ({Path(args[0]).name}): {detail[-4000:]}")
    return done.stdout.strip()


def fingerprint(paper: dict) -> str:
    selected={k:paper.get(k) for k in ("paper_id","version","source_type","source_url","title","tags")}
    code=[]
    for path in (ROOT/"scripts/prepare_paper.py",ROOT/"scripts/parse_pdf_pilot.py"):
        code.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256((json.dumps(selected,sort_keys=True)+"".join(code)).encode()).hexdigest()


def score_fingerprint(paper: dict) -> str:
    material=fingerprint(paper)
    for path in (ROOT/"scripts/score_paper_auto.py",ROOT/"rubrics/v0.1/scoring_guide.md"):
        material += hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(material.encode()).hexdigest()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"pipeline_version":"1.0","papers":{}}


def stage_done(state, paper, stage, outputs: list[Path], resume: bool) -> bool:
    item=state.get("papers",{}).get(paper["paper_id"],{})
    expected=(item.get("score_fingerprint")==score_fingerprint(paper) if stage=="score"
              else item.get("fingerprint")==fingerprint(paper))
    return bool(resume and expected
                and item.get("stages",{}).get(stage,{}).get("status")=="completed"
                and all(x.exists() for x in outputs))


def worker(paper: dict, args, initial: dict) -> dict:
    pid,version=paper["paper_id"],paper["version"]
    previous=json.loads(json.dumps(initial)) if initial else {"stages":{}}
    result=json.loads(json.dumps(previous))
    result.update({"paper_id":pid,"version":version,"fingerprint":fingerprint(paper),
                   "score_fingerprint":score_fingerprint(paper)})
    result.setdefault("stages",{})
    try:
        source=ROOT/"data/source_snapshots"/pid/"source_record.json"
        if not stage_done({"papers":{pid:previous}},paper,"prepare",[source],args.resume):
            output=run_command([str(ROOT/"scripts/prepare_paper.py"),"--paper-json",json.dumps(paper)])
            result["stages"]["prepare"]={"status":"completed","at":utc_now(),"output":output}
        record=json.loads(source.read_text()); pdf=ROOT/record["local_pdf_path"]
        folder=ROOT/"data/parsed"/pid/version
        parse_outputs=[folder/x for x in ("metadata.json","sections.json","chunks.jsonl","elements.json","links.json","parse_report.json")]
        parse_ok=stage_done({"papers":{pid:previous}},paper,"parse",parse_outputs,args.resume)
        if parse_ok:
            meta=json.loads((folder/"metadata.json").read_text())
            parse_ok=meta.get("pdf_sha256")==sha256(pdf)
        if not parse_ok:
            output=run_command([str(ROOT/"scripts/parse_pdf_pilot.py"),"--pdf",str(pdf),"--output",str(folder),
                                "--paper-id",pid,"--version",version,"--source-record",str(source)])
            result["stages"]["parse"]={"status":"completed","at":utc_now(),"output":output}
        assessment=ROOT/"data/annotations/ai_trial"/f"{pid}.json"
        descriptors=folder/"semantic_descriptors.json"
        if args.score and not stage_done({"papers":{pid:previous}},paper,"score",[assessment,descriptors],args.resume):
            command=[str(ROOT/"scripts/score_paper_auto.py"),"--paper-id",pid,"--version",version,
                     "--scorer",args.scorer,"--model",args.model]
            if args.scorer_command: command += ["--scorer-command",args.scorer_command]
            output=run_command(command)
            result["stages"]["score"]={"status":"completed","at":utc_now(),"output":output}
        result.update({"status":"completed" if args.score else "structured",
                       "updated_at":utc_now(),"error":None})
    except Exception as exc:
        result.update({"status":"failed","updated_at":utc_now(),"error":str(exc),
                       "traceback":traceback.format_exc(limit=5)})
    return result


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",type=Path,default=ROOT/"data/manifests/pipeline_papers.json")
    ap.add_argument("--workers",type=int,default=min(4,os.cpu_count() or 1))
    ap.add_argument("--resume",action="store_true")
    ap.set_defaults(score=False)
    ap.add_argument("--score",dest="score",action="store_true",
                    help="Explicitly generate new AI scores (not needed for the released benchmark)")
    ap.add_argument("--no-score",dest="score",action="store_false",help=argparse.SUPPRESS)
    ap.add_argument("--scorer",choices=("openai","command"),default="openai")
    ap.add_argument("--model",default=os.environ.get("PCC_SCORING_MODEL","gpt-5.4-mini"))
    ap.add_argument("--scorer-command",default=os.environ.get("PCC_SCORER_COMMAND"))
    ap.add_argument("--only",nargs="*",help="Optional paper IDs")
    ap.add_argument("--skip-finalize",action="store_true")
    args=ap.parse_args()
    papers=load_pipeline_manifest(args.manifest)
    if args.only: papers=[p for p in papers if p["paper_id"] in set(args.only)]
    if not papers: raise SystemExit("No enabled papers selected")
    if args.score and args.scorer=="openai" and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is missing. Set it, use --scorer command, or use --no-score.")
    if args.scorer=="command" and args.score and not args.scorer_command:
        raise SystemExit("--scorer command requires --scorer-command or PCC_SCORER_COMMAND")
    state=load_state(); state["manifest"]=str(args.manifest); state["started_at"]=utc_now()
    state.setdefault("papers",{})
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
        futures={pool.submit(worker,p,args,state["papers"].get(p["paper_id"],{})):p for p in papers}
        for future in as_completed(futures):
            paper=futures[future]; item=future.result(); state["papers"][paper["paper_id"]]=item
            atomic_json(STATE_PATH,state)
            print(f"[{item['status']}] {paper['paper_id']}" + (f": {item['error']}" if item.get("error") else ""),flush=True)
    failed=[p["paper_id"] for p in papers if state["papers"][p["paper_id"]]["status"]=="failed"]
    completed=sum(state["papers"][p["paper_id"]]["status"]=="completed" for p in papers)
    structured=sum(state["papers"][p["paper_id"]]["status"]=="structured" for p in papers)
    state["finished_at"]=utc_now(); state["summary"]={"selected":len(papers),"completed":completed,
        "structured_only":structured,"failed":failed}
    atomic_json(STATE_PATH,state)
    if failed:
        print(f"[pipeline] {len(failed)} paper(s) failed; rerun with --resume after fixing them")
        return 1
    if not args.skip_finalize:
        for script in ("sync_parsed_provenance.py","build_manifest.py","export_all_structured_sql.py"):
            print(run_command([str(ROOT/"scripts"/script)]))
        validate=[str(ROOT/"scripts/validate_full_dataset.py"),"--manifest",str(args.manifest)]
        print(run_command([str(ROOT/"scripts/validate_manifest.py")]))
        print(run_command(validate))
    print(f"[pipeline] completed {len(papers)} paper(s)")
    return 0


if __name__=="__main__": raise SystemExit(main())
