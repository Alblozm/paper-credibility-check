#!/usr/bin/env python3
"""Strict cross-file acceptance gate for parsed papers, assessments and SQL."""
import argparse,hashlib,json,sys,unicodedata
from pathlib import Path
from jsonschema import Draft202012Validator
from pipeline_common import load_pipeline_manifest, release_metadata

ROOT=Path(__file__).resolve().parents[1]; errors=[]; warnings=[]
RELEASE=release_metadata()
EXPECTED_DIMS={"research_value","soundness_evidence","originality_positioning",
 "clarity_usability","reproducibility_transparency"}
CHECKS={"code_availability","data_availability","random_seed","hyperparameters",
 "hardware_environment","preprocessing_details"}

def err(pid,msg): errors.append(f"{pid}: {msg}")
def warn(pid,msg): warnings.append(f"{pid}: {msg}")
def controls(value):
    return [c for c in value if unicodedata.category(c) in {"Cc","Cs"} and c not in "\n\r\t"]

ap=argparse.ArgumentParser()
ap.add_argument("--manifest",type=Path,help="Validate exactly the enabled papers in this pipeline manifest")
ap.add_argument("--require-visual-qa",action="store_true",
                help="Require a human visual review marker for every PDF page")
ap.add_argument("--require-model-scoring",action="store_true",
                help="Reject rule-assisted templates and require a genuine recorded model run")
args=ap.parse_args()
schemas={name:json.loads((ROOT/"schemas"/f"{name}.schema.json").read_text())
         for name in ("paper","parsed_paper","annotation","evidence","benchmark_manifest")}
manifest_raw=json.loads(args.manifest.read_text()) if args.manifest else None
if manifest_raw:
    for issue in Draft202012Validator(schemas["benchmark_manifest"]).iter_errors(manifest_raw):
        errors.append(f"manifest: schema error at {list(issue.path)}: {issue.message}")
expected={p["paper_id"]:p for p in load_pipeline_manifest(args.manifest)} if args.manifest else None
all_parsed=list(sorted((ROOT/"data/parsed").glob("paper_*/*/metadata.json")))
parsed=[p for p in all_parsed if expected is None or p.parents[1].name in expected]
if expected is not None:
    found={p.parents[1].name for p in parsed}
    for pid in sorted(set(expected)-found): errors.append(f"{pid}: no parsed metadata found")
    for path in parsed:
        pid=path.parents[1].name
        if path.parent.name != expected[pid]["version"]:
            errors.append(f"{pid}: parsed version {path.parent.name} != manifest version {expected[pid]['version']}")
if not parsed:
    errors.append("dataset: no parsed papers found")
for metadata_path in parsed:
    pid=metadata_path.parents[1].name; folder=metadata_path.parent
    needed=["sections.json","chunks.jsonl","elements.json","links.json",
            "semantic_descriptors.json","parse_report.json"]
    for name in needed:
        if not (folder/name).exists(): err(pid,f"missing {name}")
    if any(not (folder/name).exists() for name in needed): continue
    meta=json.loads(metadata_path.read_text()); sections=json.loads((folder/"sections.json").read_text())
    for issue in Draft202012Validator(schemas["parsed_paper"]).iter_errors(meta):
        err(pid,f"metadata schema error at {list(issue.path)}: {issue.message}")
    chunks=[json.loads(x) for x in (folder/"chunks.jsonl").read_text().splitlines() if x]
    elements=json.loads((folder/"elements.json").read_text()); report=json.loads((folder/"parse_report.json").read_text())
    links=json.loads((folder/"links.json").read_text())
    descriptors=json.loads((folder/"semantic_descriptors.json").read_text())
    sids=[x["section_id"] for x in sections]; cids=[x["chunk_id"] for x in chunks]
    eids=[x["element_id"] for x in elements]
    lids=[x["paper_link_id"] for x in links]
    if len(sids)!=len(set(sids)): err(pid,"duplicate section_id")
    if len(cids)!=len(set(cids)): err(pid,"duplicate chunk_id")
    if len(eids)!=len(set(eids)): err(pid,"duplicate element_id")
    if len(lids)!=len(set(lids)): err(pid,"duplicate paper_link_id")
    if [x["sequence_index"] for x in sections]!=list(range(len(sections))): err(pid,"section order is not contiguous")
    if [x["sequence_index"] for x in chunks]!=list(range(len(chunks))): err(pid,"chunk order is not contiguous")
    sidset=set(sids)
    for section in sections:
        if section["parent_section_id"] and section["parent_section_id"] not in sidset: err(pid,"orphan section parent")
    for chunk in chunks:
        if chunk["section_id"] not in sidset: err(pid,f"chunk {chunk['chunk_id']} has invalid section")
        section=sections[sids.index(chunk["section_id"])]
        if not (section["page_start"]<=chunk["page_start"]<=chunk["page_end"]<=section["page_end"]):
            err(pid,f"chunk {chunk['chunk_id']} crosses its section page span")
        if chunk["char_count"]!=len(chunk["text_content"]): err(pid,f"bad char_count in {chunk['chunk_id']}")
        if controls(chunk["text_content"]): err(pid,f"unsafe control character in {chunk['chunk_id']}")
        if chunk.get("token_count",0)>1500: warn(pid,f"oversized chunk {chunk['chunk_id']} ({chunk['token_count']} estimated tokens)")
    for element in elements:
        if element["section_id"] and element["section_id"] not in sidset: err(pid,f"orphan element {element['element_id']}")
        if not 1<=element["page_number"]<=meta["page_count"]: err(pid,f"bad page on {element['element_id']}")
    for link in links:
        if link.get("source_chunk_id") and link["source_chunk_id"] not in set(cids): err(pid,f"orphan link {link['paper_link_id']}")
        if not str(link.get("url","")).startswith(("http://","https://")): err(pid,f"invalid URL on {link['paper_link_id']}")
    required_groups={"research_problem","contributions","claims","method","evaluation",
                     "reliability_checks","limitations"}
    if {x.get("descriptor_group") for x in descriptors} != required_groups:
        err(pid,"semantic descriptors must cover all seven groups")
    for item in descriptors:
        if item.get("evidence_chunk_id") not in set(cids): err(pid,f"orphan semantic descriptor {item.get('descriptor_id')}")
        if not str(item.get("text","")).strip(): err(pid,f"empty semantic descriptor {item.get('descriptor_id')}")
    if report["cross_section_chunk_count"]!=0: err(pid,"cross-section chunks reported")
    if report.get("chunk_count")!=len(chunks): err(pid,"parse report chunk_count is stale")
    if report.get("section_count")!=len(sections): err(pid,"parse report section_count is stale")
    if report.get("element_count")!=len(elements): err(pid,"parse report element_count is stale")
    if report["acceptance_gate"]=="failed": err(pid,"parse acceptance gate failed")
    if args.require_visual_qa:
        if report.get("visual_pages_reviewed")!=list(range(1,meta["page_count"]+1)):
            err(pid,"visual QA must cover every PDF page")
        if report.get("visual_review_status")!="all_pages_reviewed": err(pid,"visual QA is not complete")
    source=ROOT/"data/source_snapshots"/pid/"source_record.json"
    if not source.exists(): err(pid,"missing source_record.json")
    else:
        src=json.loads(source.read_text())
        for issue in Draft202012Validator(schemas["paper"]).iter_errors(src):
            err(pid,f"source schema error at {list(issue.path)}: {issue.message}")
        if src.get("pdf_sha256")!=meta["pdf_sha256"]: err(pid,"source/PDF hash mismatch")
        if not src.get("authors") and src.get("authors_status") not in {
                "anonymous_at_assessed_version","missing_requires_review"}:
            err(pid,"empty authors must have an explicit authors_status")
    assessment_path=ROOT/"data/annotations/ai_trial"/f"{pid}.json"
    if not assessment_path.exists(): err(pid,"missing assessment")
    else:
        a=json.loads(assessment_path.read_text())
        for issue in Draft202012Validator(schemas["annotation"]).iter_errors(a):
            err(pid,f"assessment schema error at {list(issue.path)}: {issue.message}")
        if a.get("schema_version")!=RELEASE["schema_version"]: err(pid,"assessment schema_version differs from release")
        if a.get("dataset_version")!=RELEASE["dataset_version"]: err(pid,"assessment dataset_version differs from release")
        if a.get("rubric_version")!=RELEASE["rubric_version"]: err(pid,"assessment rubric_version differs from release")
        provider=a.get("model_run",{}).get("provider")
        if provider=="rule_assisted_template" or a.get("stage")=="template_generated_not_model_scored":
            (err if args.require_model_scoring else warn)(pid,"assessment is a template, not a genuine model run")
        elif args.require_model_scoring:
            run_id=a.get("model_run",{}).get("model_run_id")
            if not run_id or not (ROOT/"data/model_runs"/f"{run_id}.json").exists():
                err(pid,"genuine model scoring requires a retained raw model-run record")
            else:
                run=json.loads((ROOT/"data/model_runs"/f"{run_id}.json").read_text())
                raw_digest=hashlib.sha256(json.dumps(run.get("raw_output"),ensure_ascii=False,
                    sort_keys=True).encode()).hexdigest()
                if raw_digest!=run.get("raw_output_sha256"):
                    err(pid,"retained raw model-run hash does not match its content")
                if raw_digest!=a.get("model_run",{}).get("raw_output_sha256"):
                    err(pid,"assessment/model-run hash mismatch")
        required_report=["paper_summary","overall_assessment","strengths","concerns",
                         "unresolved_questions","limitations_of_this_assessment","guardrails"]
        for field in required_report:
            if not a.get(field): err(pid,f"assessment report field is empty: {field}")
        dims=a.get("dimension_assessments",[])
        if {x["dimension_id"] for x in dims}!=EXPECTED_DIMS: err(pid,"must have exactly five dimensions")
        applicable=[x for x in dims if x["applicability"]=="applicable"]
        if abs(sum(x["weight"] for x in applicable)-1)>1e-6: err(pid,"applicable weights do not total 1")
        calculated=round(sum(x["score"]*x["weight"] for x in applicable),1)
        if calculated!=a["overall_score"]: err(pid,f"overall score {a['overall_score']} != {calculated}")
        refs={x["evidence_id"]:x for x in a.get("evidence_references",[])}
        for ev in refs.values():
            for issue in Draft202012Validator(schemas["evidence"]).iter_errors(ev):
                err(pid,f"evidence schema error at {list(issue.path)}: {issue.message}")
            label=ev.get("locator_label")
            if ev["locator_type"]=="text" and label not in set(cids): err(pid,f"evidence locator missing: {label}")
            elif ev["locator_type"]=="text":
                chunk=chunks[cids.index(label)]
                if ev.get("evidence_mode")=="quote" and ev.get("excerpt","") not in chunk["text_content"]:
                    err(pid,f"quoted evidence is not present in source chunk: {ev['evidence_id']}")
        for link in a.get("evidence_links",[]):
            if link["evidence_id"] not in refs: err(pid,f"link references missing evidence {link['evidence_id']}")
            if link["dimension_id"] not in EXPECTED_DIMS: err(pid,"link references invalid dimension")
        linked_dims={x["dimension_id"] for x in a.get("evidence_links",[])}
        if linked_dims!=EXPECTED_DIMS: err(pid,"every dimension must have evidence links")
        for dim in dims:
            stances={x["stance"] for x in a.get("evidence_links",[]) if x["dimension_id"]==dim["dimension_id"]}
            if "supports" not in stances or "contradicts" not in stances:
                err(pid,f"dimension {dim['dimension_id']} needs supporting and limiting evidence")
        if {x["check_id"] for x in a.get("reproducibility_checks",[])}!=CHECKS: err(pid,"six reproducibility checks required")

sql=ROOT/"sql/020_seed_all_structured_papers.sql"
if not sql.exists(): errors.append("dataset: missing complete SQL seed")
else:
    text=sql.read_text()
    if controls(text): errors.append("dataset: SQL contains unsafe control characters")
    if text.count("BEGIN;")!=text.count("COMMIT;"): errors.append("dataset: unbalanced transactions")

print(f"[full-validate] parsed={len(parsed)} errors={len(errors)} warnings={len(warnings)}")
for item in warnings: print("  WARN ",item)
for item in errors: print("  ERROR",item)
sys.exit(1 if errors else 0)
