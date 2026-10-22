#!/usr/bin/env python3
"""Score one structured paper with the locked rubric through an AI provider.

Providers:
  openai  - Responses API; requires OPENAI_API_KEY (default model configurable)
  command - JSON request on stdin, JSON result on stdout; integrates any backend

The provider returns judgments and chunk IDs. This script owns IDs, weights,
overall-score arithmetic and the final database-facing document shape.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

import certifi

from pipeline_common import ROOT, atomic_json, release_metadata, utc_now

DIMS = ["research_value", "soundness_evidence", "originality_positioning",
        "clarity_usability", "reproducibility_transparency"]
CHECKS = ["code_availability", "data_availability", "random_seed", "hyperparameters",
          "hardware_environment", "preprocessing_details"]
ROLES = {"abstract", "introduction", "related_work", "method", "theory", "experiments",
         "results", "discussion", "limitations", "conclusion", "implementation_details"}


def paper_context(folder: Path, max_chars: int) -> tuple[dict, list[dict], str]:
    meta = json.loads((folder / "metadata.json").read_text())
    sections = {s["section_id"]: s for s in json.loads((folder / "sections.json").read_text())}
    chunks = [json.loads(line) for line in (folder / "chunks.jsonl").read_text().splitlines() if line]
    # Round-robin across section roles prevents a long introduction/method from
    # consuming the whole model context before results and limitations appear.
    groups={role:[] for role in sorted(ROLES)}
    other=[]
    for c in chunks:
        role=sections[c["section_id"]]["section_role"]
        (groups[role] if role in groups else other).append(c)
    ordered=[]; depth=0
    while True:
        added=False
        for role in sorted(groups):
            if depth < len(groups[role]): ordered.append(groups[role][depth]); added=True
        if not added: break
        depth += 1
    ordered += other
    selected=[]; used=0
    for chunk in ordered:
        estimate=len(chunk["text_content"])+220
        if selected and used + estimate > max_chars: continue
        selected.append(chunk); used += estimate
    elements = json.loads((folder / "elements.json").read_text())
    parts=["\n<paper_metadata>\n" + json.dumps({
        "page_count": meta.get("page_count"),
        "figures_tables_and_formal_elements": [
            {k: e.get(k) for k in ("element_id", "element_type", "label", "caption", "page_number")}
            for e in elements
        ],
    }, ensure_ascii=False) + "\n</paper_metadata>\n"]
    for chunk in sorted(selected,key=lambda c:c["sequence_index"]):
        section = sections[chunk["section_id"]]
        text = (f"\n<chunk id=\"{chunk['chunk_id']}\" pages=\"{chunk['page_start']}-"
                f"{chunk['page_end']}\" section=\"{section['title']}\">\n"
                f"{chunk['text_content']}\n</chunk>\n")
        parts.append(text)
    return meta, chunks, "".join(parts)


def instructions(rubric: str) -> str:
    return f"""You are assessing a machine-learning paper. Use ONLY evidence inside the
provided paper chunks. Do not use venue, reviews, acceptance decisions or outside knowledge.
Do not predict acceptance and do not claim experiments were reproduced.

LOCKED RUBRIC:\n{rubric}\n
Return one JSON object only, with these exact top-level keys:
paper_summary, overall_assessment, strengths, concerns, unresolved_questions,
dimension_assessments, reproducibility_checks, claimed_contributions, claim_checks.

dimension_assessments must contain exactly these five dimension_id values: {DIMS}.
Each dimension needs: dimension_id, applicability (applicable|uncertain|not_applicable),
score (integer 1-10 only if applicable, otherwise null), confidence (high|medium|low),
summary, strengths (array), weaknesses (array), rationale, supporting_chunk_ids (nonempty
when applicable), limiting_chunk_ids (nonempty when applicable). Cite only supplied chunk IDs.
reproducibility_checks must contain exactly these check_id values: {CHECKS}; each needs
check_id, status (available|not_available|unable_to_verify|not_applicable), detail, chunk_ids.
claimed_contributions: array of objects with text, type, chunk_id.
claim_checks: array of objects with claim_text, claim_type, scope, verdict, reasoning,
supporting_chunk_ids, limiting_chunk_ids. Keep all prose concise and evidence-bound.
"""


def call_openai(system: str, context: str, model: str) -> dict:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for --scorer openai")
    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/responses"
    payload = {"model": model, "instructions": system, "input": context,
               "store": False, "max_output_tokens": 12000,
               "text": {"format": {"type": "json_object"}}}
    result=None; last=None
    for attempt in range(1,4):
        request = Request(endpoint, data=json.dumps(payload).encode(), method="POST",
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            tls_context = ssl.create_default_context(cafile=certifi.where())
            with urlopen(request, timeout=300, context=tls_context) as response:
                result=json.load(response)
            break
        except Exception as exc:
            last=exc
            if attempt<3: time.sleep(2**attempt)
    if result is None: raise RuntimeError(f"AI request failed after 3 attempts: {last}")
    text = result.get("output_text")
    if not text:
        for item in result.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text")
    if not text:
        raise RuntimeError("AI provider returned no output_text")
    return json.loads(text)


def call_command(system: str, context: str, command: str) -> dict:
    request = json.dumps({"instructions": system, "paper_context": context}, ensure_ascii=False)
    completed = subprocess.run(command, input=request, text=True, capture_output=True,
                               shell=True, check=True)
    return json.loads(completed.stdout)


def require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"AI result has empty {field}")
    return value.strip()


def normalize(pid: str, version: str, raw: dict, chunks: list[dict], provider: str, model: str) -> dict:
    release = release_metadata()
    chunk_map = {c["chunk_id"]: c for c in chunks}
    raw_dims = {d.get("dimension_id"): d for d in raw.get("dimension_assessments", [])}
    if set(raw_dims) != set(DIMS):
        raise ValueError("AI result must contain exactly the five rubric dimensions")
    evidence, evidence_by_chunk = [], {}

    def evidence_id(chunk_id: str) -> str:
        if chunk_id not in chunk_map:
            raise ValueError(f"AI cited nonexistent chunk: {chunk_id}")
        if chunk_id not in evidence_by_chunk:
            c = chunk_map[chunk_id]; eid = f"{pid}_ev_{len(evidence)+1:03d}"
            evidence_by_chunk[chunk_id] = eid
            evidence.append({"evidence_id": eid, "page_number": c["page_start"],
                "section_id": c["section_id"], "section_title": None, "locator_type": "text",
                "locator_label": chunk_id, "evidence_mode": "quote",
                "excerpt": c["text_content"][:900], "location_confidence": c["location_confidence"]})
        return evidence_by_chunk[chunk_id]

    dimensions, links, numeric = [], [], []
    for dim_id in DIMS:
        item = raw_dims[dim_id]; applicability = item.get("applicability")
        if applicability not in {"applicable", "uncertain", "not_applicable"}:
            raise ValueError(f"{dim_id}: invalid applicability")
        score = item.get("score")
        if applicability == "applicable" and (not isinstance(score, int) or not 1 <= score <= 10):
            raise ValueError(f"{dim_id}: applicable score must be integer 1..10")
        if applicability != "applicable": score = None
        support = list(dict.fromkeys(item.get("supporting_chunk_ids") or []))
        limits = list(dict.fromkeys(item.get("limiting_chunk_ids") or []))
        if applicability == "applicable" and (not support or not limits):
            raise ValueError(f"{dim_id}: supporting and limiting evidence are required")
        ids = [evidence_id(x) for x in support + limits]
        dimensions.append({"dimension_id": dim_id, "dimension_question": dim_id.replace("_", " "),
            "applicability": applicability, "score": score,
            "rating": None if score is None else ("strong" if score >= 9 else "good" if score >= 7 else "adequate" if score >= 5 else "weak"),
            "weight": .2, "confidence": item.get("confidence", "medium"),
            "summary": require_text(item.get("summary"), f"{dim_id}.summary"),
            "strengths": item.get("strengths") or [], "weaknesses": item.get("weaknesses") or [],
            "rationale": require_text(item.get("rationale"), f"{dim_id}.rationale"), "evidence_ids": ids})
        for cid in support:
            links.append({"evidence_id": evidence_id(cid), "dimension_id": dim_id,
                          "stance": "supports", "reason": "Supports this dimension judgment."})
        for cid in limits:
            links.append({"evidence_id": evidence_id(cid), "dimension_id": dim_id,
                          "stance": "contradicts", "reason": "Records a limitation or boundary."})
        if score is not None: numeric.append((len(dimensions)-1, score))
    if numeric:
        effective_weight = round(1 / len(numeric), 10)
        for i, _ in numeric: dimensions[i]["weight"] = effective_weight
        for i, dim in enumerate(dimensions):
            if dim["score"] is None: dim["weight"] = 0
    overall = round(sum(score for _, score in numeric) / len(numeric), 1) if len(numeric) >= 3 else None
    rating = None if overall is None else ("strong" if overall >= 9 else "good" if overall >= 7 else "adequate" if overall >= 5 else "weak")

    checks_by_id = {x.get("check_id"): x for x in raw.get("reproducibility_checks", [])}
    if set(checks_by_id) != set(CHECKS):
        raise ValueError("AI result must contain exactly six reproducibility checks")
    checks = []
    for cid in CHECKS:
        item = checks_by_id[cid]; status = item.get("status")
        if status not in {"available", "not_available", "unable_to_verify", "not_applicable"}:
            raise ValueError(f"{cid}: invalid status")
        checks.append({"check_id": cid, "status": status,
                       "detail": require_text(item.get("detail"), f"{cid}.detail"),
                       "evidence_ids": [evidence_id(x) for x in item.get("chunk_ids", [])]})
    contributions=[]
    for i,item in enumerate(raw.get("claimed_contributions") or [],1):
        contributions.append({"contribution_id":f"c{i}","text":require_text(item.get("text"),"contribution.text"),
            "type":item.get("type") or "method_or_finding","evidence_id":evidence_id(item["chunk_id"])})
    claims=[]
    for i,item in enumerate(raw.get("claim_checks") or [],1):
        cited=list(dict.fromkeys((item.get("supporting_chunk_ids") or [])+(item.get("limiting_chunk_ids") or [])))
        claims.append({"claim_id":f"cl{i}","claim_text":require_text(item.get("claim_text"),"claim_text"),
            "claim_type":item.get("claim_type") or "major","scope":item.get("scope") or "assessed PDF",
            "verdict":item.get("verdict") or "partially_supported",
            "reasoning":require_text(item.get("reasoning"),"claim.reasoning"),
            "evidence_ids":[evidence_id(x) for x in cited]})
    return {"assessment_id":f"assessment_ai_{pid}_{version}","paper_version_id":f"{pid}_{version}",
        "assessment_type":"ai","schema_version":release["schema_version"],"rubric_id":release["rubric_id"],
        "rubric_version":release["rubric_version"],"dataset_version":release["dataset_version"],"status":"approved",
        "stage":"report_ready","is_provisional":False,
        "provisional_reason":None,
        "created_at":utc_now(),"model_run":{"provider":provider,"model_id":model,"prompt_version":"rubric-0.1-auto-1"},
        "paper_summary":require_text(raw.get("paper_summary"),"paper_summary"),
        "claimed_contributions":contributions,"claim_checks":claims,"overall_score":overall,
        "overall_rating":rating,"reading_recommendation":"unable_to_assess" if overall is None else ("worth_deep_reading" if overall>=8.5 else "worth_reading_with_checks" if overall>=7 else "selective_reading"),
        "confidence":"medium","overall_assessment":require_text(raw.get("overall_assessment"),"overall_assessment"),
        "strengths":raw.get("strengths") or [],"concerns":raw.get("concerns") or [],
        "unresolved_questions":raw.get("unresolved_questions") or [],
        "limitations_of_this_assessment":["Paper-internal evidence only.","No experiment or code was executed.","Originality is not an exhaustive prior-art search."],
        "dimension_assessments":dimensions,"evidence_references":evidence,"evidence_links":links,
        "reproducibility_checks":checks,"guardrails":{"experiments_reproduced":False,
        "accept_reject_prediction_made":False,"external_reviews_used_for_scoring":False}}


def descriptors(pid, version, assessment):
    items=[]
    def add(group, kind, text, evidence_id):
        ref=next(e for e in assessment["evidence_references"] if e["evidence_id"]==evidence_id)
        items.append({"descriptor_id":f"{pid}_{version}_descriptor_{len(items)+1:03d}",
            "paper_version_id":f"{pid}_{version}","descriptor_group":group,"descriptor_type":kind,
            "text":text,"evidence_chunk_id":ref["locator_label"],"evidence_element_id":None,"confidence":"medium"})
    first=assessment["evidence_references"][0]["evidence_id"]
    add("research_problem","problem_statement",assessment["paper_summary"],first)
    for x in assessment["claimed_contributions"]: add("contributions","claimed_contribution",x["text"],x["evidence_id"])
    if not assessment["claimed_contributions"]: add("contributions","claimed_contribution","No explicit contribution reliably extracted.",first)
    for x in assessment["claim_checks"]: add("claims","major_claim",x["claim_text"],x["evidence_ids"][0] if x["evidence_ids"] else first)
    if not assessment["claim_checks"]: add("claims","major_claim","No major claim reliably extracted.",first)
    group_dim={"method":"research_value","evaluation":"soundness_evidence","reliability_checks":"reproducibility_transparency"}
    for group,dim in group_dim.items():
        d=next(x for x in assessment["dimension_assessments"] if x["dimension_id"]==dim)
        add(group,group[:-1]+"_summary",d["summary"],d["evidence_ids"][0] if d["evidence_ids"] else first)
    limitation=(assessment["concerns"] or ["No limitation reliably extracted."])[0]
    limit_ref=next((x for x in assessment["evidence_links"] if x["stance"]=="contradicts"),None)
    add("limitations","boundary_or_gap",limitation,limit_ref["evidence_id"] if limit_ref else first)
    return items


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--paper-id",required=True); ap.add_argument("--version",required=True)
    ap.add_argument("--scorer",choices=("openai","command"),default="openai")
    ap.add_argument("--model",default=os.environ.get("PCC_SCORING_MODEL","gpt-5.4-mini"))
    ap.add_argument("--scorer-command",default=os.environ.get("PCC_SCORER_COMMAND"))
    ap.add_argument("--max-input-chars",type=int,default=180000); args=ap.parse_args()
    folder=ROOT/"data/parsed"/args.paper_id/args.version
    meta,chunks,context=paper_context(folder,args.max_input_chars)
    source_path=ROOT/"data/source_snapshots"/args.paper_id/"source_record.json"
    source=json.loads(source_path.read_text())
    header=json.dumps({"paper_id":args.paper_id,"title":source.get("title"),"version":args.version},ensure_ascii=False)
    system=instructions((ROOT/"rubrics/v0.1/scoring_guide.md").read_text())
    raw=call_openai(system,header+context,args.model) if args.scorer=="openai" else call_command(system,header+context,args.scorer_command or "")
    raw_bytes=json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()
    result=normalize(args.paper_id,args.version,raw,chunks,args.scorer,args.model)
    run_id=f"model_run_ai_trial_{args.paper_id}_{args.version}"
    result["model_run"]["model_run_id"]=run_id
    result["model_run"]["raw_output_sha256"]=hashlib.sha256(raw_bytes).hexdigest()
    atomic_json(ROOT/"data/model_runs"/f"{run_id}.json",{
        "model_run_id":run_id,"paper_id":args.paper_id,"version":args.version,
        "provider":args.scorer,"model_id":args.model,"created_at":result["created_at"],
        "raw_output_sha256":result["model_run"]["raw_output_sha256"],"raw_output":raw})
    atomic_json(ROOT/"data/annotations/ai_trial"/f"{args.paper_id}.json",result)
    atomic_json(folder/"semantic_descriptors.json",descriptors(args.paper_id,args.version,result))
    print(json.dumps({"paper_id":args.paper_id,"overall_score":result["overall_score"],"provider":args.scorer}))


if __name__=="__main__": main()
