"""OpenAI Responses API client.

The rest of the pipeline consumes the provider-neutral ``LLMClient`` protocol,
so selecting GPT only changes this transport layer. Report validation and the
repair pass remain provider independent.
"""
from __future__ import annotations

import ssl
import time
from typing import Any

import certifi
import httpx

from app.llm.base import LLMClient, LLMResponse, LLMToolCall, LLMToolResponse, LLMUsage
from app.llm.retry import (
    LLMEmptyContent,
    LLMOutputInvalid,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
)


class OpenAIClient(LLMClient):
    """Generate JSON reports through OpenAI's Responses API."""

    # Agent loops use Chat Completions tool calling (complete_tools); JSON reports
    # keep using the Responses API (complete_json).
    supports_native_tools = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-5.6-terra",
        reasoning_effort: str = "medium",
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.model_id = "model_gpt_v1"
        self.model_version = model

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 25000,
    ) -> LLMResponse:
        # Existing .env files may still select GPT-4.1 or GPT-4o. Those models
        # accept temperature but do not accept reasoning configuration.
        non_reasoning_model = self.model.startswith(("gpt-4.1", "gpt-4o"))
        payload = {
            "model": self.model,
            "instructions": system_prompt,
            # JSON mode requires the word "json" in the input message itself;
            # the eligibility prompt otherwise mentions it only in instructions.
            "input": f"Return one JSON object.\n\n{user_prompt}",
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": max_tokens,
            # Assessment payloads may contain private uploaded-paper content.
            "store": False,
        }
        if non_reasoning_model:
            payload["temperature"] = temperature
        else:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        started = time.perf_counter()
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        try:
            with httpx.Client(timeout=self.timeout, verify=ssl_context) as client:
                response = client.post(
                    f"{self.base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(str(exc)) from exc

        if response.status_code == 429:
            raise LLMRateLimited("rate limited by provider")
        if response.status_code >= 500:
            raise LLMUnavailable(f"provider returned {response.status_code}")
        if response.status_code >= 400:
            raise LLMUnavailable(f"provider rejected the request: {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMOutputInvalid("provider returned a non-JSON response envelope") from exc

        if data.get("status") == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
            raise LLMOutputInvalid(f"provider returned an incomplete response: {reason}")

        text_parts = [
            content.get("text", "")
            for item in data.get("output", [])
            if item.get("type") == "message"
            for content in item.get("content", [])
            if content.get("type") == "output_text"
        ]
        text = "".join(text_parts)
        if not text.strip():
            raise LLMEmptyContent("provider returned no output_text content")

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model_id=self.model_id,
            model_version=data.get("model", self.model_version),
            usage=LLMUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )

    def complete_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 8000,
        tool_choice: str = "auto",
    ) -> LLMToolResponse:
        """One Responses API tool turn from the shared Chat-style transcript.

        GPT reasoning models accept function tools only on /v1/responses. With
        store=false the encrypted reasoning items are returned in the assistant
        message's opaque ``reasoning_content`` field (never logged by callers)
        and replayed verbatim on the next turn.
        """
        if tool_choice not in {"auto", "none"}:
            raise ValueError("tool_choice must be auto or none")
        instructions, items = _responses_input(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions or None,
            "input": items,
            "tools": [_responses_tool(tool) for tool in tools],
            "tool_choice": tool_choice,
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if self.model.startswith(("gpt-4.1", "gpt-4o")):
            payload["temperature"] = 0.2
        else:
            payload["reasoning"] = {"effort": self.reasoning_effort}
            payload["include"] = ["reasoning.encrypted_content"]
        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=self.timeout, verify=ssl.create_default_context(cafile=certifi.where()),
            ) as client:
                response = client.post(
                    f"{self.base_url}/responses",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(str(exc)) from exc
        if response.status_code == 429:
            raise LLMRateLimited("rate limited by provider")
        if response.status_code >= 400:
            raise LLMUnavailable(f"provider rejected tools request: {response.status_code}")
        try:
            data = response.json()
            output = data["output"]
            if not isinstance(output, list):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMOutputInvalid("provider returned an invalid tools envelope") from exc
        if data.get("status") == "incomplete":
            raise LLMOutputInvalid("provider output failed protocol validation: response_truncated")
        calls, texts = [], []
        for item in output:
            if not isinstance(item, dict):
                raise LLMOutputInvalid("provider output failed protocol validation: invalid_output_item")
            if item.get("type") == "function_call":
                call_id, name, arguments = item.get("call_id"), item.get("name"), item.get("arguments")
                if not all(isinstance(x, str) and x for x in (call_id, name, arguments)):
                    raise LLMOutputInvalid("provider output failed protocol validation: invalid_tool_call")
                calls.append(LLMToolCall(call_id, name, arguments))
            elif item.get("type") == "message":
                texts += [c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text"]
        if len(calls) > 20:
            raise LLMOutputInvalid("provider output failed protocol validation: too_many_tool_calls")
        if len({c.call_id for c in calls}) != len(calls):
            raise LLMOutputInvalid("provider output failed protocol validation: duplicate_tool_call_ids")
        content = "".join(texts)
        if not content.strip() and not calls:
            raise LLMEmptyContent("provider returned neither content nor tool calls")
        assistant: dict[str, Any] = {"role": "assistant", "content": content, "reasoning_content": output}
        if calls:
            assistant["tool_calls"] = [
                {"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        usage = data.get("usage") or {}
        return LLMToolResponse(
            text=content, tool_calls=calls, assistant_message=assistant,
            model_id=self.model_id, model_version=data.get("model", self.model_version),
            usage=LLMUsage(prompt_tokens=usage.get("input_tokens", 0),
                           completion_tokens=usage.get("output_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            response_id=data.get("id") if isinstance(data.get("id"), str) else None,
        )


def _responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function", tool)
    return {"type": "function", "name": function["name"], "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}})}


def _responses_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Translate the shared Chat-style transcript into Responses API input items."""
    instructions, items = [], []
    for row in messages:
        role = row.get("role")
        if role == "system":
            instructions.append(str(row.get("content") or ""))
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": row["tool_call_id"],
                          "output": str(row.get("content") or "")})
        elif role == "assistant" and isinstance(row.get("reasoning_content"), list):
            # Replay this provider's own output items (reasoning, calls, message) verbatim.
            items.extend(row["reasoning_content"])
        elif role == "assistant":
            if row.get("content"):
                items.append({"role": "assistant", "content": str(row["content"])})
            for call in row.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": call["id"],
                              "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
        else:
            items.append({"role": "user", "content": str(row.get("content") or "")})
    return "\n\n".join(part for part in instructions if part), items
