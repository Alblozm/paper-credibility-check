"""Claude and Gemini through an OpenAI-compatible endpoint: payloads follow each capability profile."""
from __future__ import annotations

import pytest

from app.llm.compatible import PROFILES, CompatibleChatClient, unfence
from app.llm.retry import LLMEmptyContent, LLMOutputInvalid, LLMUnavailable


def _client(provider: str) -> CompatibleChatClient:
    model = "claude-sonnet-5" if provider == "anthropic" else "gemini-3.7-flash"
    return CompatibleChatClient(model_id=f"model_{provider}", profile=PROFILES[provider], api_key="key",
                                base_url="https://api.example.com/v1/", model=model)


def _stub(monkeypatch, body: dict, captured: dict) -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return body

    class HttpClient:
        def __init__(self, timeout, verify=None):
            captured["verify"] = verify

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            return Response()

    monkeypatch.setattr("app.llm.compatible.httpx.Client", HttpClient)


def _reply(content=None, tool_calls=None, finish="stop") -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"model": "served-model", "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7}}


def test_claude_json_is_unfenced_and_sent_without_json_mode(monkeypatch):
    captured = {}
    _stub(monkeypatch, _reply('```json\n{"ok": true}\n```'), captured)
    response = _client("anthropic").complete_json("system", "paper", temperature=0.0, max_tokens=500)
    assert response.text == '{"ok": true}' and response.model_version == "served-model"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert "response_format" not in captured["payload"] and captured["payload"]["temperature"] == 0.0
    # The output budget never drops below the profile floor.
    assert captured["payload"]["max_tokens"] == PROFILES["anthropic"].min_output_tokens


def test_gemini_uses_json_mode_and_leaves_room_for_thinking(monkeypatch):
    captured = {}
    _stub(monkeypatch, _reply('{"ok": true}'), captured)
    _client("google").complete_json("system", "paper", max_tokens=100)
    payload = captured["payload"]
    google = PROFILES["google"]
    assert payload["response_format"] == {"type": "json_object"} and "temperature" not in payload
    # Reasoning counts against max_tokens, so the answer budget gets headroom and the reasoning is capped.
    assert payload["max_tokens"] == google.min_output_tokens + google.thinking_headroom
    assert payload["reasoning_effort"] == "medium"
    captured.clear()
    _client("google").complete_json("system", "paper", max_tokens=60_000)
    assert captured["payload"]["max_tokens"] == google.max_output_tokens


def test_tool_calls_round_trip_verbatim(monkeypatch):
    call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "x"}'},
            "extra_content": {"google": {"thought_signature": "opaque"}}}
    _stub(monkeypatch, _reply(None, [call], finish="tool_calls"), {})
    response = _client("google").complete_tools([{"role": "user", "content": "find"}], [], max_tokens=1000)
    assert [(c.call_id, c.name) for c in response.tool_calls] == [("call_1", "lookup")]
    assert response.assistant_message["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "opaque"


def test_truncated_and_empty_answers_are_errors(monkeypatch):
    _stub(monkeypatch, _reply("{", finish="length"), {})
    with pytest.raises(LLMOutputInvalid):
        _client("anthropic").complete_json("s", "u")
    _stub(monkeypatch, _reply("  "), {})
    with pytest.raises(LLMEmptyContent):
        _client("anthropic").complete_json("s", "u")


def test_input_beyond_the_context_fails_clearly():
    with pytest.raises(LLMUnavailable, match="exceeds the model context"):
        _client("anthropic").complete_json("s", "x" * 800_000)


def test_unfence_leaves_other_text_for_validation():
    assert unfence('{"a": 1}') == '{"a": 1}'
    assert unfence("Here you go:\n```json\n{}\n```") == "Here you go:\n```json\n{}\n```"


def test_factory_routes_claude_and_gemini(monkeypatch):
    from app.config import get_settings
    from app.pipeline.orchestrator import build_llm_client

    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "claude-key")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-key")
    claude, gemini = build_llm_client("model_claude_v1"), build_llm_client("model_gemini_v1")
    assert (claude.provider, claude.api_key, claude.model) == ("anthropic", "claude-key", settings.anthropic_model)
    assert (gemini.provider, gemini.api_key, gemini.model) == ("google", "gemini-key", settings.gemini_model)


def test_model_list_hides_deepseek_and_gates_new_models(monkeypatch):
    from app.api.routes_options import list_models
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "claude-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    listed = {item.model_id: item for item in list_models()}
    assert "model_standard_v1" not in listed and "model_gemini_v1" not in listed
    assert listed["model_claude_v1"].is_available is False
    monkeypatch.setattr(settings, "claude_enabled", True)
    assert {item.model_id: item for item in list_models()}["model_claude_v1"].is_available is True


def test_declared_provider_is_recorded_and_search_uses_the_openai_transport(monkeypatch):
    from app.config import get_settings
    from app.credibility.claim_model import _provider
    from app.credibility.code_discovery_provider import OpenAICodeDiscoveryProvider, discovery_provider_for
    from app.credibility.model_labels import provider

    client = _client("anthropic")
    assert _provider(client) == provider(client) == "anthropic"
    monkeypatch.setattr(get_settings(), "openai_api_key", "openai-key")
    assert isinstance(discovery_provider_for(client, max_requests=4, deadline_seconds=60), OpenAICodeDiscoveryProvider)
    monkeypatch.setattr(get_settings(), "openai_api_key", "")
    assert discovery_provider_for(client, max_requests=4, deadline_seconds=60) is None


def test_thinking_models_get_a_longer_call_timeout():
    gemini = CompatibleChatClient(model_id="model_gemini_v1", profile=PROFILES["google"], api_key="k",
                                  base_url="https://api.example.com/v1", model="gemini", timeout=120)
    claude = CompatibleChatClient(model_id="model_claude_v1", profile=PROFILES["anthropic"], api_key="k",
                                  base_url="https://api.example.com/v1", model="claude", timeout=120)
    assert gemini.timeout >= 300
    assert claude.timeout == 120
