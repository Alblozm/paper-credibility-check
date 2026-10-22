"""DeepSeek Chat Completions client for JSON reports."""
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

_OUTPUT_CODES = {
    "invalid_json_response", "invalid_response_envelope", "invalid_choices", "invalid_choice",
    "invalid_assistant_message", "response_truncated", "invalid_tool_calls", "too_many_tool_calls",
    "invalid_tool_call", "duplicate_tool_call_ids", "invalid_assistant_content", "empty_response",
    "invalid_usage",
}
_FINISH_REASONS = {"stop", "length", "tool_calls", "function_call", "content_filter",
                   "insufficient_system_resource", "missing", "unknown"}


def safe_provider_diagnostics(value: object) -> dict:
    """Copy only typed, finite protocol facts; never exception/provider prose."""
    if (not isinstance(value, dict) or value.get("provider") != "deepseek"
            or not isinstance(value.get("error_code"), str) or value["error_code"] not in _OUTPUT_CODES):
        return {}
    result = {"provider": "deepseek", "error_code": value["error_code"]}
    if isinstance(value.get("finish_reason"), str) and value["finish_reason"] in _FINISH_REASONS:
        result["finish_reason"] = value["finish_reason"]
    for key, limit in (("http_status", 599), ("choices_count", 1000), ("tool_call_count", 1000)):
        item = value.get(key)
        if type(item) is int and 0 <= item <= limit:
            result[key] = item
    usage = value.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {key: item for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                           if type(item := usage.get(key)) is int and 0 <= item <= 1_000_000_000}
    return result


def _output_error(code: str, data: object, http_status: int, *, empty: bool = False):
    facts = {"provider": "deepseek", "error_code": code, "http_status": http_status}
    if isinstance(data, dict):
        facts["usage"] = data.get("usage")
        choices = data.get("choices")
        if isinstance(choices, list):
            facts["choices_count"] = len(choices)
            choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            finish = choice.get("finish_reason")
            facts["finish_reason"] = (finish if isinstance(finish, str) and finish in _FINISH_REASONS
                                      else "missing" if finish is None else "unknown")
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
                facts["tool_call_count"] = len(message["tool_calls"])
    error = LLMEmptyContent("provider returned empty content") if empty else LLMOutputInvalid(
        "provider output failed protocol validation: " + code
    )
    error.provider_diagnostics = safe_provider_diagnostics(facts)
    return error


def _response_message(response) -> tuple[dict, dict]:
    try:
        data = response.json()
    except ValueError as exc:
        raise _output_error("invalid_json_response", None, response.status_code) from exc
    if not isinstance(data, dict):
        raise _output_error("invalid_response_envelope", data, response.status_code)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _output_error("invalid_choices", data, response.status_code)
    choice = choices[0]
    if not isinstance(choice, dict):
        raise _output_error("invalid_choice", data, response.status_code)
    # Reject the entire truncated turn before examining or admitting any call.
    if choice.get("finish_reason") == "length":
        raise _output_error("response_truncated", data, response.status_code)
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise _output_error("invalid_assistant_message", data, response.status_code)
    return data, message


def _response_usage(data, http_status) -> LLMUsage:
    usage = data.get("usage")
    if usage is None:
        usage = {}
    if (not isinstance(usage, dict) or any(type(usage.get(key, 0)) is not int or usage.get(key, 0) < 0
                                        or usage.get(key, 0) > 1_000_000_000
                                        for key in ("prompt_tokens", "completion_tokens"))):
        raise _output_error("invalid_usage", data, http_status)
    return LLMUsage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


class DeepSeekClient(LLMClient):
    supports_native_tools = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.model_id = "model_standard_v1"   # 契约 §7.2：稳定产品 ID
        self.model_version = model            # 实际供应商版本

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 25000,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
            "max_tokens": max_tokens,
        }
        # DeepSeek 思考模式不支持 temperature；非思考模式保留可重复调优参数。
        if self.thinking_enabled:
            payload["reasoning_effort"] = self.reasoning_effort
        else:
            payload["temperature"] = temperature
        started = time.perf_counter()
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        try:
            with httpx.Client(timeout=self.timeout, verify=ssl_context) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(str(exc)) from exc

        if resp.status_code == 429:
            raise LLMRateLimited("rate limited by provider")
        if resp.status_code >= 500:
            raise LLMUnavailable(f"provider returned {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMUnavailable(f"provider rejected the request: {resp.status_code}")

        data, message = _response_message(resp)
        text = message.get("content")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise _output_error("invalid_assistant_content", data, resp.status_code)
        if not text.strip():
            # DeepSeek json 模式的已知缺陷，必须重试而不是当成失败
            raise _output_error("empty_response", data, resp.status_code, empty=True)

        return LLMResponse(
            text=text,
            model_id=self.model_id,
            model_version=data.get("model", self.model_version),
            usage=_response_usage(data, resp.status_code),
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
        """A real Chat Completions tool turn, separate from the stable JSON path.

        Do not force tool_choice=required: thinking mode does not support it.
        Returning the original assistant fields preserves reasoning_content for
        the next turn, as required by DeepSeek's thinking-tool protocol.
        """
        if tool_choice not in {"auto", "none"}:
            raise ValueError("tool_choice must be auto or none")
        payload: dict[str, Any] = {
            "model": self.model, "messages": messages, "tools": tools,
            "tool_choice": tool_choice, "max_tokens": max_tokens,
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
        }
        if self.thinking_enabled:
            payload["reasoning_effort"] = self.reasoning_effort
        else:
            payload["temperature"] = 0.2
        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=self.timeout, verify=ssl.create_default_context(cafile=certifi.where()),
            ) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(str(exc)) from exc
        if response.status_code == 429:
            raise LLMRateLimited("rate limited by provider")
        if response.status_code >= 400:
            raise LLMUnavailable(f"provider rejected tools request: {response.status_code}")
        data, message = _response_message(response)
        raw_calls = message.get("tool_calls")
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise _output_error("invalid_tool_calls", data, response.status_code)
        if len(raw_calls) > 20:
            raise _output_error("too_many_tool_calls", data, response.status_code)
        calls = []
        for call in raw_calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or call.get("type") != "function":
                raise _output_error("invalid_tool_call", data, response.status_code)
            call_id, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
            if not all(isinstance(x, str) and x for x in (call_id, name, arguments)):
                raise _output_error("invalid_tool_call", data, response.status_code)
            calls.append(LLMToolCall(call_id, name, arguments))
        if len({c.call_id for c in calls}) != len(calls):
            raise _output_error("duplicate_tool_call_ids", data, response.status_code)
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise _output_error("invalid_assistant_content", data, response.status_code)
        if not content.strip() and not calls:
            raise _output_error("empty_response", data, response.status_code, empty=True)
        # Only accepted protocol fields may be sent back. No reasoning is persisted.
        assistant = {key: message[key] for key in ("content", "reasoning_content", "tool_calls") if key in message}
        assistant["role"] = "assistant"
        return LLMToolResponse(
            text=content, tool_calls=calls, assistant_message=assistant,
            model_id=self.model_id, model_version=data.get("model", self.model_version),
            usage=_response_usage(data, response.status_code),
            latency_ms=int((time.perf_counter() - started) * 1000),
            response_id=data.get("id") if isinstance(data.get("id"), str) else None,
        )
