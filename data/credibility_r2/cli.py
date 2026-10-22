import argparse
import hashlib
import sys
from pathlib import Path
from .core import ROOT, ValidationError, build_report, load_json, save_json, validate_case, validate_report
from .connectors import PublicJsonClient, verify_reference, discover_repositories, collect_reproduction_signals
from .extraction import extract_claim_candidates, extract_references
from .presentation import render_html, export_seed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent draft credibility check; Python 3.10+, no dependencies.")
    parser.add_argument("command", choices=("validate", "build-report", "extract-claims", "compare-claims", "extract-references", "verify-references", "discover-repositories", "collect-signals", "export-sql", "render-report"))
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rubric", type=Path, default=ROOT / "rubrics/credibility_v0.1/rubric.json")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--pdf", type=Path, help="Optional original PDF: verify bytes against the locked version SHA-256")
    parser.add_argument("--online", action="store_true", help="Explicitly run read-only public metadata requests")
    parser.add_argument("--cache", type=Path, default=Path("data/external_evidence/api_cache"))
    parser.add_argument("--mailto", help="Crossref polite-pool contact, optional")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--author-login", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "validate" and args.input.is_dir():
            paths = sorted(args.input.rglob("*.json"))
            if not paths:
                raise ValidationError("No case JSON files found in the specified directory")
            seen = set()
            for path in paths:
                item = validate_case(load_json(path))
                if item["case_id"] in seen:
                    raise ValidationError("Duplicate case_id in dataset: " + item["case_id"])
                seen.add(item["case_id"])
            print(f"PASS: {len(paths)} case files; all draft contracts/evidence/state checks passed")
            return 0
        value = load_json(args.input)
        if args.command == "render-report":
            validate_report(value)
            if not args.output:
                parser.error("render-report requires --output")
            render_html(value, args.output)
            print("HTML report written: " + str(args.output))
            return 0
        validate_case(value)
        if args.pdf:
            actual = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
            if actual != value["paper_version"]["pdf_sha256"]:
                raise ValidationError("PDF SHA-256 does not match locked paper version")
        if args.command == "validate":
            print("PASS: draft schema, evidence locations, state/coverage semantics and source identities")
            return 0
        if args.command in ("verify-references", "collect-signals") and not args.online:
            parser.error("External requests require --online; offline validation/reporting never needs API credentials")
        if args.command == "extract-claims":
            result = {"claims": extract_claim_candidates(value), "claim_scope_complete": False, "scope_complete": False}
        elif args.command == "compare-claims":
            result = {"claim_checks": value["claim_checks"], "scope_complete": value["claim_scope_complete"], "notes": ["检查已标注证据链和条件；本草案没有自动语义裁决模型。"]}
        elif args.command == "extract-references":
            result = extract_references(value)
        elif args.command == "discover-repositories":
            result = {"repositories": discover_repositories(value), "notes": ["只提取论文显式链接，关联状态始终为 candidate；须人工确认。"]}
        elif args.command == "verify-references":
            result = dict(value)
            client = PublicJsonClient(args.cache)
            result["reference_checks"] = [verify_reference(ref, client, args.mailto) for ref in value["references"]]
            validate_case(result)
        elif args.command == "collect-signals":
            result = dict(value)
            client = PublicJsonClient(args.cache)
            signals, coverage = [], []
            # Retain existing human-reviewed signals; query candidates cannot replace them.
            reviewed = {s["signal_id"]: s for s in value["reproduction_signals"] if s["review_status"] == "reviewed"}
            for repo in value["repositories"]:
                if repo["platform"] == "GitHub" and repo["association_status"] == "confirmed":
                    found, scope = collect_reproduction_signals(repo, client, args.max_pages, args.author_login)
                    signals.extend(found)
                    coverage.append(scope)
            combined = {s["signal_id"]: s for s in signals}
            combined.update(reviewed)
            result["reproduction_signals"] = list(combined.values())
            result["code_scope"] = {**value["code_scope"], "repository_checks": coverage}
            validate_case(result)
        else:
            result = build_report(value, load_json(args.rubric))
            if args.command == "export-sql":
                if not args.output:
                    parser.error("export-sql requires --output")
                export_seed(value, result, args.output)
                print("Credibility extension SQL written: " + str(args.output))
                return 0
            if args.html:
                render_html(result, args.html)
        if args.output:
            save_json(args.output, result)
            print("Output written: " + str(args.output))
        else:
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValidationError, OSError, ValueError, KeyError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
