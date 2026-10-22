"""Evidence validation and deterministic report building; no model-generated scores."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DIMENSIONS = ("claim_evidence_consistency", "reference_verification", "code_reproduction_signals")
CLAIM_STATES = ("supported", "partially_supported", "evidence_not_found", "contradicted", "unable_to_determine")
REF_STATES = ("matched", "metadata_conflict", "unverified", "parse_failed", "not_checked", "query_failed")
CODE_STATES = ("official_code_available", "independent_success_report", "failure_or_data_problem_report", "discussion_inconclusive", "no_relevant_public_feedback", "repository_not_confirmed", "no_public_code_found", "query_failed", "not_applicable")
STRONG_ASSOCIATIONS = ("paper_link", "arxiv_link", "openreview_link", "project_link", "author_link", "readme_citation")
ROOT = Path(__file__).resolve().parents[1]


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def load_json(path):
    def invalid_number(value):
        raise ValidationError("Non-finite JSON number: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid_number)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    except (AttributeError, ValueError):
        return False


def schema_validate(value, schema, path="$", root=None):
    """Validate the JSON Schema subset used by our bundled contracts, offline.

    This is deliberately not a general-purpose JSON Schema implementation.
    Bundled contracts use only local $ref, anyOf, type, required, properties,
    additionalProperties, enum, const, items, minItems, minLength, pattern,
    minimum/maximum and date-time/uri formats. Unsupported keywords fail closed.
    """
    root = schema if root is None else root
    allowed = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "required", "properties", "additionalProperties", "enum", "const", "anyOf", "items", "minItems", "minLength", "pattern", "minimum", "maximum", "format"}
    require(not set(schema) - allowed, f"{path}: unsupported schema keywords {set(schema) - allowed}")
    if "$ref" in schema:
        require(schema["$ref"].startswith("#/"), "Only local schema references are supported")
        target = root
        for part in schema["$ref"][2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_validate(value, target, path, root)
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            try:
                schema_validate(value, choice, path, root)
                break
            except ValidationError:
                pass
        else:
            raise ValidationError(f"{path}: no anyOf alternative matches")
    if "type" in schema:
        expected = schema["type"]
        types = expected if isinstance(expected, list) else [expected]
        checks = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str), "boolean": type(value) is bool, "integer": type(value) is int, "number": type(value) in (int, float) and math.isfinite(value), "null": value is None}
        require(any(checks.get(t, False) for t in types), f"{path}: expected {expected}")
    if "enum" in schema:
        require(value in schema["enum"], f"{path}: invalid enum value {value!r}")
    if "const" in schema:
        require(value == schema["const"], f"{path}: wrong constant")
    if isinstance(value, dict):
        require(set(schema.get("required", [])) <= set(value), f"{path}: missing {set(schema.get('required', [])) - set(value)}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            require(not set(value) - set(props), f"{path}: unexpected fields {set(value) - set(props)}")
        for key, item in value.items():
            if key in props:
                schema_validate(item, props[key], f"{path}.{key}", root)
            elif isinstance(schema.get("additionalProperties"), dict):
                schema_validate(item, schema["additionalProperties"], f"{path}.{key}", root)
    if isinstance(value, list):
        require(len(value) >= schema.get("minItems", 0), f"{path}: array too short")
        if "items" in schema:
            for index, item in enumerate(value):
                schema_validate(item, schema["items"], f"{path}[{index}]", root)
    if isinstance(value, str):
        require(len(value) >= schema.get("minLength", 0), f"{path}: string too short")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, f"{path}: invalid format")
        if schema.get("format") == "date-time":
            require(timestamp(value), f"{path}: timezone-aware timestamp required")
        if schema.get("format") == "uri":
            require(re.match(r"^https?://[^\s/]+(?:/[^\s]*)?$", value) is not None, f"{path}: HTTP(S) URL required")
    if type(value) in (int, float):
        require(math.isfinite(value), f"{path}: non-finite number")
        require(value >= schema.get("minimum", -math.inf), f"{path}: below minimum")
        require(value <= schema.get("maximum", math.inf), f"{path}: above maximum")


def index(rows, key):
    output = {}
    for row in rows:
        require(row[key] not in output, f"duplicate {key}: {row[key]}")
        output[row[key]] = row
    return output


def validate_case(case):
    schema_validate(case, load_json(ROOT / "schemas/credibility_case.schema.json"))
    version = case["paper_version"]
    version_id, pages = version["paper_version_id"], version["page_count"]
    document = case["document"]
    sections = index(document["sections"], "section_id")
    chunks = index(document["chunks"], "chunk_id")
    elements = index(document["elements"], "element_id")
    for section in sections.values():
        require(1 <= section["page_start"] <= section["page_end"] <= pages, "section page range outside PDF")
    for row in list(chunks.values()) + list(elements.values()):
        require(row["section_id"] in sections, "unknown section_id")
        section = sections[row["section_id"]]
        require(section["page_start"] <= row["page_number"] <= section["page_end"], "chunk/element page outside section")
    claims = index(case["claims"], "credibility_claim_id")
    checks = index(case["claim_checks"], "claim_check_id")
    evidence_ids, checked_claims = set(), set()
    for claim in claims.values():
        require(claim["paper_version_id"] == version_id, "claim bound to wrong PDF version")
        require(claim["source_chunk_id"] in chunks, "claim points to nonexistent chunk")
        chunk = chunks[claim["source_chunk_id"]]
        require(claim["page_number"] == chunk["page_number"], "claim page does not match chunk")
        require(claim["claim_text"] in chunk["text"], "claim excerpt does not exist in source chunk")
        require(sections[chunk["section_id"]]["kind"] in ("abstract", "introduction"), "claim must originate in abstract/introduction")
        if claim["review_status"] == "excluded":
            require(bool(claim["exclusion_reason"]), "excluded claim requires reason")
    for check in checks.values():
        claim_id = check["credibility_claim_id"]
        require(claim_id in claims and claim_id not in checked_claims, "unknown or multiply checked claim")
        checked_claims.add(claim_id)
        require(claims[claim_id]["review_status"] == "reviewed" and claims[claim_id]["verifiability"], "checks require reviewed verifiable claim")
        for scope in check["searched_scope"]:
            require(scope["section_id"] in sections, "unknown searched section")
            section = sections[scope["section_id"]]
            require(section["page_start"] <= scope["page_start"] <= scope["page_end"] <= section["page_end"], "invalid searched page range")
        roles = set()
        for evidence in check["evidence"]:
            require(evidence["internal_evidence_id"] not in evidence_ids, "duplicate evidence ID")
            evidence_ids.add(evidence["internal_evidence_id"])
            require(evidence["chunk_id"] is not None or evidence["element_id"] is not None, "evidence needs chunk or element")
            for key, lookup in (("chunk_id", chunks), ("element_id", elements)):
                if evidence[key] is None:
                    continue
                require(evidence[key] in lookup, f"nonexistent {key}")
                source = lookup[evidence[key]]
                require(evidence["page_number"] == source["page_number"], "invented evidence page")
                text = source.get("text", source.get("caption", ""))
                require(evidence["excerpt"] in text, "evidence excerpt not in source text/caption")
                require(sections[source["section_id"]]["kind"] not in ("abstract", "introduction", "references"), "claim check must cite later paper evidence")
            roles.add(evidence["evidence_role"])
        verdict = check["verdict"]
        if verdict in ("supported", "partially_supported"):
            require("supports" in roles, "support verdict requires supporting evidence")
        if verdict == "evidence_not_found":
            require(bool(check["searched_scope"]), "evidence_not_found requires actual searched scope")
        if verdict == "contradicted":
            comparison = check["context_comparison"]
            require("contradicts" in roles and comparison is not None, "contradiction requires evidence and matched contexts")
            require(comparison["claim_context"] == comparison["evidence_context"], "different dataset/metric/baseline/conditions cannot establish contradiction")
            require(all(comparison["claim_context"][key] for key in ("dataset", "metric", "baseline", "conditions")), "contradiction context may not be unspecified; use explicit 'not_applicable' where appropriate")
    references = index(case["references"], "paper_reference_id")
    sequences = set()
    for ref in references.values():
        require(ref["paper_version_id"] == version_id, "reference bound to wrong PDF version")
        require(ref["sequence_index"] not in sequences, "duplicate reference sequence")
        sequences.add(ref["sequence_index"])
        require(1 <= ref["page_start"] <= ref["page_end"] <= pages, "reference page outside PDF")
    expected = case["reference_scope"]["expected_count"]
    require(expected is None or expected >= len(references), "expected reference count smaller than extracted count")
    reference_checks = index(case["reference_checks"], "reference_check_id")
    checked_refs = set()
    for check in reference_checks.values():
        ref_id, status = check["paper_reference_id"], check["status"]
        require(ref_id in references and ref_id not in checked_refs, "unknown or multiply checked reference")
        checked_refs.add(ref_id)
        candidates = index(check["candidates"], "candidate_id")
        require(check["query_attempted"] == (status in ("matched", "metadata_conflict", "unverified", "query_failed")), "reference query/status inconsistency")
        if check["query_attempted"]:
            require(check["source_url"] is not None and timestamp(check["checked_at"]), "external query requires source and timestamp")
        if references[ref_id]["parse_status"] == "failed":
            require(status == "parse_failed", "failed parsing must not masquerade as external verification")
        if status == "parse_failed":
            require(references[ref_id]["parse_status"] == "failed", "parse_failed state requires failed parse record")
        if status == "query_failed":
            require(bool(check["technical_error"]), "query_failed requires technical_error")
        if status == "matched":
            require(check["selected_candidate_id"] in candidates and not check["conflicting_fields"], "matched result requires conflict-free selected candidate")
        if status == "metadata_conflict":
            require(bool(candidates) and bool(check["conflicting_fields"]), "metadata_conflict requires candidates and conflict fields")
        if check["selected_candidate_id"] is not None:
            require(check["selected_candidate_id"] in candidates, "selected reference candidate missing")
    require(set(references) == checked_refs, "every extracted reference must have one state, including not_checked")
    repos = index(case["repositories"], "repository_id")
    for repo in repos.values():
        require(repo["paper_version_id"] == version_id, "repository bound to wrong PDF version")
        if repo["association_status"] == "confirmed":
            require(repo["association_reviewed"] and any(e["kind"] in STRONG_ASSOCIATIONS and e["excerpt"] for e in repo["association_evidence"]), "repository confirmation requires reviewed strong association")
        if repo["access_status"] != "not_checked":
            require(timestamp(repo["checked_at"]), "repository access check timestamp required")
    signals = index(case["reproduction_signals"], "signal_id")
    for signal in signals.values():
        require(signal["repository_id"] in repos, "signal references unknown repository")
        require(repos[signal["repository_id"]]["association_status"] == "confirmed", "reproduction signals require confirmed repository")
        if signal["signal_type"] == "independent_success_report":
            require(signal["review_status"] == "reviewed" and signal["third_party"] is True, "independent success requires reviewed third-party report")
        if signal["resolution_status"] == "fixed":
            require(signal["resolution_url"] is not None, "fixed status requires fix source; issue closure alone is insufficient")
    query_repos = set()
    for check in case["code_scope"]["repository_checks"]:
        require(check["repository_id"] in repos and check["repository_id"] not in query_repos, "invalid repository query coverage")
        query_repos.add(check["repository_id"])
        require(repos[check["repository_id"]]["association_status"] == "confirmed", "only confirmed repos may enter feedback coverage")
    if case["code_scope"]["applicability"] == "not_applicable":
        require(bool(case["code_scope"]["justification"]), "not_applicable requires justification")
    if case["review_status"] == "gold":
        record = case["review_record"]
        require(not case["synthetic"], "synthetic cases cannot be real-paper Gold")
        require(len(set(record["annotators"])) >= 2 and bool(record["adjudicator"]), "Gold requires two annotators and adjudication")
    return case


def validate_rubric(rubric):
    schema_validate(rubric, load_json(ROOT / "schemas/credibility_rubric.schema.json"))
    if rubric["status"] == "draft":
        return False
    if rubric["status"] == "approved":
        require(bool(rubric["approved_by"]) and timestamp(rubric["approved_at"]), "approved rubric needs decision owner/time")
    require(rubric["weights"] is not None and set(rubric["weights"]) == set(DIMENSIONS), "approved rubric needs exactly three weights")
    for weight in rubric["weights"].values():
        require(type(weight) in (int, float) and math.isfinite(weight) and weight > 0, "weights must be finite and positive")
    for key, states in (("claim_scores", CLAIM_STATES[:-1]), ("reference_scores", REF_STATES[:3]), ("code_scores", ("official_code_available", "independent_success_report", "failure_or_data_problem_report", "discussion_inconclusive", "no_relevant_public_feedback", "no_public_code_found"))):
        require(rubric[key] is not None and set(rubric[key]) == set(states), f"approved rubric needs complete {key}")
        for score in rubric[key].values():
            require(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 10, "invalid rubric score")
    require(rubric["reference_aggregation"] in ("mean", "min") and rubric["code_aggregation"] == "min", "aggregation policy not frozen")
    require(type(rubric["code_score_cap"]) in (int, float) and 0 <= rubric["code_score_cap"] < 10, "non-execution code score cap must be below 10")
    require(type(rubric["low_score_threshold"]) in (int, float) and 0 <= rubric["low_score_threshold"] <= 10, "low-score threshold required")
    thresholds = rubric["categories"]
    require(thresholds is not None and len(thresholds) == 4, "exactly four categories required")
    require([row["lower_bound"] for row in thresholds] == sorted(set(row["lower_bound"] for row in thresholds)) and thresholds[0]["lower_bound"] == 0, "category boundaries must be unique, ascending, start at 0")
    require(len({r["category"] for r in thresholds}) == 4, "category labels must be unique")
    return True


def reference_coverage(case):
    counts = Counter(check["status"] for check in case["reference_checks"])
    refs = case["references"]
    expected = case["reference_scope"]["expected_count"]
    parsed = sum(r["parse_status"] != "failed" for r in refs)
    queried = sum(c["query_attempted"] for c in case["reference_checks"])
    return {"expected_count": expected, "extracted_count": len(refs), "parsed_count": parsed,
            "queried_count": queried, "matched_count": counts["matched"], "metadata_conflict_count": counts["metadata_conflict"],
            "unverified_count": counts["unverified"], "parse_failed_count": counts["parse_failed"],
            "not_checked_count": counts["not_checked"], "query_failed_count": counts["query_failed"],
            "extraction_coverage": len(refs) / expected if expected else None,
            "verification_coverage": queried / parsed if parsed else None}


def code_states(case):
    scope = case["code_scope"]
    if scope["applicability"] == "not_applicable":
        return ["not_applicable"], []
    errors = []
    if scope["applicability"] == "unable_to_assess":
        errors.append("code_applicability_unknown")
    if not scope["query_completed"] or not scope["searched_sources"]:
        errors.append("code_source_search_incomplete")
    source_query_failed = any(s["status"] == "query_failed" for s in scope["searched_sources"])
    if source_query_failed:
        errors.append("code_source_query_failed")
    repos = [r for r in case["repositories"] if r["association_status"] != "rejected"]
    confirmed = [r for r in repos if r["association_status"] == "confirmed" and r["access_status"] == "accessible"]
    queries = {r["repository_id"]: r for r in scope["repository_checks"]}
    states = []
    if source_query_failed:
        states.append("query_failed")
    if any(r["access_status"] == "query_failed" for r in repos):
        states.append("query_failed")
        errors.append("repository_query_failed")
    if any(r["association_status"] == "candidate" for r in repos):
        states.append("repository_not_confirmed")
        errors.append("repository_association_unconfirmed")
    if any(r["access_status"] in ("inaccessible", "not_checked") for r in repos):
        errors.append("repository_access_unresolved")
    if confirmed:
        states.append("official_code_available")
        for repo in confirmed:
            query = queries.get(repo["repository_id"])
            if query is None or not query["completed"] or query["truncated"] or query["technical_error"]:
                errors.append("repository_feedback_query_incomplete")
                if query and query["technical_error"]:
                    states.append("query_failed")
                continue
            signals = [s for s in case["reproduction_signals"] if s["repository_id"] == repo["repository_id"]]
            if not signals:
                states.append("no_relevant_public_feedback")
            for signal in signals:
                if signal["review_status"] == "reviewed":
                    states.append(signal["signal_type"])
                else:
                    states.append("discussion_inconclusive")
                    errors.append("reproduction_signals_need_review")
    elif not repos and not errors:
        states.append("no_public_code_found")
    return list(dict.fromkeys(states)), list(dict.fromkeys(errors))


def build_report(case, rubric, assessment_id=None):
    validate_case(case)
    approved = validate_rubric(rubric)
    checks = case["claim_checks"]
    active = [c for c in case["claims"] if c["review_status"] == "reviewed" and c["verifiability"]]
    groups = defaultdict(list)
    lookup = {c["credibility_claim_id"]: c for c in active}
    for check in checks:
        claim = lookup[check["credibility_claim_id"]]
        groups[claim["dedup_group_id"] or claim["credibility_claim_id"]].append(check)
    pending_claims = any(c["review_status"] == "candidate" for c in case["claims"])
    claim_errors = []
    if not active or len(checks) != len(active) or pending_claims or not case["claim_scope_complete"] or any(not c["scope_complete"] for c in checks):
        claim_errors.append("claim_scope_incomplete")
    if any(c["verdict"] == "unable_to_determine" for c in checks):
        claim_errors.append("claim_unable_to_determine")
    if any(c["review_status"] != "reviewed" for c in checks):
        claim_errors.append("claim_checks_need_review")
    coverage = reference_coverage(case)
    ref_errors = []
    if not case["reference_scope"]["extraction_complete"] or coverage["expected_count"] is None or coverage["expected_count"] != coverage["extracted_count"]:
        ref_errors.append("reference_extraction_incomplete")
    if any(coverage[k] for k in ("unverified_count", "parse_failed_count", "not_checked_count", "query_failed_count")):
        ref_errors.append("reference_verification_incomplete_or_failed")
    states, code_errors = code_states(case)
    if case["paper_version"]["extraction_quality"] != "usable":
        claim_errors.append("paper_parse_quality_insufficient")
        ref_errors.append("paper_parse_quality_insufficient")
    claim_scope_ok, ref_scope_ok, code_scope_ok = not claim_errors, not ref_errors, not code_errors
    errors = [claim_errors, ref_errors, code_errors]
    if not approved:
        for error in errors:
            error.append("rubric_not_approved")
    provisional = rubric["status"] == "provisional"
    scores = [None, None, None]
    if approved and not claim_errors:
        # Each dedup group contributes its lowest reviewed value; dimension is
        # the minimum over all valid groups, never an average hiding one claim.
        scores[0] = min(min(rubric["claim_scores"][c["verdict"]] for c in group) for group in groups.values())
    elif approved and provisional:
        # Missing material or an inconclusive technical check is low
        # verifiability, not contradiction or misconduct.
        values = []
        for group in groups.values():
            group_values = [rubric["claim_scores"].get(c["verdict"], 3) for c in group]
            if group_values:
                values.append(min(group_values))
        # Entering this branch already means the required claim scope has a
        # gap, so a supported subset cannot erase the fallback penalty.
        scores[0] = min(values + [3]) if values else 3
    # Provisional calibration batches score the verified subset while keeping
    # every unresolved/failed item visible as a coverage gap. Approved/Gold
    # reporting remains strict and requires complete reference coverage.
    if approved and (not ref_errors or provisional):
        technical_fallback = {"parse_failed": 3, "query_failed": 3, "not_checked": 3}
        values = [rubric["reference_scores"].get(c["status"], technical_fallback.get(c["status"])) for c in case["reference_checks"]]
        values = [value for value in values if value is not None]
        if values:
            scores[1] = min(values) if rubric["reference_aggregation"] == "min" else sum(values) / len(values)
        else:
            if provisional:
                scores[1] = 3
            ref_errors.append("no_references_to_score")
    code_na = case["code_scope"]["applicability"] == "not_applicable"
    if approved and not code_errors and not code_na:
        values = [rubric["code_scores"][s] for s in states if s in rubric["code_scores"]]
        if values:
            scores[2] = min(min(values), rubric["code_score_cap"])
        else:
            code_errors.append("no_code_state_to_score")
    elif approved and provisional and not code_na:
        reviewed_failure = any(s["review_status"] == "reviewed" and s["signal_type"] == "failure_or_data_problem_report" for s in case["reproduction_signals"])
        if reviewed_failure:
            scores[2] = rubric["code_scores"]["failure_or_data_problem_report"]
        elif "official_code_available" in states:
            scores[2] = min(rubric["code_scores"]["official_code_available"], rubric["code_score_cap"])
        elif "no_public_code_found" in states:
            scores[2] = rubric["code_scores"]["no_public_code_found"]
        else:
            scores[2] = 3
    dimensions = []
    coverages = [{"reviewed_claim_count": len(active), "deduplicated_claim_count": len(groups), "checked_claim_count": len(checks), "scope_complete": claim_scope_ok}, coverage, {**case["code_scope"], "states": states}]
    for i, dimension_id in enumerate(DIMENSIONS):
        applicability = "not_applicable" if i == 2 and code_na else "applicable" if scores[i] is not None else "unable_to_assess"
        dimensions.append({"dimension_id": dimension_id, "applicability": applicability, "score": scores[i],
                           "weight": rubric["weights"][dimension_id] if approved else None,
                           "coverage": coverages[i], "failure_reasons": [] if applicability == "not_applicable" else errors[i]})
    complete = case["scope_complete"] and claim_scope_ok and ref_scope_ok and (code_na or code_scope_ok)
    applicable = [d for d in dimensions if d["applicability"] != "not_applicable"]
    overall = category = None
    scored = [d for d in applicable if d["score"] is not None]
    if approved and ((complete and applicable and len(scored) == len(applicable)) or (provisional and applicable and len(scored) == len(applicable))):
        # No individual-dimension category cap. Display alerts separately.
        overall = sum(d["score"] * d["weight"] for d in scored) / sum(d["weight"] for d in scored)
        category = next(row["category"] for row in reversed(rubric["categories"]) if overall >= row["lower_bound"])
    findings = []
    for check in checks:
        if check["verdict"] == "supported":
            continue
        claim = lookup[check["credibility_claim_id"]]
        severity = "high" if check["verdict"] == "contradicted" else "medium" if check["verdict"] in ("partially_supported", "evidence_not_found") else "info"
        findings.append({"finding_id": "f-" + check["claim_check_id"], "dimension_id": DIMENSIONS[0], "severity": severity,
                         "status": "unable_to_determine" if check["verdict"] == "unable_to_determine" else "confirmed" if check["review_status"] == "reviewed" else "pending_verification",
                         "title": check["verdict"], "description": check["reasoning"], "claim": claim,
                         "searched_scope": check["searched_scope"], "context_comparison": check["context_comparison"],
                         "internal_evidence": check["evidence"], "external_source_urls": [], "checked_at": check["checked_at"],
                         "uncertainty": "只覆盖 recorded searched_scope；未找到证据不等于论文不存在证据。" if check["verdict"] == "evidence_not_found" else "人工核查结论，范围见主张与实验条件。"})
    for check in case["reference_checks"]:
        if check["status"] == "matched":
            continue
        findings.append({"finding_id": "f-" + check["reference_check_id"], "dimension_id": DIMENSIONS[1], "severity": "medium" if check["status"] == "metadata_conflict" else "info", "status": "pending_verification", "title": check["status"], "description": "冲突字段：" + ", ".join(check["conflicting_fields"]) if check["status"] == "metadata_conflict" else "该条目尚未获得足够确认，详见逐条技术状态。", "reference_check": check, "internal_evidence": [], "external_source_urls": [check["source_url"]] if check["source_url"] else [], "checked_at": check["checked_at"], "uncertainty": "未核实、解析失败或 API 失败均不能作为引用虚假的结论。"})
    for signal in case["reproduction_signals"]:
        findings.append({"finding_id": "f-" + signal["signal_id"], "dimension_id": DIMENSIONS[2], "severity": "medium" if signal["signal_type"] == "failure_or_data_problem_report" else "info", "status": "pending_verification", "title": signal["signal_type"], "description": signal["third_party_claim"], "reproduction_signal": signal, "internal_evidence": [], "external_source_urls": [signal["source_url"]], "checked_at": signal["observed_at"], "uncertainty": "第三方公开线索；系统没有运行代码。关闭 issue 不等于结果已复现。"})
    alerts = [{"dimension_id": d["dimension_id"], "kind": "low_dimension_score", "score": d["score"]} for d in dimensions if approved and d["score"] is not None and d["score"] < rubric["low_score_threshold"]]
    alerts += [{"finding_id": f["finding_id"], "kind": "important_finding"} for f in findings if f["severity"] in ("high", "critical")]
    report = {"schema_version": "0.1-draft", "analysis_mode": "credibility_check", "credibility_assessment_id": assessment_id or "ca-" + digest({"case": case, "rubric": rubric})[:16],
              "paper_version_id": case["paper_version"]["paper_version_id"], "pdf_sha256": case["paper_version"]["pdf_sha256"],
              "rubric_id": rubric["rubric_id"], "rubric_version": rubric["version"], "rubric_status": rubric["status"], "rubric_sha256": digest(rubric),
              "source_case_sha256": digest(case), "status": "draft", "scope_complete": complete, "overall_score": overall, "overall_category": category,
              "dimensions": dimensions, "findings": findings, "alerts": alerts,
              "reference_coverage": coverage, "code_states": states, "created_at": now(),
              "limitations": (["试验性完整评分：无法消除的未核实、技术失败、不确定和材料缺失按可验证性较低计分，同时保留原始状态与失败原因。", "技术失败或材料缺失不等于引用虚假、结果错误、造假或其他学术不端；这些分数只表示本轮可验证性。"] if provisional else []) + (["本合同与实现均为独立草案，正式合同尚未取得。", "系统不运行论文代码，不提供亲自复现认证。", "外部来源只代表各记录的 checked_at/observed_at 时点。", "示例案例为合成数据，不能作为真实论文 Gold 或模型效果证据。"] if case["synthetic"] else ["本合同与实现均为独立草案，正式合同尚未取得。", "系统不运行论文代码，不提供亲自复现认证。", "外部来源只代表各记录的 checked_at/observed_at 时点。"])}
    validate_report(report)
    return report


def validate_report(report):
    schema_validate(report, load_json(ROOT / "schemas/credibility_report.schema.json"))
    dimensions = index(report["dimensions"], "dimension_id")
    require(set(dimensions) == set(DIMENSIONS), "report must have exactly the independent three dimensions")
    for dimension in dimensions.values():
        require((dimension["score"] is not None) == (dimension["applicability"] == "applicable"), "unable/not_applicable must never be zero or scored")
        if dimension["score"] is not None:
            require(report["rubric_status"] in ("provisional", "approved"), "draft rubric cannot emit numeric scores")
            require(dimension["weight"] is not None and dimension["weight"] > 0, "scored dimension needs positive weight")
    require(dimensions[DIMENSIONS[2]]["score"] is None or dimensions[DIMENSIONS[2]]["score"] < 10, "no code execution: code score must be below 10")
    overall = report["overall_score"]
    require((overall is None) == (report["overall_category"] is None), "overall score/category must both be null or both available")
    if overall is not None:
        require(report["rubric_status"] in ("provisional", "approved"), "draft report cannot have overall score")
        applicable = [d for d in dimensions.values() if d["applicability"] != "not_applicable"]
        scored = [d for d in applicable if d["score"] is not None]
        require(scored and len(scored) == len(applicable), "overall requires every applicable dimension to be scored")
        if report["rubric_status"] == "approved":
            require(report["scope_complete"] and len(scored) == len(applicable), "approved overall requires complete applicable dimensions")
        calculated = sum(d["score"] * d["weight"] for d in scored) / sum(d["weight"] for d in scored)
        require(abs(calculated - overall) < 1e-8, "overall must be the applicable weighted score")
    coverage = report["reference_coverage"]
    require(coverage["extracted_count"] == sum(coverage[key] for key in ("matched_count", "metadata_conflict_count", "unverified_count", "parse_failed_count", "not_checked_count", "query_failed_count")), "reference state counts do not cover every extracted item")
    require(coverage["queried_count"] == sum(coverage[key] for key in ("matched_count", "metadata_conflict_count", "unverified_count", "query_failed_count")), "queried coverage misrepresented")
    require(coverage["parsed_count"] == coverage["extracted_count"] - coverage["parse_failed_count"], "parsed coverage misrepresented")
    return report
