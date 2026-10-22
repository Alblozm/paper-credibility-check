"""LLM 客户端接口。

模型客户端只需要实现这个 Protocol，校验与重试逻辑就能直接复用。

⚠️ 重要约束：DeepSeek 只支持 `response_format={"type": "json_object"}`，
**没有 strict json_schema**。也就是说没有任何供应商级别的结构保证，
JSON 是否符合契约完全由 app.validation 负责。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    text: str
    model_id: str
    model_version: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0
    raw: dict | None = None


@dataclass(frozen=True)
class LLMToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class LLMToolResponse:
    """One provider tool turn; assistant_message is for internal round-tripping only.

    In particular, thinking providers may require their opaque reasoning field
    in subsequent requests. Callers must never log that field as an explanation.
    """

    text: str
    tool_calls: list[LLMToolCall]
    assistant_message: dict[str, Any]
    model_id: str
    model_version: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0
    response_id: str | None = None


class LLMClient(Protocol):
    """所有模型客户端的统一形状。"""

    model_id: str
    model_version: str

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 8000,
    ) -> LLMResponse:
        """返回一段应当是 JSON 的文本。不做任何校验——校验是 app.validation 的事。"""
        ...
