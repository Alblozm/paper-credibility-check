"""重试与 repair pass。"""
from __future__ import annotations

import copy

import pytest

from app.llm.deepseek import DeepSeekClient
from app.llm.fixture import FixtureLLMClient, ScriptedLLMClient
from app.llm.openai import OpenAIClient
from app.llm.repair import build_repair_prompt, validate_with_repair
from app.llm.retry import (
    LLMEmptyContent,
    LLMOutputInvalid,
    LLMTimeout,
    LLMUnavailable,
    RetryPolicy,
    call_with_retry,
)
from app.validation.errors import ValidationIssue


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise LLMTimeout("timeout")
        return "ok"

    assert call_with_retry(flaky, RetryPolicy(max_attempts=3), sleep=lambda _: None) == "ok"
    assert calls["n"] == 3


def test_empty_content_is_retried():
    """DeepSeek json 模式的已知缺陷必须走重试，不能直接判失败。"""
    calls = {"n": 0}

    def empty_then_ok():
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMEmptyContent("empty")
        return "ok"

    assert call_with_retry(empty_then_ok, RetryPolicy(max_attempts=2), sleep=lambda _: None) == "ok"


def test_retry_gives_up_after_max_attempts():
    def always_fail():
        raise LLMUnavailable("down")

    with pytest.raises(LLMUnavailable):
        call_with_retry(always_fail, RetryPolicy(max_attempts=2), sleep=lambda _: None)


def test_repair_prompt_lists_every_issue():
    prompt = build_repair_prompt(
        '{"broken": true}',
        [ValidationIssue(code="X", path="dimensions.0.score", message="score must be 1..10")],
    )
    assert "dimensions.0.score" in prompt
    assert "score must be 1..10" in prompt
    assert "EXACT REPORT CONTRACT" in prompt
    assert '"display_name"' in prompt
    assert '"why_it_matters"' in prompt


def test_valid_output_needs_no_repair(example_report):
    client = FixtureLLMClient(example_report)
    result, repairs = validate_with_repair(client, client._text)
    assert result.ok
    assert repairs == 0
    assert client.calls == []


def test_broken_output_is_repaired_in_one_pass(example_report):
    broken = copy.deepcopy(example_report)
    broken["dimensions"][0]["score"] = 42          # 越界，schema 会拒
    client = ScriptedLLMClient([example_report])   # 第一次修复就返回合法结果

    import json

    result, repairs = validate_with_repair(client, json.dumps(broken), max_repairs=2)
    assert result.ok
    assert repairs == 1
    assert len(client.calls) == 1


def test_unrepairable_output_reports_blocking_issues(example_report):
    broken = copy.deepcopy(example_report)
    broken["dimensions"] = broken["dimensions"][:2]
    client = ScriptedLLMClient([broken, broken, broken])

    import json

    result, repairs = validate_with_repair(client, json.dumps(broken), max_repairs=2)
    assert not result.ok
    assert repairs == 2
    assert result.blocking_issues


@pytest.mark.parametrize(
    ("thinking_enabled", "expected_type", "has_temperature"),
    [(False, "disabled", True), (True, "enabled", False)],
)
def test_deepseek_thinking_mode_payload(
    monkeypatch, thinking_enabled, expected_type, has_temperature,
):
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "model": "deepseek-flash",
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }

    class HttpClient:
        def __init__(self, timeout, verify=None):
            captured["timeout"] = timeout
            captured["verify"] = verify

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            return Response()

    monkeypatch.setattr("app.llm.deepseek.httpx.Client", HttpClient)
    client = DeepSeekClient("test-key", thinking_enabled=thinking_enabled)
    response = client.complete_json("return json", "paper")

    assert response.text == '{"ok": true}'
    assert captured["verify"] is not None
    assert captured["payload"]["thinking"] == {"type": expected_type}
    assert ("temperature" in captured["payload"]) is has_temperature
    assert captured["payload"].get("reasoning_effort") == (
        "medium" if thinking_enabled else None
    )
    assert captured["payload"]["model"] == "deepseek-flash"


def test_deepseek_rejects_truncated_json(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "length", "message": {"content": '{"partial":'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 8000},
            }

    class HttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def post(*args, **kwargs):
            return Response()

    monkeypatch.setattr("app.llm.deepseek.httpx.Client", HttpClient)
    with pytest.raises(LLMOutputInvalid):
        DeepSeekClient("test-key").complete_json("return json", "paper")


@pytest.mark.parametrize(
    ("model", "reasoning_effort", "has_reasoning"),
    [
        ("gpt-5.6-terra", "medium", True),
        ("gpt-5.6-terra", "high", True),
        ("gpt-6-sol", "max", True),
        ("gpt-4.1", "medium", False),
        ("gpt-4.1-mini", "medium", False),
        ("gpt-4.1-mini-2025-04-14", "medium", False),
        ("gpt-4.1-nano", "medium", False),
        ("gpt-4o", "medium", False),
        ("gpt-4o-mini", "medium", False),
        ("gpt-4o-mini-2024-07-18", "medium", False),
    ],
)
def test_openai_responses_payload_and_output_mapping(monkeypatch, model, reasoning_effort, has_reasoning):
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "completed",
                "model": model,
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"ok": true}'}],
                }],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            }

    class HttpClient:
        def __init__(self, timeout, verify=None):
            captured.update(timeout=timeout, verify=verify)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            return Response()

    monkeypatch.setattr("app.llm.openai.httpx.Client", HttpClient)
    response = OpenAIClient(
        "test-key", model=model, reasoning_effort=reasoning_effort,
    ).complete_json("system", "paper", temperature=0.4, max_tokens=1234)

    assert response.text == '{"ok": true}'
    assert response.model_id == "model_gpt_v1"
    assert response.model_version == model
    assert response.usage.total_tokens == 18
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["verify"] is not None
    expected_payload = {
        "model": model,
        "instructions": "system",
        "input": "Return one JSON object.\n\npaper",
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 1234,
        "store": False,
    }
    if has_reasoning:
        expected_payload["reasoning"] = {"effort": reasoning_effort}
    else:
        expected_payload["temperature"] = 0.4
    assert captured["payload"] == expected_payload


def test_openai_rejects_incomplete_output(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            }

    class HttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def post(*args, **kwargs):
            return Response()

    monkeypatch.setattr("app.llm.openai.httpx.Client", HttpClient)
    with pytest.raises(LLMOutputInvalid, match="max_output_tokens"):
        OpenAIClient("test-key").complete_json("system", "paper")
