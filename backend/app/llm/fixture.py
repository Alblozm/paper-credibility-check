"""开发与测试用的假客户端，返回契约 §19 的示例报告。

用途：校验、修复、评分逻辑不需要等真实模型接通就能测。
在 .env 里设 LLM_PROVIDER=fixture 即可让整条流程跑起来。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.llm.base import LLMClient, LLMResponse, LLMUsage

EXAMPLE_REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "contract_example_report.json"


def load_example_report() -> dict:
    return json.loads(EXAMPLE_REPORT_PATH.read_text(encoding="utf-8"))


class FixtureLLMClient(LLMClient):
    """总是返回同一份合法报告；可注入 mutator 制造各种坏输出来测校验层。"""

    model_id = "model_fixture_v1"
    model_version = "fixture-2026-08-30"

    def __init__(self, payload: dict | str | None = None, mutator=None, eligibility_payload: dict | None = None):
        if payload is None:
            payload = load_example_report()
        if mutator is not None and isinstance(payload, dict):
            payload = mutator(payload)
        self._text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self._eligibility_payload = eligibility_payload
        self.calls: list[tuple[str, str]] = []

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 8000,
    ) -> LLMResponse:
        self.calls.append((system_prompt, user_prompt))
        if "bounded eligibility classifier" in system_prompt:
            chunk = re.search(r"\[([^\]| ]+_chunk_[^\]| ]+)", user_prompt)
            payload = self._eligibility_payload or {
                "document_type": "research_paper",
                "language": "english",
                "ml_relevance": "yes",
                "confidence": "high",
                "reason": "Fixture document is treated as an ML research paper.",
                "evidence_chunk_ids": [chunk.group(1)] if chunk else [],
            }
            text = json.dumps(payload, ensure_ascii=False)
            return LLMResponse(
                text=text,
                model_id=self.model_id,
                model_version=self.model_version,
                usage=LLMUsage(prompt_tokens=len(user_prompt) // 4, completion_tokens=len(text) // 4),
                latency_ms=1,
            )
        return LLMResponse(
            text=self._text,
            model_id=self.model_id,
            model_version=self.model_version,
            usage=LLMUsage(prompt_tokens=len(user_prompt) // 4, completion_tokens=len(self._text) // 4),
            latency_ms=1,
        )


class ScriptedLLMClient(FixtureLLMClient):
    """按顺序返回预设的多次响应，用来测 repair pass。"""

    def __init__(self, responses: list[dict | str]):
        super().__init__(responses[0])
        self._responses = responses
        self._index = 0

    def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> LLMResponse:
        self.calls.append((system_prompt, user_prompt))
        payload = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return LLMResponse(text=text, model_id=self.model_id, model_version=self.model_version)
