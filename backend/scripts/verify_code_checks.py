"""Read a parsed benchmark version and save task-three results (public GETs only).

Run after installing this project with pip install -e '.[dev]'.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.credibility.code_checks import check_code
from app.credibility.code_lookup import now
from app.credibility.context import CheckContext
from app.parsing.benchmark import load_parsed_document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed-dir", type=Path, required=True)
    parser.add_argument("--arxiv-id", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    doc = load_parsed_document(args.parsed_dir)
    ctx = CheckContext(
        source={"type": "arxiv", "arxiv_id": args.arxiv_id},
        repo_root=args.parsed_dir.resolve(),
        generated_at=now(),
        ref=None,
        doc=doc,
        unresolved_reason=None,
    )
    result = check_code(ctx)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"{doc.paper.paper_id}: code={result.checks.code_availability}, "
        f"feedback={result.checks.reproduction_signal}, repositories={len(result.checks.repositories)}, "
        f"discussions={len(result.checks.discussions)}, sources={len(result.sources)}"
    )
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
