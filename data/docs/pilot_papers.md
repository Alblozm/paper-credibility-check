# Pilot sample selection

Status: five ICLR 2024 samples selected; one candidate parked pending source
verification. Only `paper_001` has a completed PDF parse.

All samples are ICLR 2024. Holding venue and year constant reduces rubric drift
while the pilot tests the data shape and the annotation process. The sample
bands below are **sampling strata, not five-dimension ratings**, and the public
decision is **never** used as a quality label — it is selection evidence and
post-hoc calibration material only.

Last updated 2026-09-02 (source verification round 2).

## Coverage: what each sample is for

| Paper | Band | Decision | The specific behaviour it tests |
|---|---|---|---|
| `paper_001` | `high_consensus` | Accept | Baseline: theory + experiments + public code; upper-band calibration |
| `paper_002` | `mixed_or_borderline` | Accept | Negative-result paper making **no** SOTA claim; reviewer disagreement |
| `paper_004` | `mixed_or_borderline` | Reject | **Explicit SOTA claim** + rejected-but-influential |
| `paper_005` | `high_consensus` | Accept | **Theory only**: no code, no experiments → `not_applicable` |
| `paper_006` | `high_consensus` | Accept (oral) | **Parse stress**: 116 pages, 120 figures |
| `paper_003` | *(parked)* | unverified | OpenReview-only, sparse metadata — not yet admissible |

Still uncovered, and wanted for the next round: missing or anonymous metadata,
a PDF that must fail parsing, and a case that forces a dimension to `uncertain`
because of parse failure rather than paper content.

## `paper_001`: Tangent Transformers for Composition, Privacy and Removal

- arXiv: https://arxiv.org/abs/2307.08122 (assessed `v3`, revised 2024-05-14)
- OpenReview: https://openreview.net/forum?id=VLFhbOCz5D
- public decision: Accept (poster) — acceptance verified via ICLR proceedings; the
  poster designation itself is **not** independently verified
- pilot value: combines theoretical motivation and empirical results, has public
  code, and received broadly positive reviews while retaining limited concerns.
- caveat: `v3` postdates the ICLR 2024 decision and is very likely the camera
  ready. The public reviews address the submitted version, not the file we scored.

## `paper_002`: Large Language Models Cannot Self-Correct Reasoning Yet

- arXiv: https://arxiv.org/abs/2310.01798 (**lock `v1`**, 2023-10-03)
- OpenReview: https://openreview.net/forum?id=IkmD3fKBPQ
- public decision: Accept (poster) — verified via the ICLR 2024 virtual site
- pilot value: reviewers differed materially; the paper is a negative-result
  study that makes no SOTA claim, which stops the rubric from equating
  leaderboard gains with research value.
- version note: `v2` (2024-03-14) postdates the decision. `v1` is the reviewed
  version and is the correct lock for review calibration.

## `paper_004`: Mamba: Linear-Time Sequence Modeling with Selective State Spaces

- arXiv: https://arxiv.org/abs/2312.00752 (**lock `v1`**, 2023-12-01)
- OpenReview: https://openreview.net/forum?id=AL1fq05o7H
- public decision: Reject — **not verified**; see below
- pilot value: the abstract states plainly that the method "achieves
  state-of-the-art performance across several modalities", and the submission was
  rejected at ICLR 2024 while going on to be widely adopted. It is the only
  sample that tests both hard rules simultaneously: a SOTA claim must not buy a
  high score, and a reject decision must not be read as a quality verdict.
- verification note: unlike every other sample here, the arXiv record carries **no**
  ICLR comments field, and the paper appears in neither the ICLR 2024 proceedings
  nor the virtual site. That is consistent with rejection but does not prove it.
  The reject outcome is reported by secondary sources only; the OpenReview forum
  could not be read. `decision_verified = false`.
- expected failure mode to watch for: a model that reads "state-of-the-art" and
  inflates `research_value` or `soundness_evidence` without checking the evidence,
  or an annotator who anchors low because they know it was rejected.

## `paper_005`: The Expressive Power of Transformers with Chain of Thought

- arXiv: https://arxiv.org/abs/2310.07923 (assessed `v5`, the ICLR camera ready)
- OpenReview: https://openreview.net/forum?id=NjNGlPh8Wh
- public decision: Accept (poster) — verified via the ICLR 2024 virtual site
- pilot value: verified to contain **no experiments, no datasets, no baselines,
  no metrics and no code or data release**. Structure is 5 numbered sections plus
  appendices A–E, carrying 4 theorems, 10 lemmas and 1 corollary.
- why it matters: it is the only sample that can positively exercise
  `not_applicable`, and the only test of the scoring guide's rule that absence of
  code must not by itself force a low score. It is also the first sample that will
  produce `theorem` and `equation` element types — `paper_001` produced only
  tables and figures, so half the `paper_elements.element_type` enum has never
  been exercised.
- expected failure mode to watch for: a model returning `not_available` instead of
  `not_applicable` on the reproducibility checks and marking the paper down for it.

## `paper_006`: MathVista: Evaluating Mathematical Reasoning of Foundation Models in Visual Contexts

- arXiv: https://arxiv.org/abs/2310.02255 (assessed `v3`, 2024-01-21)
- OpenReview: https://openreview.net/forum?id=KUNzEQMWU7
- public decision: Accept (oral) — verified via the ICLR 2024 virtual site and proceedings
- pilot value: the arXiv comments field records **116 pages and 120 figures**,
  against `paper_001`'s 18 pages and 16 elements. The current one-chunk-per-page
  pilot parser would emit ~116 chunks and several hundred elements. This sample is
  chosen precisely to break it and to show what the parser does at scale.
- secondary value: a benchmark/dataset paper, so `data_availability` rather than
  code becomes the primary reproducibility evidence; and a 10-author list, which
  stresses author parsing.

## `paper_003`: MemStranding: Adversarial attacks on temporal graph neural networks — PARKED

- OpenReview: https://openreview.net/forum?id=Pmrc0nEvxf
- status: rolled back from `selected` to `candidate` on 2026-09-02.
- reason: the title and forum id resolve to a real OpenReview submission, but the
  venue, decision, authors and page count could **not** be verified. The
  `substantial_concerns` band was assigned on the premise that public reviews
  raise substantive issues — a premise that is itself unverified.
- to admit it: a human must sign in to OpenReview and archive the decision,
  official reviews and meta-review into `data/source_snapshots/paper_003/`.
  It does not enter parsing or scoring before then.

## Verification gate before annotation

For every paper, confirm and record:

1. The exact PDF assessed and its SHA-256 checksum.
2. The corresponding arXiv/OpenReview version and modification date, **and
   whether that version predates or postdates the venue decision**.
3. Complete public official reviews, meta-review and decision snapshot.
4. Whether author rebuttal changed claims or added results.
5. Whether the parsed text preserves page order, sections and key tables, and
   that **every section owns at least one text chunk**.

Do not begin gold annotation until these checks pass. As of 2026-09-02 only
`paper_001` has reached step 5, and it fails the chunk-coverage clause.
