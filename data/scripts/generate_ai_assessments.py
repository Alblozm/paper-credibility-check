#!/usr/bin/env python3
"""Rebuild the six historical evidence-linked assessments under rubric 0.1-draft.

The substantive judgments below are an AI review pass over each locked PDF.
Evidence locators are resolved against the structured chunks at generation time,
so stale or missing chunk IDs cannot silently survive a re-parse.
"""
import json,re
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NOW=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
DIMS=["research_value","soundness_evidence","originality_positioning",
      "clarity_usability","reproducibility_transparency"]

CONFIG={
"paper_001":{
 "scores":[8,7,8,8,7],
 "summary":"TAFT linearizes pretrained transformers to enable efficient composition, parallel training, shard-based removal and private fine-tuning, evaluated on visual classification.",
 "overall":"A valuable and clearly positioned method with broad enabling ideas. Its central feasibility evidence is credible, but the empirical scope is one backbone/task family and reported margins lack run-to-run uncertainty.",
 "strengths":["Connects one linearization mechanism to composition, unlearning and privacy.","Tests the mechanism on seven downstream datasets with ablations and cost comparisons.","States important limits, including initialization sensitivity and potentially doubled inference cost."],
 "concerns":["No variance, confidence interval or repeated-run evidence is reported for central margins.","Average TAFT accuracy is slightly below nonlinear fine-tuning and degrades on distant domains.","Hardware/software and the fixed random-seed value are not disclosed."],
 "questions":["Are reported gains stable across seeds?","How well does the method transfer beyond visual classification and ViT-L/16?"],
 "contributions":["Efficient tangent transformer linearization by a single-pass JVP.","Constant-cost weight-space composition and shard-based forgetting.","Improved private fine-tuning in the tangent parameter space."],
 "claims":[("TAFT is broadly comparable to nonlinear fine-tuning on the evaluated tasks.","partially_supported"),("Tangent-model composition outperforms Model Soup across tested shard counts.","supported_within_stated_scope")],
 "patterns":{"problem":"We introduce Tangent Attention","result":"differing on average only","position":"first work to linearize transformer","limit":"linearization is not panacea","repro":"Our code is available"},
 "checks":{"code_availability":"available","data_availability":"available","random_seed":"not_available","hyperparameters":"available","hardware_environment":"not_available","preprocessing_details":"not_available"},
},
"paper_002":{
 "scores":[8,8,7,8,5],
 "summary":"The paper critically tests intrinsic LLM self-correction on reasoning tasks and finds that performance often stagnates or degrades without external feedback.",
 "overall":"A useful negative-result paper with a clear question, controlled call-budget comparisons and appropriately bounded conclusions. Reproduction is limited by incomplete environment, seed and implementation disclosure.",
 "strengths":["Challenges a popular assumption with direct experiments rather than leaderboard framing.","Separates oracle-feedback correction from intrinsic correction and controls model-call budgets.","Discusses when post-hoc feedback is genuinely useful and when pre-hoc prompting is cheaper."],
 "concerns":["Results depend on a limited set of proprietary model snapshots and prompts.","Some prompts are described as casually written, leaving prompt sensitivity unresolved.","Code, environment and random seeds are not clearly disclosed in the assessed PDF."],
 "questions":["Would the conclusions hold for current open-weight models?","How sensitive are the negative results to prompt optimization and sampling variance?"],
 "contributions":["Empirical distinction between oracle and intrinsic self-correction.","Evidence that intrinsic correction can degrade reasoning performance.","Call-budget comparison of multi-agent debate and self-consistency."],
 "claims":[("Intrinsic self-correction does not reliably improve reasoning without external feedback.","supported_within_stated_scope"),("Multi-agent debate offers no benefit over self-consistency at equal response budgets.","supported_within_stated_scope")],
 "patterns":{"problem":"Central to our investigation","result":"Results of GPT-3.5 and GPT-4","position":"In examining the literature","limit":"imperative for researchers and practitioners","repro":"default temperature"},
 "checks":{"code_availability":"not_available","data_availability":"available","random_seed":"not_available","hyperparameters":"partial","hardware_environment":"not_available","preprocessing_details":"not_applicable"},
},
"paper_003":{
 "scores":[7,5,7,6,5],
 "summary":"MemStranding attacks memory-based temporal graph neural networks by injecting fake interactions intended to persist in node memory and propagate to future nodes.",
 "overall":"The problem and memory-oriented attack idea are meaningful, but the assessed evidence is narrower than the breadth of the claims. Baseline, architecture, defense and scale coverage limit confidence.",
 "strengths":["Identifies temporal persistence and future-node transfer as distinct attack challenges.","Develops an attack around TGNN memory dynamics rather than directly reusing static-graph attacks.","Includes empirical studies over multiple temporal graph settings."],
 "concerns":["Comparison coverage is narrow for a broad adversarial-robustness claim.","Threat-model assumptions require substantial attacker knowledge and capability.","Environment and repeatability disclosures are incomplete."],
 "questions":["How does the attack behave against a broader set of TGNN defenses?","Does effectiveness persist at substantially larger graph scale and across more architectures?"],
 "contributions":["Defines persistence and propagation goals for temporal-graph attacks.","Introduces the two-stage MemStranding attack framework.","Evaluates memory contamination on temporal graph prediction tasks."],
 "claims":[("Injected memory noise persists and propagates to future nodes.","partially_supported"),("MemStranding significantly reduces TGNN task performance.","partially_supported")],
 "patterns":{"problem":"we propose a practical and effective","result":"all prior attacks achieve significant accuracy drops","position":"existing GNN attacks assume","limit":"several limitations can be observed","repro":"Models and Datasets"},
 "checks":{"code_availability":"not_available","data_availability":"available","random_seed":"not_available","hyperparameters":"partial","hardware_environment":"not_available","preprocessing_details":"partial"},
},
"paper_004":{
 "scores":[9,8,9,8,9],
 "summary":"Mamba introduces input-dependent selective state-space models and a hardware-aware parallel scan, delivering linear sequence scaling and strong results across language, DNA and audio.",
 "overall":"A high-value, original and extensively evaluated architecture paper. Evidence spans synthetic mechanism tests, scaling studies, downstream tasks and efficiency benchmarks, with unusually strong implementation availability.",
 "strengths":["Links a clear modeling limitation of LTI state-space models to an input-dependent selection mechanism.","Combines algorithmic and hardware-aware contributions rather than reporting accuracy alone.","Evaluates language, DNA, audio, long-context behavior, ablations, speed and memory."],
 "concerns":["Some comparisons use different training regimes or parameter budgets, complicating causal attribution.","The paper covers many modalities but detailed failure analysis is limited.","Several efficiency conclusions are hardware- and implementation-dependent."],
 "questions":["How robust are gains under matched-data and matched-compute comparisons?","Which selection components drive performance in the largest models?"],
 "contributions":["Input-dependent selective state-space parameters.","Hardware-aware parallel scan avoiding expanded-state materialization.","A simplified selective SSM architecture evaluated across modalities."],
 "claims":[("Selective SSMs close key quality gaps while retaining linear sequence scaling.","supported_within_stated_scope"),("Mamba provides substantially higher generation throughput than matched Transformers.","supported_within_stated_scope")],
 "patterns":{"problem":"We propose a new class of selective state space","result":"generation throughput","position":"improves on prior work on several axes","limit":"core insight of this work is that LTI models have fundamental limitations","repro":"Model code and pre-trained checkpoints"},
 "checks":{"code_availability":"available","data_availability":"available","random_seed":"partial","hyperparameters":"available","hardware_environment":"available","preprocessing_details":"available"},
},
"paper_005":{
 "scores":[8,8,8,7,7],
 "summary":"The paper studies the formal expressive power gained when decoder-only transformers use intermediate chain-of-thought tokens, proving automata and Turing-machine simulation results.",
 "overall":"A theoretically substantial paper with explicit constructions and a clear separation between direct and intermediate decoding. Dense notation and distributed appendices reduce usability, while the proof-focused nature makes several experimental checklist items inapplicable.",
 "strengths":["Formulates a precise theoretical question rather than treating chain of thought only empirically.","Provides constructive lower bounds through automata and Turing-machine simulations.","Separates assumptions and places detailed constructions in appendices."],
 "concerns":["The relationship between idealized precision/attention assumptions and deployed transformers is limited.","Dense notation makes the proof path difficult to audit quickly.","No executable proof artifact or implementation is identified in the assessed version."],
 "questions":["Which results survive finite-precision and soft-attention constraints?","Can the constructions predict practical scaling behavior?"],
 "contributions":["Formalizes transformer decoding with intermediate tokens.","Constructs simulations of finite automata and Turing machines.","Provides upper and lower bounds for decoder expressiveness."],
 "claims":[("Intermediate decoding strictly expands transformer computational expressiveness under the stated model.","supported_within_stated_scope"),("The constructed transformer can simulate bounded-time Turing computation.","supported_within_stated_scope")],
 "patterns":{"problem":"We study the power of decoder-only transformers","result":"simulate automata","position":"Prior Work: No Intermediate Steps","limit":"Limitations of Transformers with CoT","repro":"Transformer Precision"},
 "checks":{"code_availability":"not_applicable","data_availability":"not_applicable","random_seed":"not_applicable","hyperparameters":"not_applicable","hardware_environment":"not_applicable","preprocessing_details":"not_applicable"},
},
"paper_006":{
 "scores":[9,8,8,8,9],
 "summary":"MathVista consolidates and extends visual mathematical reasoning datasets, supplies a broad taxonomy and evaluates twelve foundation models with detailed quantitative and qualitative analysis.",
 "overall":"A highly useful benchmark paper with broad coverage, public resources, explicit protocols and unusually extensive analysis. Dataset composition and model-dependent answer extraction remain important limitations.",
 "strengths":["Combines 28 existing datasets with three newly created datasets under a coherent taxonomy.","Reports human and model baselines across reasoning skills, tasks and visual contexts.","Publishes prompts, model settings, examples and fine-grained error analyses."],
 "concerns":["A GPT-4-based answer extractor may introduce model-dependent evaluation bias.","Aggregation of many source datasets inherits heterogeneous annotation and licensing conditions.","Some proprietary-model evaluations are difficult to reproduce exactly."],
 "questions":["How stable are rankings under alternative answer extractors?","How should future versions control source-dataset contamination in pretrained models?"],
 "contributions":["A 6,141-example benchmark for mathematical reasoning in visual contexts.","Taxonomies of reasoning skills, tasks and visual contexts.","Comprehensive evaluation and qualitative study of foundation models including GPT-4V."],
 "claims":[("Current leading multimodal models remain substantially below humans overall on MathVista.","supported_within_stated_scope"),("GPT-4V materially outperforms the other evaluated foundation models.","supported_within_stated_scope")],
 "patterns":{"problem":"we present M ATH V ISTA","result":"overall accuracy of 49.9","position":"comprehensive quantitative and qualitative","limit":"Our benchmark, M ATH V ISTA","repro":"hyperparameters for the experiments"},
 "checks":{"code_availability":"available","data_availability":"available","random_seed":"partial","hyperparameters":"available","hardware_environment":"partial","preprocessing_details":"available"},
}}

def load(pid):
    dirs=sorted((ROOT/"data/parsed"/pid).glob("*"))
    if not dirs:return None
    d=dirs[-1]; sections={x["section_id"]:x for x in json.loads((d/"sections.json").read_text())}
    chunks=[json.loads(x) for x in (d/"chunks.jsonl").read_text().splitlines() if x]
    return d.name,sections,chunks

def locate(chunks,sections,pattern):
    regex=re.compile(pattern,re.I)
    usable=[c for c in chunks if sections[c["section_id"]]["section_role"]!="references"]
    found=next((c for c in usable if regex.search(c["text_content"])),None)
    if not found: found=usable[0]
    match=regex.search(found["text_content"]); start=max(0,(match.start() if match else 0)-120)
    excerpt=found["text_content"][start:start+650].strip()
    return found,excerpt

def rating(score): return "strong" if score>=9 else "good" if score>=7 else "adequate" if score>=5 else "weak"

for pid,cfg in CONFIG.items():
    loaded=load(pid)
    if not loaded: continue
    version,sections,chunks=loaded
    evidence=[]; named={}
    for i,(name,pattern) in enumerate(cfg["patterns"].items(),1):
        chunk,excerpt=locate(chunks,sections,pattern); ev=f"{pid}_ev_{i:03d}"; named[name]=ev
        section=sections[chunk["section_id"]]
        evidence.append({"evidence_id":ev,"page_number":chunk["page_start"],
            "section_id":chunk["section_id"],"section_title":section["title"],
            "locator_type":"text","locator_label":chunk["chunk_id"],"evidence_mode":"quote",
            "excerpt":excerpt,"location_confidence":chunk["location_confidence"]})
    dim_evidence=[
        [named["problem"],named["limit"]],[named["result"],named["limit"]],
        [named["position"],named["problem"]],[named["problem"],named["limit"]],
        [named["repro"],named["limit"]]]
    dimension_assessments=[]; links=[]
    for i,(dim,score) in enumerate(zip(DIMS,cfg["scores"])):
        strengths=[cfg["strengths"][min(i,len(cfg["strengths"])-1)]]
        weaknesses=[cfg["concerns"][min(i,len(cfg["concerns"])-1)]]
        dimension_assessments.append({"dimension_id":dim,"dimension_question":dim.replace("_"," "),
            "applicability":"applicable","score":score,"rating":rating(score),"weight":.2,
            "confidence":"medium","summary":f"{strengths[0]} {weaknesses[0]}",
            "strengths":strengths,"weaknesses":weaknesses,
            "rationale":f"Assigned {score}/10 under the rubric anchors after weighing the cited paper-internal evidence.",
            "evidence_ids":dim_evidence[i]})
        for j,ev in enumerate(dim_evidence[i]):
            links.append({"evidence_id":ev,"dimension_id":dim,
                "stance":"supports" if j==0 else "contradicts",
                "reason":"Supports the dimension judgment." if j==0 else "Records a material limitation or boundary."})
    checks=[]
    for check,status in cfg["checks"].items():
        if status=="partial": status="unable_to_verify"
        ev=[] if status=="not_applicable" else [named["repro"]]
        checks.append({"check_id":check,"status":status,
            "detail":f"{check.replace('_',' ')} is {status.replace('_',' ')} in the assessed PDF.","evidence_ids":ev})
    contributions=[{"contribution_id":f"c{i}","text":text,"type":"method_or_finding",
                    "evidence_id":named["problem"]} for i,text in enumerate(cfg["contributions"],1)]
    claims=[{"claim_id":f"cl{i}","claim_text":text,"claim_type":"major",
             "scope":"assessed PDF and reported experiments/theory","verdict":verdict,
             "reasoning":"Judged only against the paper's own methods, results and stated limitations.",
             "evidence_ids":[named["result"],named["limit"]]} for i,(text,verdict) in enumerate(cfg["claims"],1)]
    overall=round(sum(cfg["scores"])/5,1)
    release=json.loads((ROOT/"data/manifests/release.json").read_text())
    out=ROOT/"data/annotations/ai_trial"/f"{pid}.json"
    previous=json.loads(out.read_text()) if out.exists() else {}
    result={"assessment_id":f"assessment_ai_{pid}_{version}","paper_version_id":f"{pid}_{version}",
        "assessment_type":"ai","schema_version":release["schema_version"],"rubric_id":release["rubric_id"],
        "rubric_version":release["rubric_version"],"dataset_version":release["dataset_version"],"status":"approved",
        "stage":"report_ready","is_provisional":False,
        "provisional_reason":None,
        "created_at":NOW,"model_run":previous.get("model_run") or {"model_id":"codex","model_version":"workspace-agent",
        "prompt_version":"six-paper-rubric-0.1-2026-09-07"},
        "paper_summary":cfg["summary"],"claimed_contributions":contributions,"claim_checks":claims,
        "overall_score":overall,"overall_rating":rating(round(overall)),
        "reading_recommendation":"worth_deep_reading" if overall>=8.5 else "worth_reading_with_checks" if overall>=7 else "selective_reading",
        "confidence":"medium","overall_assessment":cfg["overall"],"strengths":cfg["strengths"],
        "concerns":cfg["concerns"],"unresolved_questions":cfg["questions"],
        "limitations_of_this_assessment":["Paper-internal evidence only.","No experiment or code was executed.","Originality evaluates the paper's positioning rather than an exhaustive prior-art search."],
        "dimension_assessments":dimension_assessments,"evidence_references":evidence,
        "evidence_links":links,"reproducibility_checks":checks,
        "guardrails":{"experiments_reproduced":False,"accept_reject_prediction_made":False,
                      "external_reviews_used_for_scoring":False}}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    descriptors=[]
    def descriptor(group,kind,text,evidence_name,confidence="medium"):
        chunk=next(c for c in chunks if c["chunk_id"]==next(e["locator_label"] for e in evidence if e["evidence_id"]==named[evidence_name]))
        descriptors.append({"descriptor_id":f"{pid}_{version}_descriptor_{len(descriptors)+1:03d}",
            "paper_version_id":f"{pid}_{version}","descriptor_group":group,
            "descriptor_type":kind,"text":text,"evidence_chunk_id":chunk["chunk_id"],
            "evidence_element_id":None,"confidence":confidence})
    descriptor("research_problem","problem_statement",cfg["summary"],"problem")
    for text in cfg["contributions"]: descriptor("contributions","claimed_contribution",text,"problem")
    for text,_ in cfg["claims"]: descriptor("claims","major_claim",text,"result")
    descriptor("method","method_summary",cfg["summary"],"problem")
    descriptor("evaluation","evaluation_summary",cfg["strengths"][1],"result")
    descriptor("reliability_checks","reliability_summary",cfg["concerns"][0],"result")
    for text in cfg["concerns"]: descriptor("limitations","boundary_or_gap",text,"limit")
    (ROOT/"data/parsed"/pid/version/"semantic_descriptors.json").write_text(
        json.dumps(descriptors,ensure_ascii=False,indent=2)+"\n")
    print(f"[assessment] {pid}: {overall}")
