#!/usr/bin/env python3
"""把 parsed 目录转换成可信度案例合同。

该脚本只做确定性的字段映射，不调用模型、不生成评分。人工/模型核查结果可通过
--annotation 合并；未完成的范围会明确保留为 false/null。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def section_kind(row):
    role = (row.get("section_role") or "").lower()
    title = (row.get("title") or "").lower()
    text = f"{role} {title}"
    aliases = (
        ("abstract", "abstract"), ("introduction", "introduction"),
        ("method", "methods"), ("experiment", "results"), ("result", "results"),
        ("discussion", "discussion"), ("conclusion", "conclusion"),
        ("reference", "references"), ("appendix", "appendix"),
    )
    return next((kind for needle, kind in aliases if needle in text), "other")


def deep_merge(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def build(parsed_dir: Path, annotation: Path | None = None):
    metadata = read_json(parsed_dir / "metadata.json")
    sections0 = read_json(parsed_dir / "sections.json")
    chunks0 = read_jsonl(parsed_dir / "chunks.jsonl")
    elements0 = read_json(parsed_dir / "elements.json")
    page_count = metadata["page_count"]

    sections = [{
        "section_id": row["section_id"], "title": row["title"],
        "kind": section_kind(row), "page_start": row.get("page_start") or 1,
        "page_end": row.get("page_end") or page_count,
    } for row in sections0]
    section_ids = {row["section_id"] for row in sections}
    fallback_id = f'{metadata["paper_version_id"]}_credibility_unassigned'
    if any(not row.get("section_id") or row.get("section_id") not in section_ids for row in chunks0 + elements0):
        sections.append({"section_id": fallback_id, "title": "Unassigned", "kind": "other", "page_start": 1, "page_end": page_count})
        section_ids.add(fallback_id)

    chunks = [{
        "chunk_id": row["chunk_id"],
        "section_id": row.get("section_id") if row.get("section_id") in section_ids else fallback_id,
        "page_number": row.get("page_start") or 1,
        "sequence_index": row["sequence_index"], "text": row["text_content"],
    } for row in chunks0]
    elements = []
    for row in elements0:
        caption = row.get("caption") or row.get("extracted_text") or row.get("label") or "未提取到图表标题"
        elements.append({
            "element_id": row["element_id"],
            "section_id": row.get("section_id") if row.get("section_id") in section_ids else fallback_id,
            "page_number": row.get("page_number") or 1,
            "element_type": row.get("element_type") if row.get("element_type") in {"table", "figure", "equation", "theorem"} else "other",
            "caption": caption,
        })

    created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    case = {
        "schema_version": "0.1-draft",
        "case_id": f'{metadata["paper_version_id"]}_credibility',
        "synthetic": False,
        "paper_version": {
            "paper_id": metadata["paper_id"], "paper_version_id": metadata["paper_version_id"],
            "pdf_sha256": metadata["pdf_sha256"], "page_count": page_count,
            "extraction_quality": "usable" if metadata.get("parse_quality") == "good" else "partial",
            "source_url": metadata["source_url"],
        },
        "review_status": "candidate", "reviewed_at": created,
        "review_record": {"annotators": ["pipeline_adapter"], "adjudicator": None},
        "scope_complete": False, "claim_scope_complete": False,
        "reference_scope": {"expected_count": None, "extraction_complete": False},
        "code_scope": {"applicability": "unable_to_assess", "justification": "尚未执行代码仓库与公开复现信号核查", "searched_sources": [], "query_completed": False, "repository_checks": []},
        "document": {"sections": sections, "chunks": chunks, "elements": elements},
        "claims": [], "claim_checks": [], "references": [], "reference_checks": [],
        "repositories": [], "reproduction_signals": [],
    }
    if annotation:
        deep_merge(case, read_json(annotation))
    return case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--version", required=True, help="parsed 下的版本目录，例如 v3")
    parser.add_argument("--annotation")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    parsed = ROOT / "data" / "parsed" / args.paper_id / args.version
    case = build(parsed, Path(args.annotation) if args.annotation else None)
    from credibility_r2.core import save_json, validate_case
    validate_case(case)
    save_json(args.output, case)
    print(f"validated credibility case: {args.output}")


if __name__ == "__main__":
    main()
