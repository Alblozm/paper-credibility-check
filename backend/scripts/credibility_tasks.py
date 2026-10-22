#!/usr/bin/env python3
"""Explicit local task inspection, interrupted/failed-stage recovery and offline replay."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.credibility import runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "inspect", "resume", "replay"])
    parser.add_argument("assessment_id", nargs="?")
    parser.add_argument("--force", action="store_true", help="Explicitly revoke an old worker lease; stop it first")
    parser.add_argument("--refresh-failures", action="store_true",
                        help="Clear failed external response caches on resume")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    store = runtime.get_task_store()
    if args.action == "list":
        result = store.list_recoverable_tasks()
    else:
        if not args.assessment_id:
            parser.error("assessment_id is required")
        task = store.load_task(args.assessment_id)
        if task is None:
            parser.error("Unknown assessment")
        if args.action == "inspect":
            result = {"metadata": store.load_task_metadata(args.assessment_id), "assessment": task}
        elif args.action == "replay":
            result = runtime.replay_task(args.assessment_id, store=store)
        else:
            from app.api.routes_credibility import _run

            metadata = store.load_task_metadata(args.assessment_id)
            if args.refresh_failures:
                from app.credibility.code_lookup import LOOKUP
                from app.credibility.references import _LOOKUP

                LOOKUP.clear_failed_cache()
                _LOOKUP.clear_failed_cache()
            _run(task["assessment_id"], task["model_id"], task["source"], task["created_at"],
                 metadata["versions"]["rubric_version"], resume=True, force=args.force)
            result = store.load_task(args.assessment_id)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n")
        print(json.dumps({"output": str(args.output), "action": args.action}))
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
