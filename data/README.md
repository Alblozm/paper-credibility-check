# Benchmark data

**English** · [中文](README.zh-CN.md)

A small, evidence-backed benchmark of 50 machine learning papers used to build, calibrate and regression-test Paper Credibility Check. Dataset `iclr_pilot`, release `0.6.0-ai-final` (see `data/manifests/release.json`).

- **Papers**: a balanced calibration split of 30 ICLR 2024 papers, plus a temporal holdout of 15 ICLR 2026 and 5 ICLR 2025 proceedings papers.
- **Structured text**: every paper is parsed into metadata, sections, text chunks and paper elements (tables, figures), each traceable to a page.
- **Five-dimension scores**: evidence-bound AI assessments under rubric `iclr_inspired_due_diligence` `0.1-draft`.
- **Credibility reports**: all 50 papers have claims & evidence, references and code & reproduction scores plus an overall score under the provisional rubric `0.2-provisional-complete`.

These are AI benchmark scores for development and prompt calibration. They are not human gold labels, do not predict acceptance, and do not claim that any experiment was reproduced. A failed technical lookup or missing material lowers verifiability, and the original failure state stays visible in the record.

## Quick start

Python 3.9+ (the backend needs 3.10+).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make validate        # offline cross-file acceptance checks, no API key
python3 -m pytest tests
```

Load everything into PostgreSQL:

```bash
createdb pcc_pilot
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/001_initial_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/020_seed_all_structured_papers.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/030_credibility_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/032_credibility_seed_all_ai_batch.sql
```

`make db` runs the first two files for you (database name `PGDB`, default `pcc_pilot`).

## Make targets

```bash
make validate   # manifest and full cross-file acceptance gates (offline)
make sql        # regenerate sql/020_seed_all_structured_papers.sql
make db         # load schema + full seed into $(PGDB)
make pipeline   # download and parse the manifest, keeping released scores (network)
make resume     # resume download/parse
make pilot      # rebuild the six-paper regression set without API calls
make score      # real model scoring; requires OPENAI_API_KEY
```

`python3 scripts/rebuild_credibility_reports.py` rebuilds the saved credibility reports and their SQL offline, with no API key.

## Layout

| Path | Contents |
|---|---|
| `data/manifests/` | Paper list, dataset membership, release status |
| `data/source_snapshots/` | Source version, URL, checksum and storage policy per paper |
| `data/parsed/` | Sections, text chunks and elements that passed the quality gates |
| `data/annotations/` | AI trial scores and templates for optional human review |
| `data/credibility_cases/` | Evidence cases fed to the credibility check |
| `data/credibility_reports/` | Generated credibility reports for all 50 papers |
| `data/model_runs/` | Model run records for the five-dimension scores |
| `data/quality_reports/` | Parse and validation reports |
| `credibility_r2/` | Dependency-free credibility case/report toolkit and CLI |
| `rubrics/` | Versioned scoring rules and anchors |
| `schemas/` | JSON Schemas for every record type |
| `scripts/` | Collection, parsing, scoring, validation and SQL export |
| `sql/` | PostgreSQL schema and seeds |
| `docs/` | Field definitions, structuring spec, workflow and data sources |

Start with [docs/data_dictionary.md](docs/data_dictionary.md), [docs/paper_structuring_spec.md](docs/paper_structuring_spec.md) and [rubrics/v0.1/scoring_guide.md](rubrics/v0.1/scoring_guide.md). The credibility batch is described in [docs/credibility_pipeline.zh-CN.md](docs/credibility_pipeline.zh-CN.md) and [docs/credibility_batch_results.zh-CN.md](docs/credibility_batch_results.zh-CN.md).

## Workflow for adding a paper

1. Select a public ICLR/OpenReview paper and record why it is useful.
2. Record stable identifiers and source URLs in `data/manifests/papers.csv`.
3. Pin the exact paper version and its checksum. Raw PDFs are not committed.
4. Parse the paper into metadata, sections, text chunks and paper elements.
5. Score it with the locked five dimensions using only evidence from the paper.
6. Validate evidence links, scores, structure and database constraints.
7. Export the record for the backend.

See [docs/workflow.md](docs/workflow.md) for status definitions.

## Privacy and storage

Do not commit uploaded PDFs, API keys or private material. Public paper PDFs are referenced by URL, version and SHA-256 checksum and stored outside git when downloaded.
