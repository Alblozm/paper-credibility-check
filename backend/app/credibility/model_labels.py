"""One bounded, checkpointed model call that labels a list of items by ID.

The model only classifies text the pipeline already extracted; every decision
that follows (fetching, matching, scoring) stays deterministic in the caller.
"""
from __future__ import annotations

import json
import logging
from typing import TypeVar

from pydantic import ValidationError

from app.credibility.context import CheckContext
from app.credibility.models import CredibilityModelCall
from app.llm.retry import LLMError, LLMOutputInvalid, RetryPolicy, call_with_retry
from app.schemas.common import StrictModel
from app.tracking.run_log import RunRecord, log_run

logger = logging.getLogger(__name__)
_T = TypeVar("_T", bound=StrictModel)


def label_items(
    ctx: CheckContext, *, purpose: str, prompt_version: str, system: str, task: dict, schema: type[_T],
    expected_ids: list[str], calls: list[CredibilityModelCall], id_field: str, max_tokens: int = 4000,
) -> _T:
    """Return validated labels for exactly ``expected_ids``; raise LLMError when none are valid."""
    from app.credibility.runtime import digest
    from app.pipeline.orchestrator import build_llm_client

    client = build_llm_client(ctx.model_id)
    fingerprint = digest({"task": task, "prompt": prompt_version, "model": client.model_version})
    if ctx.checkpoint_load is not None and (cached := ctx.checkpoint_load(purpose, fingerprint)) is not None:
        calls.extend(CredibilityModelCall.model_validate(call) for call in cached["model_calls"])
        return schema.model_validate(cached["result"])

    start = len(calls)

    def complete() -> _T:
        call = CredibilityModelCall(purpose=purpose, provider=provider(client), model_version=client.model_version)
        calls.append(call)
        response = client.complete_json(system, json.dumps(task, ensure_ascii=False), temperature=0.0,
                                        max_tokens=max_tokens)
        call.model_version = response.model_version
        log_run(RunRecord(
            assessment_id=f"credibility:{ctx.paper_version_id}", stage=purpose, model_id=response.model_id,
            model_version=response.model_version, rubric_version="not_applied", prompt_version=prompt_version,
            temperature=0.0, prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens, latency_ms=response.latency_ms, outcome="returned",
        ))
        try:
            labels = schema.model_validate(json.loads(response.text))
            rows = next(value for value in labels.__dict__.values() if isinstance(value, list))
            if sorted(getattr(row, id_field) for row in rows) != sorted(expected_ids):
                raise ValueError("The model must label exactly the given IDs")
        except (json.JSONDecodeError, ValidationError, ValueError, StopIteration) as exc:
            raise LLMOutputInvalid(f"Invalid {purpose} labels: {exc}") from exc
        return labels

    labels = call_with_retry(complete, policy=RetryPolicy(max_attempts=2))
    if ctx.checkpoint_save is not None:
        ctx.checkpoint_save(purpose, fingerprint, {
            "result": labels.model_dump(mode="json"),
            "model_calls": [call.model_dump(mode="json") for call in calls[start:]],
        })
    return labels


def label_or_none(fn, purpose: str):
    """Run a labelling call; None tells the caller to keep its deterministic rules."""
    try:
        return fn()
    except (LLMError, ValueError) as exc:
        logger.warning("%s unavailable; using deterministic rules: %s", purpose, exc)
        return None


def provider(client) -> str:
    identity = f"{type(client).__module__}.{type(client).__name__} {client.model_version}".lower()
    return "deepseek" if "deepseek" in identity else "openai" if "openai" in identity else type(client).__module__
