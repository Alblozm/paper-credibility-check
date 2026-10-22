# LLM Analysis Plan

Prompt version: `analysis-v0.2`  
Schema version: `0.4`  
Rubric version: configured by `RUBRIC_VERSION` in `.env`

## Scope

This backend layer turns a parsed paper into one structured report candidate. It
does not calculate final overall score, trust model arithmetic, run code, verify
external SOTA leaderboards, or predict ICLR accept/reject decisions. The
validation layer recalculates deterministic fields and downgrades unverifiable
evidence.

## Model Configuration

Runtime selection is controlled by `.env`.

```text
LLM_PROVIDER=fixture | deepseek | openai
DEEPSEEK_API_KEY=<required for real calls>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING_ENABLED=true
DEEPSEEK_REASONING_EFFORT=medium
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=medium
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=2
```

DeepSeek accepts `medium` for compatibility but maps it to the actual `high`
effort. GPT-5.6 Terra supports `medium` directly. Both clients reserve up to
25,000 output tokens for reasoning plus the final JSON report.

The stable product-facing model id remains `model_standard_v1`; the supplier
model string is recorded as `model_version`.

## Prompt And Context

`app/llm/prompts.py` owns the analysis prompt. It includes:

- Product boundary: ICLR-inspired scorecard, not official review, no
  accept/reject prediction, no code execution, no external SOTA verification.
- Rubric guide: five fixed dimensions, scoring anchors, confidence anchors, and
  SOTA-as-ordinary-claim handling.
- Report schema guide: the exact top-level fields expected by
  `app.schemas.report.Report`.
- Evidence instructions: text evidence must cite a provided location anchor
  chunk id; non-text evidence must use labels from the element catalog.

The context builder currently uses `ParsedDocument`:

- paper metadata
- section list
- parser quality and warnings
- figure/table/equation/theorem catalog when available
- full anchored text from `build_anchored_text`

When the parser is upgraded, the analysis layer should still depend only on
`ParsedDocument`, not on parser-specific internals.

## Validation Contract

DeepSeek JSON mode does not provide strict JSON schema enforcement. The model
output is therefore treated as untrusted text until it passes:

1. JSON parsing
2. Pydantic report schema validation
3. dangling evidence-link removal
4. quote/page/locator/chunk verification
5. reproducibility checklist cross-check
6. evidence-based dimension degradation
7. backend recomputation of overall score, rating, and provisional status

Invalid output can be repaired through `validate_with_repair`; transient model
errors are retried through `call_with_retry`.

## Initial Tuning Log

| Version | Date | Change | Expected Effect | Validation Focus |
|---|---|---|---|---|
| `analysis-v0.1` | 2026-08-30 | Minimal anchored text prompt | End-to-end fixture demo | Schema validation |
| `analysis-v0.2` | 2026-09-05 | Added rubric, schema guide, product boundaries, evidence rules, and confidence anchors | Better structured reports and fewer hallucinated locators | Five-dimension completeness, evidence accuracy, SOTA handling, checklist status |

## Round 2 Inputs

- The data group now provides 30 structured calibration papers and rubric
  `0.1-draft`. They are regression inputs, not a runtime allowlist.
- Round 2 must add a generic arXiv/PDF parser and load the versioned rubric
  instead of limiting the real path to `paper_001`.
- Accuracy benchmarking and human Gold are deferred; functional acceptance
  focuses on using the current input's content, valid evidence locators, and a
  clear no-score result for non-ML or unparseable documents.

## Manual Real-Model Check

For the existing restricted `paper_001` check:

```bash
# Create .env only if it does not already exist, then set DEEPSEEK_API_KEY.
make run-real
```

Submit one arXiv assessment through `/docs` or the frontend, then check:

- response reaches `completed` or returns a clear `MODEL_*` error
- generated report has five dimensions and six checklist items
- major strengths/concerns cite evidence
- warnings identify any dropped, downgraded, or repaired model output
- `app/tracking/run_log.py` output records model, prompt, rubric, latency, token
  counts, repair passes, and validation issue count

For local regression tests while `.env` is set to DeepSeek, force fixture mode:

```bash
LLM_PROVIDER=fixture .venv/bin/python -m pytest -q
```
