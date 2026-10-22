# Scaling the sample set to 50

## What the pipeline does and does not establish

`scripts/fetch_papers.py` reads the arXiv Atom API and keeps a paper only when
its **comment field names the venue** — the same check that was done by hand
for the core samples. That is real, primary-source evidence, and it is evidence
of exactly one thing: the venue.

| Field | Established by the pipeline? |
|---|---|
| title, authors, abstract, arXiv id + version, DOI, categories | **yes**, from the arXiv API |
| venue and year | **yes**, but only from the comment field |
| accept / reject decision | **no** — needs OpenReview |
| review scores, meta-review | **no** — needs OpenReview |
| `sample_quality_band` | **no** — bands are review-based strata |
| PDF SHA-256, page count | **no** — needs the PDF downloaded |

Everything in the "no" column is written as `null` with a note saying why. None
of it is guessed, and no discovered paper is promoted past `candidate`.

So `make fetch N=50` gives you **50 verified candidates, not 50 usable samples**.
The gap between the two is human triage, and it is not a formality:

1. Archive the OpenReview decision, official reviews and meta-review.
2. Assign `sample_quality_band` from those reviews.
3. Download the PDF, record SHA-256 and page count.
4. Lock the version, and record whether it predates or postdates the decision.
5. Promote `data_status` from `candidate` to `selected`.

`scripts/validate_manifest.py` refuses to let a row past `candidate` without a
source record and a `pdf_sha256`, so the gate is mechanical rather than a
convention someone has to remember.

## Why the arXiv comment field, and what it misses

It is the only venue signal in the sample set that is machine-readable from a
primary source. It has one systematic blind spot worth knowing: **rejected
papers usually do not carry it**. `paper_004` (Mamba) is precisely that case —
no comment field, because it was rejected — and it is in the curated core
exactly because discovery would never have found it.

The consequence is that a purely auto-discovered pool of 50 is biased toward
accepted papers. A benchmark built only on it would never test whether the
rubric can identify weakness. Rejected and borderline papers have to be added
deliberately, from OpenReview, by a human.

## Recommended composition for 50

| Stratum | Target | How to get it |
|---|---|---|
| Accepted, comment-field verified | ~30 | `make fetch N=40`, then triage |
| Rejected / withdrawn | ~12 | OpenReview browsing; will not appear in discovery |
| Borderline, split reviews | ~5 | OpenReview score spread |
| Deliberate edge cases | ~3 | see below |

Edge cases still uncovered by the current six: missing or anonymous metadata,
a PDF that must fail parsing, and a case that forces a dimension to `uncertain`
because of parse failure rather than paper content.

## Order of work

Do **not** fetch 50 and start parsing. The parser has a known defect — 16 of
`paper_001`'s 28 sections own no text chunk — and `paper_006` is 116 pages. Fix
the chunker first, or 50 papers will multiply one bug into an unanalysable mess.

1. Fix the chunker (paragraph + section boundaries, every section owns ≥1 chunk).
2. Re-parse `paper_001`, confirm the fix, then `paper_006` as the stress case.
3. Finish DP-04 on `paper_001` so there is a human baseline at all.
4. Only then scale the pool.

## Splits (implemented in release 0.6.0)

`splits.calibration` contains the original 30 quality-banded ICLR 2024 papers.
`splits.test` contains 20 papers selected before scoring from the official ICLR
proceedings: 15 from 2026 and 5 from 2025. This is a temporal holdout rather
than a balanced accept/reject benchmark; all 20 are proceedings papers, so that
selection bias must be retained when interpreting their score distribution.
