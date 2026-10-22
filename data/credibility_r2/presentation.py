"""Safe static HTML report and a PostgreSQL round-two seed exporter."""
import html
import json
from pathlib import Path
from .core import digest, validate_case, validate_report


LABELS = {"claim_evidence_consistency": "主张与证据", "reference_verification": "参考文献", "code_reproduction_signals": "代码与复现线索"}


def display_number(value):
    return "—" if value is None else f"{value:.2f}"


def render_html(report, output):
    validate_report(report)
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for dimension in report["dimensions"]:
        state = {"not_applicable": "不适用", "unable_to_assess": "无法评分", "applicable": "已评分"}[dimension["applicability"]]
        cards.append(f'<article class="card"><small>{esc(LABELS[dimension["dimension_id"]])}</small><strong>{display_number(dimension["score"])}</strong><p>{state}</p><p class="muted">{esc(" · ".join(dimension["failure_reasons"]))}</p></article>')
    findings = []
    for finding in report["findings"]:
        content = f'<p>{esc(finding["description"])}</p>'
        if "claim" in finding:
            claim = finding["claim"]
            content += f'<h4>主张端 · PDF 第 {claim["page_number"]} 页 · {esc(claim["source_chunk_id"])}</h4><blockquote>{esc(claim["claim_text"])}</blockquote>'
        for evidence in finding["internal_evidence"]:
            content += f'<h4>证据端 · PDF 第 {evidence["page_number"]} 页 · {esc(evidence["chunk_id"] or evidence["element_id"])}</h4><blockquote>{esc(evidence["excerpt"])}</blockquote>'
        for url in finding["external_source_urls"]:
            content += f'<p><a href="{esc(url)}" rel="noopener noreferrer" target="_blank">查看外部来源</a></p>'
        if "reproduction_signal" in finding:
            signal = finding["reproduction_signal"]
            content += f'<blockquote>{esc(signal["source_excerpt"])}</blockquote><p>作者回复：{esc(signal["author_response"] or "未记录/身份未确认")}</p><p>解决状态：{esc(signal["resolution_status"])}</p>'
        content += f'<p class="muted">核查时间：{esc(finding["checked_at"] or "尚未查询")}</p><p class="boundary">{esc(finding["uncertainty"])}</p><details><summary>查看结构化记录、查找范围与条件</summary><pre>{esc(json.dumps(finding, ensure_ascii=False, indent=2))}</pre></details>'
        findings.append(f'<details class="finding"><summary><span class="badge {esc(finding["severity"])}">{esc(finding["severity"])}</span> {esc(finding["title"])} <small>· {esc(finding["status"])}</small></summary>{content}</details>')
    coverage_labels = {"expected_count": "文末预计条目", "extracted_count": "成功提取", "parsed_count": "可用解析", "queried_count": "进入查询核查", "matched_count": "匹配成功", "metadata_conflict_count": "元数据冲突", "unverified_count": "未核实", "parse_failed_count": "解析失败", "not_checked_count": "尚未核查", "query_failed_count": "查询失败"}
    rows = "".join(f'<tr><td>{label}</td><td>{esc(report["reference_coverage"][key] if report["reference_coverage"][key] is not None else "未知")}</td></tr>' for key, label in coverage_labels.items())
    limitations = "".join(f'<li>{esc(item)}</li>' for item in report["limitations"])
    payload = esc(json.dumps(report, ensure_ascii=False, indent=2))
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper Credibility Check · Draft</title><style>
    :root{color-scheme:light;--ink:#142b42;--muted:#64778a;--line:#dce4e9;--gold:#a67523}*{box-sizing:border-box}body{margin:0;background:#f1f4f5;color:var(--ink);font:16px/1.65 system-ui,-apple-system,sans-serif}main{max-width:1050px;margin:48px auto;padding:0 24px}.eyebrow{font-size:12px;letter-spacing:.16em;color:var(--gold)}h1{font-size:clamp(26px,4vw,42px);line-height:1.15;margin:12px 0}header{padding-bottom:30px;border-bottom:1px solid var(--line)}.muted,small{color:var(--muted)}.hero{display:flex;gap:30px;align-items:center;background:var(--ink);color:white;border-radius:16px;padding:28px;margin:24px 0}.hero strong{font-size:52px;line-height:1}.hero small{color:#b9ccd8}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{border:1px solid var(--line);border-radius:14px;padding:22px;background:white}.card strong{display:block;font-size:36px;margin-top:8px}.card p{margin:4px 0;overflow-wrap:anywhere}.card .muted{font-size:12px}.boundary{border-left:3px solid #c8a467;padding:12px 16px;background:#fff7e7}.finding{background:white;border:1px solid var(--line);border-radius:12px;padding:18px;margin:12px 0}.finding summary{cursor:pointer}.badge{font-size:11px;border-radius:4px;padding:3px 7px;background:#e7edf1}.high,.critical{background:#f8d6cf;color:#8e2f22}.medium{background:#f5e8c9;color:#815711}blockquote{margin:12px 0;padding:12px 18px;background:#edf3f5;border-radius:8px}h2{font-size:21px;margin:32px 0 16px}h4{margin:18px 0 8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:420px;overflow:auto;background:#f4f6f7;padding:14px}table{border-collapse:collapse;width:100%;background:white}td{padding:8px 16px;border-bottom:1px solid var(--line)}td:last-child{text-align:right}a{color:#156c8a}.alert{color:#b84432}footer{margin-top:32px;font-size:12px}@media(max-width:680px){main{margin:24px auto;padding:0 16px}.grid{grid-template-columns:1fr}.hero{align-items:flex-start;padding:22px}h1{font-size:30px}}
    </style><main><header><div class="eyebrow">PAPER CREDIBILITY CHECK / CONTRACT DRAFT</div><h1>让判断回到证据</h1><p>独立可信度报告 · 不合并五维质量分数</p><small>VERSION_ID</small></header><section class="hero"><strong>OVERALL</strong><div><p>CATEGORY</p><small>SCOPE</small></div></section><p class="boundary">本批次保证试验性分数可用；未核实、材料缺失和技术失败按较低可验证性计分并保留原始状态，不代表学术不端。</p><section class="grid">CARDS</section><h2>需要进一步查看的发现</h2>ALERTS FINDINGS<h2>References 覆盖率</h2><table>ROWS</table><h2>代码与公开复现线索</h2><p>CODE_STATES</p><p class="muted">没有反馈不表示成功复现；本轮未运行论文代码。</p><h2>边界与限制</h2><ul>LIMITATIONS</ul><details><summary>完整报告 JSON</summary><pre>PAYLOAD</pre></details><footer>生成时间：CREATED · 合同 0.1-draft</footer></main></html>'''
    replacements = {"VERSION_ID": esc(report["paper_version_id"]), "OVERALL": display_number(report["overall_score"]), "CATEGORY": esc(report["overall_category"] or "可信度类别尚无法确定"), "SCOPE": "约定检查范围已完成" if report["scope_complete"] else "检查范围尚未完成", "CARDS": "".join(cards), "ALERTS": '<p class="alert">有重要发现或单项低分，请展开查看。</p>' if report["alerts"] else "", "FINDINGS": "".join(findings) or '<p class="boundary">记录中暂无 Findings；只限已执行的检查范围。</p>', "ROWS": rows, "CODE_STATES": esc(" · ".join(report["code_states"]) or "代码来源与反馈范围尚未完成"), "LIMITATIONS": limitations, "PAYLOAD": payload, "CREATED": esc(report["created_at"])}
    # A single substitution pass prevents paper text resembling placeholders
    # from being reinterpreted as template tokens.
    import re
    document = re.sub(r"\b(?:" + "|".join(replacements) + r")\b", lambda m: replacements[m.group()], document)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(document, encoding="utf-8")


def sql_literal(value, is_json=False):
    if value is None:
        return "NULL"
    if is_json:
        return "'" + json.dumps(value, ensure_ascii=False, allow_nan=False).replace("'", "''") + "'::jsonb"
    if type(value) is bool:
        return "TRUE" if value else "FALSE"
    if type(value) in (int, float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def export_seed(case, report, output):
    validate_case(case)
    validate_report(report)
    from .core import require
    require(report["source_case_sha256"] == digest(case), "report must be built from the exported source JSON")
    snapshot_id = case["case_id"] + "@" + digest(case)[:16]
    schema = "credibility_r2"
    statements = ["-- Run after sql/001_initial_schema.sql and sql/030_credibility_schema.sql.", "-- Synthetic flag remains explicit; production loaders may reject synthetic cases.", "-- Source JSON snapshot: " + digest(case), "BEGIN;"]
    def insert(table, row, conflict, immutable=False):
        cols = ", ".join(row)
        values = ", ".join(sql_literal(value, key == "payload") for key, value in row.items())
        suffix = "DO NOTHING" if immutable else "DO UPDATE SET " + ", ".join(key + "=EXCLUDED." + key for key in row if key not in conflict)
        statements.append(f"INSERT INTO {schema}.{table} ({cols}) VALUES ({values}) ON CONFLICT ({', '.join(conflict)}) {suffix};")
    version = case["paper_version"]
    insert("paper_version_bindings", {"paper_version_id": version["paper_version_id"], "paper_id": version["paper_id"], "pdf_sha256": version["pdf_sha256"], "page_count": version["page_count"], "payload": version}, ["paper_version_id"], True)
    insert("credibility_case_memberships", {"case_id": snapshot_id, "paper_version_id": version["paper_version_id"], "synthetic": case["synthetic"], "review_status": case["review_status"], "payload": {"source_case_id": case["case_id"], "source_case_sha256": digest(case), "review_record": case["review_record"]}}, ["case_id"], True)
    insert("credibility_assessments", {"case_id": snapshot_id, "credibility_assessment_id": report["credibility_assessment_id"], "paper_version_id": version["paper_version_id"], "pdf_sha256": version["pdf_sha256"], "page_count": version["page_count"], "rubric_id": report["rubric_id"], "rubric_version": report["rubric_version"], "status": report["status"], "scope_complete": report["scope_complete"], "overall_score": report["overall_score"], "overall_category": report["overall_category"], "payload": report}, ["case_id", "credibility_assessment_id"], True)
    for dimension in report["dimensions"]:
        insert("credibility_dimension_scores", {"case_id": snapshot_id, "credibility_assessment_id": report["credibility_assessment_id"], "dimension_id": dimension["dimension_id"], "applicability": dimension["applicability"], "score": dimension["score"], "weight": dimension["weight"], "payload": dimension}, ["case_id", "credibility_assessment_id", "dimension_id"], True)
    for claim in case["claims"]:
        insert("credibility_claims", {"case_id": snapshot_id, "credibility_claim_id": claim["credibility_claim_id"], "paper_version_id": version["paper_version_id"], "payload": claim}, ["case_id", "credibility_claim_id"], True)
    for check in case["claim_checks"]:
        insert("claim_evidence_checks", {"case_id": snapshot_id, "claim_check_id": check["claim_check_id"], "credibility_claim_id": check["credibility_claim_id"], "verdict": check["verdict"], "scope_complete": check["scope_complete"], "payload": check}, ["case_id", "claim_check_id"], True)
        for evidence in check["evidence"]:
            insert("credibility_internal_evidence", {"case_id": snapshot_id, "internal_evidence_id": evidence["internal_evidence_id"], "claim_check_id": check["claim_check_id"], "chunk_id": evidence["chunk_id"], "element_id": evidence["element_id"], "page_number": evidence["page_number"], "payload": evidence}, ["case_id", "internal_evidence_id"], True)
    for ref in case["references"]:
        insert("paper_references", {"case_id": snapshot_id, "paper_reference_id": ref["paper_reference_id"], "paper_version_id": version["paper_version_id"], "sequence_index": ref["sequence_index"], "parse_status": ref["parse_status"], "payload": ref}, ["case_id", "paper_reference_id"], True)
    for check in case["reference_checks"]:
        insert("reference_verification_checks", {"case_id": snapshot_id, "reference_check_id": check["reference_check_id"], "paper_reference_id": check["paper_reference_id"], "status": check["status"], "query_attempted": check["query_attempted"], "checked_at": check["checked_at"], "payload": check}, ["case_id", "reference_check_id"], True)
    for repo in case["repositories"]:
        insert("paper_code_repositories", {"case_id": snapshot_id, "repository_id": repo["repository_id"], "paper_version_id": version["paper_version_id"], "association_status": repo["association_status"], "payload": repo}, ["case_id", "repository_id"], True)
    for signal in case["reproduction_signals"]:
        insert("reproduction_signals", {"case_id": snapshot_id, "signal_id": signal["signal_id"], "repository_id": signal["repository_id"], "signal_type": signal["signal_type"], "payload": signal}, ["case_id", "signal_id"], True)
    for finding in report["findings"]:
        insert("credibility_findings", {"case_id": snapshot_id, "credibility_assessment_id": report["credibility_assessment_id"], "finding_id": finding["finding_id"], "dimension_id": finding["dimension_id"], "severity": finding["severity"], "payload": finding}, ["case_id", "credibility_assessment_id", "finding_id"], True)
    statements.append("COMMIT;")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text("\n".join(statements) + "\n", encoding="utf-8")
