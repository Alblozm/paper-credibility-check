#!/usr/bin/env python3
"""Deprecated guard: fixed-score batch generation is intentionally disabled.

Use ``scripts/run_pipeline.py`` or ``scripts/score_paper_auto.py`` so every
paper is independently evaluated by a configured model provider.
"""
raise SystemExit(
    "Fixed-score batch assessment was removed because it is not a valid AI evaluation. "
    "Use run_pipeline.py with OPENAI_API_KEY or --scorer command."
)
