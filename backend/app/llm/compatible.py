"""Chat Completions client for models reached through an OpenAI-compatible endpoint (Claude, Gemini).

Each provider gets a capability profile instead of scattered special cases:

- strict_json: whether ``response_format={"type": "json_object"}`` is honoured. Claude is not
  constrained by it and often wraps the object in a Markdown fence, which is removed here; the
  contract validation downstream still decides whether the JSON is acceptable.
- thinking: the model reasons before it answers and its reasoning counts against ``max_tokens``. Each request
  therefore adds ``thinking_headroom`` on top of the answer budget and caps the reasoning with
  ``reasoning_effort``; otherwise a long answer (such as the code review JSON) is cut off and every retry fails
  the same way. Thinking also makes a call slow (Gemini: ~12k reasoning tokens, ~90 s for one code review), so
  ``min_timeout_seconds`` keeps a call from being killed at the general timeout and restarted from scratch.
- context_tokens: a rough input ceiling; a prompt that cannot fit fails clearly instead of being
  truncated by the provider.

Neither profile has a usable built-in web search through the compatible endpoints, so code
discovery uses the separate search transport (see ``discovery_provider_for``).
"""
from __future__ import annotations

import re
import ssl
import time
from dataclasses import dataclass
from typing import Any

import certifi
import httpx

from app.llm import interrupt
from app.llm.base import LLMClient, LLMResponse, LLMToolCall, LLMToolResponse, LLMUsage
from app.llm.retry import LLMEmptyContent, LLMOutputInvalid, LLMRateLimited, LLMTimeout, LLMUnavailable


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    strict_json: bool
    thinking: bool
    min_output_tokens: int
    max_output_tokens: int
    context_tokens: int
    thinking_headroom: int = 0
    reasoning_effort: str | None = None
    min_timeout_seconds: float = 0.0


PROFILES: dict[str, ModelProfile] = {
    "anthropic": ModelProfile("anthropic", strict_json=False, thinking=False,
                              min_output_tokens=1024, max_output_tokens=64_000, context_tokens=200_000),
    "google": ModelProfile("google", strict_json=True, thinking=True,
                           min_output_tokens=8192, max_output_tokens=65_536, context_tokens=1_000_000,
                           thinking_headroom=24_576, reasoning_effort="medium", min_timeout_seconds=300.0),
}

# A single fenced block around the whole answer, e.g. ```json\n{...}\n```.
_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n(.*)\n\s*```\s*$", re.S)


def unfence(text: str) -> str:
    """The JSON inside one Markdown fence; anything else is returned unchanged for validation."""
    match = _FENCE.match(text)
    return match.group(1).strip() if match else text


def _invalid(code: str) -> LLMOutputInvalid:
    return LLMOutputInvalid("provider output failed protocol validation: " + code)


class CompatibleChatClient(LLMClient):
    supports_native_tools = True

    def __init__(self, *, model_id: str, profile: ModelProfile, api_key: str, base_url: str, model: str,
                 timeout: float = 120.0):
        self.model_id = model_id
        self.model_version = model
        self.model = model
        self.profile = profile
        self.provider = profile.provider
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = max(timeout, profile.min_timeout_seconds)

    def _budget(self, requested: int) -> int:
        answer = max(self.profile.min_output_tokens, requested)
        return min(answer + self.profile.thinking_headroom, self.profile.max_output_tokens)

    def _reasoning(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.profile.reasoning_effort:
            payload["reasoning_effort"] = self.profile.reasoning_effort
        return payload

    def _check_context(self, messages: list[dict[str, Any]]) -> None:
        characters = sum(len(str(message.get("content") or "")) for message in messages)
        # About four characters per token for English text; leave room for the answer.
        if characters / 4 > self.profile.context_tokens * 0.9:
            raise LLMUnavailable(f"input of about {characters // 4} tokens exceeds the model context")

    def _post(self, payload: dict[str, Any]) -> tuple[dict, dict, int, int]:
        started = time.perf_counter()
        try:
            verify = ssl.create_default_context(cafile=certifi.where())
            with httpx.Client(timeout=self.timeout, verify=verify) as client:
                response = interrupt.call(lambda: client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
                ))
        except httpx.TimeoutException as exc:
            raise LLMTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(str(exc)) from exc
        if response.status_code == 429:
            raise LLMRateLimited("rate limited by provider")
        if response.status_code >= 400:
            raise LLMUnavailable(f"provider rejected the request: {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise _invalid("invalid_json_response") from exc
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _invalid("invalid_choices")
        if choices[0].get("finish_reason") == "length":
            raise _invalid("response_truncated")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise _invalid("invalid_assistant_message")
        return data, message, response.status_code, int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _usage(data: dict) -> LLMUsage:
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt, completion = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        return LLMUsage(prompt if type(prompt) is int else 0, completion if type(completion) is int else 0)

    def complete_json(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2,
                      max_tokens: int = 8000) -> LLMResponse:
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        self._check_context(messages)
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": self._budget(max_tokens)}
        if self.profile.strict_json:
            payload["response_format"] = {"type": "json_object"}
        if not self.profile.thinking:
            payload["temperature"] = temperature
        data, message, _status, latency = self._post(self._reasoning(payload))
        text = message.get("content") or ""
        if not isinstance(text, str):
            raise _invalid("invalid_assistant_content")
        if not text.strip():
            raise LLMEmptyContent("provider returned empty content")
        return LLMResponse(text=unfence(text), model_id=self.model_id,
                           model_version=data.get("model", self.model_version),
                           usage=self._usage(data), latency_ms=latency, raw=data)

    def complete_tools(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                       max_tokens: int = 8000, tool_choice: str = "auto") -> LLMToolResponse:
        if tool_choice not in {"auto", "none"}:
            raise ValueError("tool_choice must be auto or none")
        self._check_context(messages)
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "tools": tools,
                                   "tool_choice": tool_choice, "max_tokens": self._budget(max_tokens)}
        if not self.profile.thinking:
            payload["temperature"] = 0.2
        data, message, _status, latency = self._post(self._reasoning(payload))
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list) or len(raw_calls) > 20:
            raise _invalid("invalid_tool_calls")
        calls = []
        for call in raw_calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or call.get("type", "function") != "function":
                raise _invalid("invalid_tool_call")
            call_id, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
            if not all(isinstance(item, str) and item for item in (call_id, name, arguments)):
                raise _invalid("invalid_tool_call")
            calls.append(LLMToolCall(call_id, name, arguments))
        if len({call.call_id for call in calls}) != len(calls):
            raise _invalid("duplicate_tool_call_ids")
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise _invalid("invalid_assistant_content")
        if not content.strip() and not calls:
            raise LLMEmptyContent("provider returned empty content")
        # Tool calls go back verbatim: Gemini attaches a thought signature it needs on the next turn.
        assistant = {key: message[key] for key in ("content", "tool_calls") if key in message}
        assistant["role"] = "assistant"
        return LLMToolResponse(
            text=content, tool_calls=calls, assistant_message=assistant, model_id=self.model_id,
            model_version=data.get("model", self.model_version), usage=self._usage(data), latency_ms=latency,
            response_id=data.get("id") if isinstance(data.get("id"), str) else None,
        )
