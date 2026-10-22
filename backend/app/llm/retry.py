"""超时、限流、空返回的重试。

DeepSeek 官方文档明确提到 json 模式**偶尔返回空内容**，因此「空返回」必须
是一条独立的重试分支，不能只处理超时和限流。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.schemas.enums import ErrorCode

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """所有模型调用异常的基类。"""

    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    retryable: bool = False


class LLMUnavailable(LLMError):
    error_code = ErrorCode.MODEL_UNAVAILABLE
    retryable = True


class LLMTimeout(LLMError):
    error_code = ErrorCode.MODEL_TIMEOUT
    retryable = True


class LLMRateLimited(LLMError):
    error_code = ErrorCode.MODEL_UNAVAILABLE
    retryable = True


class LLMEmptyContent(LLMError):
    """DeepSeek json 模式的已知缺陷：返回 200 但 content 为空。"""

    error_code = ErrorCode.MODEL_OUTPUT_INVALID
    retryable = True


class LLMOutputInvalid(LLMError):
    error_code = ErrorCode.MODEL_OUTPUT_INVALID
    retryable = True


@dataclass
class RetryPolicy:
    max_attempts: int = 3          # 首次 + 2 次重试
    base_delay_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_delay_seconds: float = 20.0

    def delay_for(self, attempt: int) -> float:
        return min(
            self.base_delay_seconds * (self.backoff_factor ** (attempt - 1)),
            self.max_delay_seconds,
        )


def call_with_retry(fn, policy: RetryPolicy | None = None, *, sleep=time.sleep):
    """执行 fn()，对可重试的 LLMError 按退避重试。

    `sleep` 参数是为了让单元测试跑得快，不要在生产代码里传。
    """
    policy = policy or RetryPolicy()
    last: LLMError | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except LLMError as exc:
            last = exc
            if not exc.retryable or attempt == policy.max_attempts:
                raise
            delay = policy.delay_for(attempt)
            logger.warning(
                "llm call failed (attempt %s/%s): %s; retrying in %.1fs",
                attempt,
                policy.max_attempts,
                exc.__class__.__name__,
                delay,
            )
            sleep(delay)

    assert last is not None
    raise last
