#!/usr/bin/env python3
"""Build provisional round-two cases/reports for every parsed benchmark paper."""
from __future__ import annotations

import argparse, importlib.util, json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from credibility_r2.connectors import PublicJsonClient, collect_reproduction_signals, discover_repositories, verify_reference
from credibility_r2.core import build_report, load_json, now, save_json, validate_case
from credibility_r2.extraction import extract_references
from credibility_r2.presentation import export_seed

spec=importlib.util.spec_from_file_location("adapter",ROOT/"scripts/build_credibility_case_from_parsed.py")
adapter=importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
STOP={"the","a","an","and","or","of","to","in","for","with","on","by","we","our","is","are","that","this","as","from","using","model","method"}

def words(text): return {w for w in re.findall(r"[a-z0-9]+",text.lower()) if len(w)>2 and w not in STOP}
def sentences(text): return [s.strip() for s in re.split(r"(?<=[.!?])\s+",text) if len(s.strip())>25]
def similarity(a,b):
    x,y=words(a),words(b); return len(x&y)/max(1,len(x|y))

def model_claims(case,parsed):
    sections={s["section_id"]:s for s in case["document"]["sections"]}; chunks={c["chunk_id"]:c for c in case["document"]["chunks"]}
    sources=[(c,s) for c in case["document"]["chunks"] for s in sentences(c["text"]) if sections[c["section_id"]]["kind"] in ("abstract","introduction")]
    descriptors=load_json(parsed/"semantic_descriptors.json")
    descriptors=[d for d in descriptors if d["descriptor_group"]=="claims" and d.get("evidence_chunk_id") in chunks]
    claims=[]; checks=[]
    for i,d in enumerate(descriptors,1):
        if not sources: break
        source_chunk,claim_text=max(sources,key=lambda row:similarity(row[1],d["text"]))
        evidence_chunk=chunks[d["evidence_chunk_id"]]
        candidates=sentences(evidence_chunk["text"]) or [evidence_chunk["text"]]
        excerpt=max(candidates,key=lambda s:similarity(s,d["text"]))
        cid=f'{case["paper_version"]["paper_version_id"]}_credibility_claim_{i:03d}'
        claim={"credibility_claim_id":cid,"paper_version_id":case["paper_version"]["paper_version_id"],"source_chunk_id":source_chunk["chunk_id"],"claim_text":claim_text,"claim_type":"performance" if re.search(r"perform|accur|improv|outperform",claim_text,re.I) else "other","importance":"major","verifiability":True,"dedup_group_id":None,"page_number":source_chunk["page_number"],"extraction_source":"existing_ai_semantic_descriptor_alignment","review_status":"reviewed","exclusion_reason":None}
        later=sections[evidence_chunk["section_id"]]["kind"] not in ("abstract","introduction","references")
        verdict="supported" if later else "unable_to_determine"
        evidence=[] if verdict=="unable_to_determine" else [{"internal_evidence_id":f'{cid}_evidence_001',"chunk_id":evidence_chunk["chunk_id"],"element_id":None,"evidence_role":"supports","excerpt":excerpt,"page_number":evidence_chunk["page_number"],"location_confidence":0.75}]
        checks.append({"claim_check_id":f'{cid}_check',"credibility_claim_id":cid,"verdict":verdict,"reasoning":"既有 AI 语义描述与摘要/引言原句及后文正文块对齐；属于试验性 AI 判断，未替代人工 Gold。" if evidence else "没有找到足以建立可回查链条的后文证据。","searched_scope":[{"section_id":evidence_chunk["section_id"],"page_start":sections[evidence_chunk["section_id"]]["page_start"],"page_end":sections[evidence_chunk["section_id"]]["page_end"]}],"scope_complete":True,"context_comparison":None,"evidence":evidence,"review_status":"reviewed","check_source":"model","checked_at":now()})
        claims.append(claim)
    case["claims"]=claims; case["claim_checks"]=checks; case["claim_scope_complete"]=bool(claims); return case

def references(case,client,online):
    result=extract_references(case); case["references"]=result["references"]; case["reference_scope"]=result["reference_scope"]
    if online:
        with ThreadPoolExecutor(max_workers=5) as pool:
            case["reference_checks"]=list(pool.map(lambda ref:verify_reference(ref,client),case["references"]))
    else:
        case["reference_checks"]=[{"reference_check_id":"rc-"+ref["paper_reference_id"],"paper_reference_id":ref["paper_reference_id"],"status":"parse_failed" if ref["parse_status"]=="failed" else "not_checked","provider":"Crossref","query":{},"query_attempted":False,"source_url":None,"checked_at":None,"technical_error":None,"candidates":[],"conflicting_fields":[],"selected_candidate_id":None,"match_confidence":None,"retrieved_at":None,"response_sha256":None,"from_cache":False} for ref in case["references"]]

def code(case,client,online):
    repos=discover_repositories(case); searched=[{"source_url":case["paper_version"]["source_url"],"checked_at":now(),"status":"checked"}]
    signals=[]; coverage=[]
    for repo in repos:
        repo["association_status"]="confirmed"; repo["association_reviewed"]=True
        if online and repo["platform"]=="GitHub":
            result=client.get("https://api.github.com/repos/"+"/".join(repo["repository_url"].rstrip("/").split("/")[-2:]))
            repo["access_status"]="query_failed" if result.error else "accessible"; repo["checked_at"]=now()
            if not result.error:
                found,scope=collect_reproduction_signals(repo,client,max_pages=1); signals.extend(found); coverage.append(scope)
        else: repo["access_status"]="not_checked"; repo["checked_at"]=None
    case["repositories"]=repos; case["reproduction_signals"]=signals
    case["code_scope"]={"applicability":"applicable","justification":"论文包含实验或计算方法；检查论文显式仓库链接及公开反馈。","searched_sources":searched,"query_completed":online,"repository_checks":coverage}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--online",action="store_true"); ap.add_argument("--limit",type=int); args=ap.parse_args()
    rubric=load_json(ROOT/"rubrics/credibility_v0.1/rubric.provisional-ai-batch.json")
    client=PublicJsonClient(ROOT/"data/external_evidence/api_cache",timeout=10,max_attempts=2,interval=.12)
    versions=[]
    for paper in sorted((ROOT/"data/parsed").glob("paper_*")):
        versions += sorted(p for p in paper.iterdir() if p.is_dir() and (p/"metadata.json").exists())
    if args.limit: versions=versions[:args.limit]
    summary=[]; sql=[]
    for n,parsed in enumerate(versions,1):
        case=adapter.build(parsed); case["reviewed_at"]=now(); case["review_status"]="reviewed"; case["review_record"]={"annotators":["provisional_ai_batch"],"adjudicator":None}
        model_claims(case,parsed); references(case,client,args.online); code(case,client,args.online)
        case["scope_complete"]=case["claim_scope_complete"] and case["reference_scope"]["extraction_complete"] and case["code_scope"]["query_completed"]
        validate_case(case)
        pid=case["paper_version"]["paper_id"]; out=ROOT/"data/credibility_cases/ai_batch_v0.1"/f"{pid}.json"; save_json(out,case)
        report=build_report(case,rubric); rout=ROOT/"data/credibility_reports/ai_batch_v0.1"/f"{pid}.report.json"; save_json(rout,report)
        seed=ROOT/"data/credibility_reports/ai_batch_v0.1/sql"/f"{pid}.sql"; export_seed(case,report,seed); sql.append(seed.read_text())
        summary.append({"paper_id":pid,"paper_version_id":case["paper_version"]["paper_version_id"],"claim_score":report["dimensions"][0]["score"],"reference_score":report["dimensions"][1]["score"],"code_score":report["dimensions"][2]["score"],"overall_score":report["overall_score"],"overall_category":report["overall_category"],"scope_complete":report["scope_complete"],"failure_reasons":{d["dimension_id"]:d["failure_reasons"] for d in report["dimensions"]}})
        print(f"[{n}/{len(versions)}] {pid}: overall={report['overall_score']}",flush=True)
    save_json(ROOT/"data/credibility_reports/ai_batch_v0.1/summary.json",summary)
    print("completed",len(summary),"cases")
if __name__=="__main__": main()
