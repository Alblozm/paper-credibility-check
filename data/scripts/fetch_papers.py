#!/usr/bin/env python3
"""Discover / refresh candidate papers and write one source_record.json each.

Venue attribution comes from the arXiv `comment` field, which is what the data
team verified by hand for the core samples. That establishes the VENUE only.
It does NOT establish the accept/reject decision, the review scores, or the
sample quality band: those need OpenReview and stay empty until a human fills
them in. Nothing here is inferred or guessed.

  python3 scripts/fetch_papers.py --discover 50
  python3 scripts/fetch_papers.py --ids 2307.08122 2310.01798
  python3 scripts/fetch_papers.py --offline data/fixtures/arxiv_sample.xml \
                                  --out-dir data/_demo/source_snapshots
"""
from __future__ import annotations
import argparse, datetime, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arxiv_client, minyaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "data/curated/core_samples.yaml")
REGISTRY = os.path.join(ROOT, "data/manifests/id_registry.json")
DEFAULT_OUT = os.path.join(ROOT, "data/source_snapshots")
CATEGORIES = ["cs.LG", "cs.CL", "cs.CV", "cs.AI"]
VENUE_RE = re.compile(r"\bICLR\b[^0-9]{0,12}(20\d\d)", re.I)


def load_registry():
    if os.path.exists(REGISTRY):
        return json.load(open(REGISTRY, encoding="utf-8"))
    return {"by_arxiv_id": {}, "next_index": 1}


def existing_paper_ids():
    ids = {os.path.basename(p) for p in glob.glob(os.path.join(DEFAULT_OUT, "paper_*"))}
    manifest = os.path.join(ROOT, "data/manifests/pipeline_papers.json")
    if os.path.exists(manifest):
        ids.update(x["paper_id"] for x in json.load(open(manifest, encoding="utf-8")).get("papers", []))
    return ids


def save_registry(reg):
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    json.dump(reg, open(REGISTRY, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def assign_id(reg, arxiv_id, reserved):
    if arxiv_id in reg["by_arxiv_id"]:
        return reg["by_arxiv_id"][arxiv_id]
    while True:
        pid = f"paper_{reg['next_index']:03d}"
        reg["next_index"] += 1
        if pid not in reserved and pid not in reg["by_arxiv_id"].values():
            reg["by_arxiv_id"][arxiv_id] = pid
            return pid


def venue_from_comment(comment):
    if not comment:
        return None, None, None
    m = VENUE_RE.search(comment)
    if not m:
        return None, None, None
    return "ICLR", int(m.group(1)), "arxiv_comment_field"


def to_record(e, paper_id, now):
    venue, year, evidence = venue_from_comment(e.comment)
    return {
        "paper_id": paper_id,
        "paper_version_id": f"{paper_id}_{e.arxiv_version or 'vX'}",
        "title": e.title,
        "authors": e.authors,
        "abstract": e.abstract,
        "language": "en",
        "arxiv_id": e.arxiv_id,
        "arxiv_version": e.arxiv_version,
        "doi": e.doi or f"10.48550/arXiv.{e.arxiv_id}",
        "subjects": e.categories,
        "primary_category": e.primary_category,
        "abs_url": e.abs_url,
        "source_url": e.pdf_url,
        "arxiv_comments": e.comment,
        "arxiv_published": e.published,
        "arxiv_updated": e.updated,
        "venue": venue,
        "publication_year": year,
        "venue_evidence": evidence,
        # --- everything below needs a human or a source this pipeline cannot read ---
        "decision": None,
        "decision_verified": False,
        "decision_note": ("Not derivable from arXiv. Requires an archived OpenReview "
                          "snapshot. Until then this paper must not be used for any "
                          "decision-based analysis."),
        "openreview_forum_id": None,
        "sample_quality_band": None,
        "sample_quality_band_note": ("Bands are review-based strata and cannot be "
                                     "assigned without the public reviews."),
        "version_history": None,
        "version_history_note": ("The arXiv API returns only the latest version. Full "
                                 "history must be read from the abstract page if the "
                                 "sample is used for review calibration."),
        "pdf_sha256": None, "pdf_sha256_status": "pending_download",
        "page_count": None, "page_count_status": "pending_download",
        "parse_status": "pending",
        "data_status": "candidate",
        "retrieved_at": now,
        "retrieval_method": "arxiv_atom_api",
        "storage_policy": ("The PDF is the source of truth and is excluded from Git; "
                           "production should use private object storage."),
        "assessment_scope": ("Paper-internal evidence only; public reviews and the "
                             "venue decision are excluded from AI scoring."),
        "blocking": ("Cannot enter paper_versions: the schema requires pdf_sha256 "
                     "CHAR(64) NOT NULL and pdf_object_key NOT NULL."),
    }


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--discover", type=int, metavar="N")
    g.add_argument("--ids", nargs="+")
    g.add_argument("--offline", metavar="XML")
    ap.add_argument("--year", default="2024")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--page-size", type=int, default=100)
    a = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = {c["paper_id"]: c for c in minyaml.load_list(CORE)}
    core_arxiv = {str(c["arxiv_id"]): c["paper_id"] for c in core.values()}

    if a.offline:
        entries = arxiv_client.parse(open(a.offline, encoding="utf-8").read())
        print(f"[offline] parsed {len(entries)} entries from {a.offline}")
    elif a.ids:
        entries = arxiv_client.by_ids(a.ids)
        print(f"[ids] fetched {len(entries)} entries")
    else:
        wanted, entries, seen = a.discover, [], set()
        query = " OR ".join(f"cat:{c}" for c in CATEGORIES)
        query = f'({query}) AND co:"ICLR {a.year}"'
        print(f"[discover] search_query = {query}")
        start = 0
        while len(entries) < wanted:
            batch = arxiv_client.search(query, start=start, max_results=a.page_size)
            if not batch:
                print("[discover] arXiv returned no more results; stopping early")
                break
            for e in batch:
                if e.arxiv_id in seen:
                    continue
                seen.add(e.arxiv_id)
                if venue_from_comment(e.comment)[0] is None:
                    continue          # comment did not actually name the venue
                entries.append(e)
                if len(entries) >= wanted:
                    break
            start += a.page_size
            print(f"[discover] {len(entries)}/{wanted} kept after {start} scanned")

    reg = load_registry()
    reserved = existing_paper_ids() | set(core)
    numeric = [int(x.split("_")[-1]) for x in reserved if re.fullmatch(r"paper_\d+", x)]
    reg["next_index"] = max(reg.get("next_index", 1), (max(numeric) + 1) if numeric else 1)
    for aid, pid in core_arxiv.items():
        reg["by_arxiv_id"].setdefault(aid, pid)
    written = skipped = 0
    for e in entries:
        pid = core_arxiv.get(e.arxiv_id) or assign_id(reg, e.arxiv_id, reserved)
        reserved.add(pid)
        if pid in core and a.out_dir == DEFAULT_OUT:
            skipped += 1                       # never overwrite a hand-verified record
            continue
        d = os.path.join(a.out_dir, pid)
        os.makedirs(d, exist_ok=True)
        rec = to_record(e, pid, now)
        json.dump(rec, open(os.path.join(d, "source_record.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        written += 1
    if a.out_dir == DEFAULT_OUT:
        save_registry(reg)
    print(f"[done] wrote {written} source records, protected {skipped} hand-verified ones")
    print(f"[done] output: {a.out_dir}")


if __name__ == "__main__":
    main()
