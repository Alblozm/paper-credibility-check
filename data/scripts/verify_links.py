#!/usr/bin/env python3
"""Check extracted HTTP(S) links and persist conservative access results."""
from __future__ import annotations

import argparse
import json
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import certifi

from pipeline_common import ROOT, atomic_json, utc_now


def check(url: str, timeout: int) -> str:
    context = ssl.create_default_context(cafile=certifi.where())
    headers = {"User-Agent": "paper-credibility-check-data/1.0", "Range": "bytes=0-1023"}
    for method in ("HEAD", "GET"):
        try:
            with urlopen(Request(url, headers=headers, method=method), timeout=timeout, context=context) as response:
                return "accessible" if response.status < 400 else "unavailable"
        except HTTPError as exc:
            if exc.code in {401, 403, 405, 429} and method == "HEAD":
                continue
            # Authentication/rate limiting does not prove the resource is gone.
            return "unchecked" if exc.code in {401, 403, 429} else "unavailable"
        except Exception:
            if method == "HEAD":
                continue
    return "unavailable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()
    paths = sorted((ROOT / "data/parsed").glob("paper_*/*/links.json"))
    items = [(path, i, row["url"]) for path in paths
             for i, row in enumerate(json.loads(path.read_text()))]
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(check, url, args.timeout): (path, i) for path, i, url in items}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    stamp = utc_now()
    for path in paths:
        rows = json.loads(path.read_text())
        for i, row in enumerate(rows):
            row["access_status"] = results[(path, i)]
            row["checked_at"] = stamp
        atomic_json(path, rows)
    counts = {s: list(results.values()).count(s) for s in sorted(set(results.values()))}
    print(json.dumps({"links": len(items), "counts": counts, "checked_at": stamp}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
