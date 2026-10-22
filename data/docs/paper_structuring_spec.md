# Paper structuring specification v0.1

## Purpose

Structuring is a loss-minimising conversion of a PDF into queryable records. It
does not judge paper quality. The original PDF remains the source of truth; all
parsed records point back to its exact version and location.

## Structured layers

### 1. Identity and provenance

Describes which paper and which exact source was processed.

| Feature | Description |
|---|---|
| `paper_id` | Stable internal paper identifier |
| `title` | Verified title |
| `authors` | Ordered author names; empty for anonymous submissions |
| `abstract` | Abstract as printed in the assessed version |
| `language` | ISO language code |
| `arxiv_id` / `arxiv_version` | arXiv identity and exact version |
| `openreview_forum_id` | OpenReview discussion identity |
| `doi` | DOI when reliably matched |
| `venue`, `year`, `track` | Submission/publication context |
| `source_url` | Public provenance URL |
| `retrieved_at` | Retrieval time in UTC |
| `pdf_sha256` | Exact-file checksum |

### 2. Document and parsing quality

Describes the physical document and whether automatic extraction can be trusted.

| Feature | Description |
|---|---|
| `page_count` | PDF page count |
| `parser_name` / `parser_version` | Parser provenance |
| `parse_status` | `pending`, `processing`, `completed`, or `failed` |
| `parse_quality` | `good`, `partial`, or `poor` |
| `parse_warnings` | Machine-readable warning codes |
| `has_ocr` | Whether OCR was required |
| `reading_order_quality` | Confidence that columns were reconstructed correctly |

### 3. Document hierarchy

Represents the paper's navigational structure.

| Feature | Description |
|---|---|
| `section_id` | Stable section identifier within a paper version |
| `parent_section_id` | Parent section for nested headings |
| `section_number` | Printed section number when present |
| `title` | Section title |
| `level` | Heading depth |
| `sequence_index` | Document order |
| `page_start`, `page_end` | Physical page span |

Expected section roles may include `abstract`, `introduction`, `related_work`,
`method`, `theory`, `experiments`, `results`, `limitations`, `conclusion`,
`references` and `appendix`. The printed title is preserved even when a role is
inferred.

### 4. Text chunks

Long text is stored as ordered chunks, not one full-paper cell. Prefer paragraph
boundaries, do not cross section boundaries, and target roughly 500-1,000 model
tokens per chunk. Preserve a small overlap only when needed for continuity.

| Feature | Description |
|---|---|
| `chunk_id` | Stable chunk identifier |
| `section_id` | Owning section |
| `sequence_index` | Global reading order |
| `chunk_index_in_section` | Section-local order |
| `page_start`, `page_end` | Source pages |
| `content_type` | `body_text`, `caption_context`, `footnote`, etc. |
| `text_content` | Extracted text |
| `token_count`, `char_count` | Size metadata |
| `location_confidence` | `high`, `medium`, or `low` |

### 5. Paper elements

Captures non-prose objects required for evidence checking.

| Feature | Description |
|---|---|
| `element_type` | `figure`, `table`, `equation`, or `theorem` |
| `label` | Printed identifier such as `Table 2` |
| `caption` | Printed caption when present |
| `page_number` | Physical PDF page |
| `section_id` | Owning section |
| `extracted_text` | Best-effort table/OCR/text representation |
| `asset_object_key` | Optional screenshot/object-storage key |
| `parse_quality` | Element-specific parse quality |

MVP does not require perfect row-column reconstruction. A table can be useful
with its label, caption, page, screenshot and nearby explanatory text.

### 6. Semantic study descriptors

These are factual extractions used to route evidence to the scoring prompt. They
are not scores.

| Group | Features |
|---|---|
| Research problem | problem statement, motivation, claimed gap, target users/domain |
| Contributions | author-claimed contribution text and type |
| Claims | claim text, claim type, scope and claimed outcome |
| Method | method name, components, assumptions, inputs and outputs |
| Evaluation | datasets, tasks, baselines, metrics and evaluation protocol |
| Reliability checks | ablations, sensitivity analysis, repeated runs, variance/CI, significance tests |
| Limitations | stated limitations, boundary conditions and failure cases |

Every semantic descriptor records its supporting `chunk_id` or `element_id` and
must be marked `uncertain` when extraction is not reliable.

### 7. Reproducibility disclosures

Extract the six locked checklist items without judging overall paper quality:

- `code_availability`
- `data_availability`
- `random_seed`
- `hyperparameters`
- `hardware_environment`
- `preprocessing_details`

Each uses `available`, `not_available`, `unable_to_verify`, or
`not_applicable`, plus evidence. A theory paper without code is not
automatically low quality.

### 8. Links and references

Capture paper-provided URLs and bibliographic references separately.

| Feature | Description |
|---|---|
| `link_type` | `code`, `data`, `project`, `appendix`, or `other` |
| `url` | Normalised URL |
| `source_chunk_id` | Where the link appears |
| `access_status` | Optional retrieval result |

External reviews and decisions remain provenance/reference data and must not be
mixed into paper-internal evidence.

## Parsing acceptance gate

A paper can be scored only when:

1. the exact PDF version and checksum are recorded;
2. title and abstract are verified;
3. reading order is usable;
4. major sections are present and ordered;
5. chunks carry page and section locations;
6. key result tables/figures have at least labels, captions and pages;
7. failures and low-confidence locations are explicit.

`poor` parsing does not silently proceed to confident scoring. It produces a
partial or unable-to-assess result.
