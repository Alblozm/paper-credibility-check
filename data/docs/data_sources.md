# Data sources

## Required

### arXiv

Use for stable paper identifiers, versions, metadata and PDFs. Record the exact
version (for example, `2401.12345v2`) rather than only the base identifier.

### OpenReview

Use for ICLR submissions, official reviews, confidence scores, discussions,
meta-reviews and final decisions. These are reference signals and sample
selection evidence, not automatic gold labels.

### Official ICLR reviewer guidelines

Use as the source for rubric interpretation. Record the guideline year and URL.

## Optional metadata checks

- Crossref: DOI and publication metadata verification
- Semantic Scholar: identifier mapping and descriptive citation metadata
- Paper-linked GitHub repository: code-availability verification only for MVP

## Out of current MVP scope

Do not build PubPeer aggregation, retraction screening, full GitHub health
analysis, global SOTA verification or web-wide community-signal collection.
