# Pilot data dictionary

The Unified Field Contract v0.4 remains authoritative for assessment output.
This document defines dataset-management fields that sit around that contract.

## Paper manifest

| Field | Meaning |
|---|---|
| `paper_id` | Stable internal identifier, such as `paper_001` |
| `title` | Verified public title |
| `arxiv_id` | Base arXiv identifier when available |
| `arxiv_version` | Exact assessed version, such as `v2` |
| `arxiv_url` | Public abstract-page URL |
| `openreview_forum_id` | Stable OpenReview forum identifier |
| `openreview_url` | Public forum URL |
| `venue` / `year` | Target venue and year |
| `decision` | Public decision; never used alone as paper-quality truth |
| `decision_verified` | `true` only when the decision was confirmed from a primary source (ICLR site, proceedings or an archived OpenReview snapshot). `false` means secondary sources or inference only, and the value must not be relied on |
| `sample_quality_band` | Sampling stratum, not model output rating |
| `challenge_tags` | Semicolon-separated evaluation behaviours |
| `selection_reason` | Why the paper improves benchmark coverage |
| `data_status` | Current lifecycle state |
| `assigned_to` | Optional maintainer for the row |
| `retrieved_at` | UTC source retrieval timestamp |

## Author status

Always interpret `authors` together with `authors_status`:

- `verified`: author names were verified for the assessed version;
- `anonymous_at_assessed_version`: the locked double-blind review PDF was anonymous;
- `missing_requires_review`: names are absent for an unresolved reason.

An empty author array with `anonymous_at_assessed_version` is intentional data,
not a failed extraction. The current release uses this explicit annotation for
`paper_003` and `paper_007` through `paper_030` (25 papers total).

## Allowed sample bands

- `high_consensus`
- `mixed_or_borderline`
- `substantial_concerns`

These labels describe sample selection **only**. They carry no mapping to any
dimension score, overall score or expected score, and the public decision and
public reviews must never be used as scoring input. Model reports continue to
use the ratings defined by Unified Field Contract v0.4.

## Version locking

`arxiv_version` is mandatory, not optional. Record whether the locked version
predates or postdates the venue decision: a camera-ready version is not the
file the public reviews were written about, so review-based calibration must
use the reviewed version.
