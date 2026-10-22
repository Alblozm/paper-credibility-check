"""Verify the locked R2C-P002-C01 PDF through the production generic parser.

This is a source regression probe, not a credibility classifier or table parser.
Run from the backend root with the existing virtual environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import pypdf  # noqa: E402

from app.parsing.anchors import build_anchored_text, build_element_catalog  # noqa: E402
from app.parsing.generic import GenericPdfParser  # noqa: E402
from app.storage.parsed_documents import parsed_document_payload  # noqa: E402


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _row(text: str, method: str, columns: list[str], benchmark: str) -> dict:
    method_pattern = re.escape(method).replace(r"\ ", r"\s+")
    match = re.search(
        rf"^\s*{method_pattern}\s+(\d+)\s+([\d.]+(?:\s+[\d.]+)*)\s*$",
        text, flags=re.MULTILINE,
    )
    if not match:
        raise ValueError(f"Cannot locate a complete numeric row: {method}")
    values = [float(value) for value in match.group(2).split()]
    if len(values) != len(columns):
        raise ValueError(f"Numeric row does not match the header: {method}")
    return {
        "method": method, "model_calls": int(match.group(1)),
        "accuracy_percent": values[columns.index(benchmark)],
        "raw_row": match.group(0).strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--case-record", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case_record.read_text())
    if case["case_id"] != "R2C-P002-C01":
        parser.error("This probe is limited to the first paper_002 case.")
    pdf = args.pdf.resolve()
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if digest != case["paper"]["pdf_sha256"]:
        parser.error("PDF SHA-256 does not match the locked case version.")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Match the PDF-upload parser entrypoint: identify the actual bytes, without
    # assigning an arXiv ID to a PDF through a model or through fixture metadata.
    doc = GenericPdfParser(
        arxiv_cache_dir=output / "arxiv-cache",
        max_download_bytes=30 * 1024 * 1024, max_pages=60,
    ).parse_pdf(str(pdf))
    claim = case["claim"]
    comparison = case["comparison"]
    quote = _normalized(claim["quote_excerpt"])
    claim_blocks = [
        block for block in doc.blocks
        if claim["paper_page"] in block.pages and quote in _normalized(block.text)
    ]
    table = next(element for element in doc.elements if element.label == comparison["locator_label"])
    table_blocks = [
        block for block in doc.blocks
        if table.page_number in block.pages and f"{table.label}:" in block.text
    ]
    if len(table_blocks) != 1:
        raise ValueError("The table must have one unambiguous source block.")
    table_block = table_blocks[0]
    header = re.search(r"^#\s*calls\s+(.+)$", table_block.text, flags=re.MULTILINE)
    if not header:
        raise ValueError("Table column header is unreadable.")
    columns = header.group(1).split()
    model_marker = re.search(
        rf"^\s*{re.escape(comparison['model'])}\s*$", table_block.text, flags=re.MULTILINE,
    )
    if not model_marker:
        raise ValueError("The target model row group is unreadable.")
    model_text = table_block.text[model_marker.end():]
    # Restrict the probe to this table's model group rather than finding the
    # expected numbers anywhere in the paper.
    model_text = re.split(r"\nGPT-\S+\s*\n", model_text, maxsplit=1)[0]
    rows = [
        _row(model_text, method, columns, comparison["benchmark"])
        for method in ("Standard Prompting", "Self-Correct (round 1)", "Self-Correct (round 2)")
    ]
    setup_blocks = [
        block for block in doc.blocks
        if "2023/08/29" in block.text and "200 questions" in _normalized(block.text)
    ]
    stopping_blocks = [
        block for block in doc.blocks
        if "remove the use of labels to determine when to stop" in _normalized(block.text)
    ]
    elements = {element.label: element.page_number for element in doc.elements}
    delta = rows[2]["accuracy_percent"] - rows[0]["accuracy_percent"]
    checks = {
        "locked_pdf_sha256": doc.source_sha256 == digest,
        "page_count": doc.paper.page_count == case["paper"]["pdf_page_count"],
        "complete_title": _normalized(doc.paper.title or "") == _normalized(case["paper"]["title"]),
        "abstract_contains_claim": quote in _normalized(doc.paper.abstract or ""),
        "abstract_defines_no_external_feedback": "without the crutch of external feedback"
        in _normalized(doc.paper.abstract or ""),
        "claim_has_page_1_chunk": len(claim_blocks) == 1 and claim_blocks[0].section_title == "Abstract",
        "table_3_has_page_5_chunk": table.page_number == comparison["paper_page"],
        "table_and_chunk_same_section": table.section_id == table_block.section_id,
        "baseline_row": rows[0]["accuracy_percent"] == comparison["baseline"]["value"]
        and rows[0]["model_calls"] == comparison["baseline"]["model_calls"],
        "round_1_row": rows[1]["accuracy_percent"] == 91.5 and rows[1]["model_calls"] == 3,
        "round_2_row": rows[2]["accuracy_percent"] == comparison["intervention"]["value"]
        and rows[2]["model_calls"] == comparison["intervention"]["model_calls"],
        "difference_percentage_points": delta == comparison["difference_percentage_points"],
        "reported_accuracy_metric": "reports the accuracies" in _normalized(table_block.text),
        "historical_model_and_sample_conditions": bool(setup_blocks),
        "correct_label_stopping_removed": bool(stopping_blocks),
        "caption_locations": elements.get("Table 6") == 19 and elements.get("Figure 2") == 6
        and elements.get("Figure 7") == 18,
        "table_structure_limitation_retained": table.extracted_text is None
        and "TABLE_STRUCTURE_UNVERIFIED" in doc.warnings and table.parse_quality.value == "partial",
    }
    now = datetime.now(timezone.utc)
    report = {
        "run_id": now.strftime("r2c-p002-source-%Y%m%dT%H%M%SZ"),
        "checked_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "case_id": case["case_id"], "status": "passed" if all(checks.values()) else "failed",
        "execution_scope": "production_generic_parser_pdf_entrypoint_only",
        "parser_name": doc.parser_name, "pypdf_version": pypdf.__version__,
        "backend_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BACKEND_ROOT, text=True,
        ).strip(),
        "parser_source_sha256": hashlib.sha256((BACKEND_ROOT / "app/parsing/generic.py").read_bytes()).hexdigest(),
        "source_sha256": digest, "source_version_locked_by_sha256": case["paper"]["arxiv_id"],
        "runtime_paper_id": doc.paper.paper_id, "page_count": doc.paper.page_count,
        "text_block_count": len(doc.blocks), "element_count": len(doc.elements),
        "parse_quality": doc.parse_quality.value, "warnings": doc.warnings,
        "claim_chunk_ids": [block.chunk_id for block in claim_blocks],
        "table_chunk_id": table_block.chunk_id, "table_element_id": table.element_id,
        "table_page": table.page_number, "table_header_columns": columns,
        "model": comparison["model"], "benchmark": comparison["benchmark"], "actual_rows": rows,
        "difference_percentage_points": delta,
        "condition_chunk_ids": [block.chunk_id for block in setup_blocks + stopping_blocks],
        "checks": checks,
        "api_runtime_executed": False, "model_called": False,
        "credibility_classification_executed": False, "table_cell_structure_verified": False,
        "artifacts": ["verification.json", "parsed_document.json", "anchored_text.txt", "element_catalog.txt"],
    }
    (output / "parsed_document.json").write_text(
        json.dumps(parsed_document_payload(doc), ensure_ascii=False, indent=2) + "\n",
    )
    (output / "anchored_text.txt").write_text(build_anchored_text(doc) + "\n")
    (output / "element_catalog.txt").write_text(build_element_catalog(doc) + "\n")
    (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "run_id": report["run_id"], "checks": checks,
                      "output_dir": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
