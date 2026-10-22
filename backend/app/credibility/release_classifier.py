"""Model classification of artifact-release claims before code reconciliation.

Paper wording varies too much for a fixed sentence pattern ("we release a
package ... and provide our implementations and model checkpoints"). The model
only labels what each claim asserts; repository association, URL matching and
the resulting outcome stay deterministic in app.credibility.reconcile.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from app.credibility.context import CheckContext
from app.credibility.model_labels import label_items, label_or_none
from app.credibility.models import CredibilityModelCall
from app.schemas.common import StrictModel

PROMPT_VERSION = "credibility-artifact-release-1.1"
PURPOSE = "artifact_release_classification"
# Cheap pre-filter only: a claim that never mentions availability is not sent.
_RELEASE_HINT = re.compile(
    r"\b(?:releas\w*|available|availability|open[- ]?sourc\w*|public\w*|github|gitlab|hugging\s*face|"
    r"repositor\w*|download\w*|provid\w*|shar\w*)\b|https?://",
    re.I,
)
Artifact = Literal["code", "model_weights", "data", "other"]

_SYSTEM = (
    "You classify claims from a machine-learning paper. Decide only what each claim asserts about "
    "making artifacts available. Do not judge whether the claim is true. Return JSON only."
)


class ReleaseLabel(StrictModel):
    claim_id: str
    is_release_claim: bool
    release_is_current: bool
    artifacts: list[Artifact] = Field(default_factory=list)
    other_assertions: bool
    rationale: str = Field(max_length=400)


class _Labels(StrictModel):
    claims: list[ReleaseLabel]


def candidate_text(statement: str, quotes: list[str]) -> bool:
    return any(_RELEASE_HINT.search(text) for text in [statement, *quotes])


def classify_release_claims(ctx: CheckContext, claims: list[dict], calls: list[CredibilityModelCall]) -> dict:
    """Return {claim_id: ReleaseLabel}; raise LLMError when no valid labels are available."""
    task = {
        "task": (
            "For each claim, using its statement and the quoted paper sentences, return one label with: "
            "is_release_claim (true only if the claim asserts that the authors make code, a package/library, "
            "model weights/checkpoints, data or similar artifacts available); release_is_current (false if the "
            "release is only planned, conditional, 'upon acceptance' or in the future); artifacts (every kind "
            "the claim says is released: 'code' for source code, implementations, packages, libraries, toolkits "
            "or scripts; 'model_weights' for checkpoints, weights or pre-trained models; 'data' for datasets, "
            "benchmarks or annotations; 'other' for anything else); other_assertions (true only if the claim also "
            "makes a separate checkable assertion such as a performance result, that the release is complete, a "
            "license, or that results are reproducible; describing what the released artifact is, does or covers "
            "(its name, purpose, supported models or framework) is NOT another assertion); rationale (one short "
            "sentence). Return exactly the given claim_ids."
        ),
        "output_schema": {"claims": [{
            "claim_id": "string", "is_release_claim": "boolean", "release_is_current": "boolean",
            "artifacts": ["code | model_weights | data | other"], "other_assertions": "boolean",
            "rationale": "string",
        }]},
        "claims": claims,
    }
    labels = label_items(
        ctx, purpose=PURPOSE, prompt_version=PROMPT_VERSION, system=_SYSTEM, task=task, schema=_Labels,
        expected_ids=[row["claim_id"] for row in claims], calls=calls, id_field="claim_id",
    )
    return {row.claim_id: row for row in labels.claims}


def safe_classify(ctx: CheckContext, claims: list[dict], calls: list[CredibilityModelCall]) -> dict | None:
    """None tells the caller to fall back to the deterministic sentence rules."""
    if not claims:
        return {}
    return label_or_none(lambda: classify_release_claims(ctx, claims, calls), PURPOSE)
