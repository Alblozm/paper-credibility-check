"""Conservative candidates from first-round text chunks, not a new PDF parser."""
import re
from .core import now


def extract_claim_candidates(case):
    sections = {s["section_id"]: s for s in case["document"]["sections"]}
    output = []
    keyword = re.compile(r"outperform|state.of.the.art|\bsota\b|faster|speedup|guarantee|theorem|first\b|robust|generaliz|优于|首次|保证|泛化|鲁棒|提升", re.I)
    for chunk in case["document"]["chunks"]:
        if sections[chunk["section_id"]]["kind"] not in ("abstract", "introduction"):
            continue
        for sentence in re.split(r"(?<=[.!?。！？])\s*", chunk["text"]):
            if not sentence.strip() or not keyword.search(sentence):
                continue
            output.append({"credibility_claim_id": f"candidate-{chunk['chunk_id']}-{len(output)+1}", "paper_version_id": case["paper_version"]["paper_version_id"], "source_chunk_id": chunk["chunk_id"], "claim_text": sentence.strip(), "claim_type": "other", "importance": "unknown", "verifiability": False, "dedup_group_id": None, "page_number": chunk["page_number"], "extraction_source": "heuristic_candidate", "review_status": "candidate", "exclusion_reason": None})
    return output


def extract_references(case):
    sections = {s["section_id"]: s for s in case["document"]["sections"]}
    chunks = [c for c in case["document"]["chunks"] if sections[c["section_id"]]["kind"] == "references"]
    # Caller preserves reading order. Physical page numbers remain authoritative.
    chunks.sort(key=lambda c: (c["page_number"], c["sequence_index"]))
    entries, current = [], None
    numbered = re.compile(r"^\s*\[(\d+)\]\s+(.+)$")
    unmatched = []
    unnumbered = []
    cross_page_continuation = False
    for chunk in chunks:
        # Common ML PDFs use either one reference per blank-line paragraph or
        # compact [N] markers. Preserve the physical page of the source chunk.
        text = re.sub(r"(?<!^)\s+(?=\[\d+\]\s)", "\n", chunk["text"])
        numbered_mode = current is not None or bool(re.search(r"(?:^|\n)\s*\[\d+\]\s", text))
        units = text.splitlines() if numbered_mode else re.split(r"\n\s*\n", text)
        for line in (p.strip() for p in units if p.strip()):
            if not line.strip():
                continue
            marker = numbered.match(line)
            if marker:
                if current:
                    entries.append(current)
                current = {"sequence_index": int(marker.group(1)), "raw_reference_text": line.strip(), "page_start": chunk["page_number"], "page_end": chunk["page_number"]}
            elif current:
                current["raw_reference_text"] += "\n" + line.strip()
                cross_page_continuation = cross_page_continuation or chunk["page_number"] > current["page_start"]
                current["page_end"] = chunk["page_number"]
            else:
                unnumbered.append({"sequence_index": len(unnumbered) + 1, "raw_reference_text": line.strip(), "page_start": chunk["page_number"], "page_end": chunk["page_number"]})
    if current:
        entries.append(current)
    if not entries and unnumbered:
        entries = unnumbered
    elif unnumbered:
        unmatched.extend({"text": row["raw_reference_text"], "page_number": row["page_start"]} for row in unnumbered)
    references = []
    for entry in entries:
        text = entry["raw_reference_text"]
        doi = re.search(r"\b10\.\d{4,9}/[^\s<>]+", text, re.I)
        arxiv = re.search(r"arxiv\s*:?\s*(\d{4}\.\d{4,5}(?:v\d+)?)", text, re.I)
        year = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        quoted = re.search(r'[“"]([^”"]{8,})[”"]', text)
        parts = [p.strip() for p in re.split(r"\.\s+", text, maxsplit=2)]
        title = quoted.group(1).strip() if quoted else (parts[1].strip() if len(parts) > 1 else None)
        title = title or None
        author_text = parts[0] if len(parts) > 1 else ""
        authors = [a.strip() for a in re.split(r",|\band\b|\bet al\.?", author_text) if a.strip()]
        useful = bool(title and year)
        references.append({**entry, "paper_reference_id": f"ref-{case['case_id']}-{entry['sequence_index']}", "paper_version_id": case["paper_version"]["paper_version_id"], "parsed_title": title, "parsed_authors": authors, "parsed_year": int(year.group(0)) if year else None, "parsed_doi": doi.group(0).rstrip(".,;") if doi else None, "parsed_arxiv_id": arxiv.group(1) if arxiv else None, "parse_status": "success" if useful and authors else "partial" if useful or doi or arxiv else "failed", "parse_confidence": 0.7 if useful else 0.4 if doi or arxiv else None})
    sequences = [r["sequence_index"] for r in references]
    contiguous = sequences == list(range(1, len(sequences)+1)) and bool(sequences)
    complete = contiguous and not unmatched and not cross_page_continuation and bool(references)
    return {"references": references, "reference_scope": {"expected_count": len(references) if complete else None, "extraction_complete": complete}, "unparsed_prefix": unmatched, "notes": ["支持 [N] 与空行分隔的 ML 论文参考文献；标题、作者和年份仍须通过外部元数据校验。"]}
