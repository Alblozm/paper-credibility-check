#!/usr/bin/env python3
"""Split legacy oversized chunks without invalidating existing evidence locators.

The first part retains the original chunk ID, so citations to the beginning of a
legacy chunk remain stable. Additional parts receive deterministic suffixes.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_ESTIMATED_TOKENS = 1200
TARGET_CHARS = 3200


def split_text(text):
    units = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if x.strip()]
    parts, current = [], ""
    for unit in units:
        if current and len(current) + len(unit) + 1 > TARGET_CHARS:
            parts.append(current)
            current = unit
        else:
            current = f"{current} {unit}".strip()
    if current:
        parts.append(current)
    # OCR of dense numeric tables can yield a single punctuation-poor unit.
    # Fall back to whitespace boundaries so one malformed table never creates
    # an unbounded database row.
    if len(parts) < 2 and len(text.split()) * 1.3 > MAX_ESTIMATED_TOKENS:
        words=text.split(); parts=[]; current=[]; size=0
        fallback_chars=min(TARGET_CHARS, 2000)
        for word in words:
            if current and size + len(word) + 1 > fallback_chars:
                parts.append(" ".join(current)); current=[]; size=0
            current.append(word); size += len(word) + 1
        if current: parts.append(" ".join(current))
    return parts


for path in sorted((ROOT / "data/parsed").glob("paper_*/*/chunks.jsonl")):
    chunks = [json.loads(line) for line in path.read_text().splitlines() if line]
    expanded = []
    changed = False
    for chunk in chunks:
        if chunk.get("token_count", 0) <= MAX_ESTIMATED_TOKENS:
            expanded.append(chunk)
            continue
        parts = split_text(chunk["text_content"])
        if len(parts) < 2:
            expanded.append(chunk)
            continue
        changed = True
        for index, text in enumerate(parts, 1):
            item = dict(chunk)
            if index > 1:
                item["chunk_id"] = f"{chunk['chunk_id']}_part_{index:02d}"
            item["text_content"] = text
            item["char_count"] = len(text)
            item["token_count"] = max(1, round(len(text.split()) * 1.3))
            expanded.append(item)
    # Older runs may already contain a ``_part_02`` row. If the retained base
    # row is split again, never reuse an existing ID: preserve the first ID and
    # deterministically rename only later collisions.
    seen={}
    for item in expanded:
        original=item["chunk_id"]
        seen[original]=seen.get(original,0)+1
        if seen[original] > 1:
            item["chunk_id"]=f"{original}_segment_{seen[original]:02d}"
            changed=True
    if changed:
        section_counts = {}
        for sequence, chunk in enumerate(expanded):
            chunk["sequence_index"] = sequence
            sid = chunk["section_id"]
            chunk["chunk_index_in_section"] = section_counts.get(sid, 0)
            section_counts[sid] = chunk["chunk_index_in_section"] + 1
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in expanded))
        print(f"[rechunk] {path.parents[1].name}: {len(chunks)} -> {len(expanded)}")
    report_path = path.parent / "parse_report.json"
    report = json.loads(report_path.read_text())
    report["chunk_count"] = len(expanded)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
