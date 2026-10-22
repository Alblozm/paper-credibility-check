"""GPT can drive the agent loops and code discovery without any DeepSeek endpoint."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import get_settings
from app.credibility import code_discovery_provider as provider_module
from app.credibility.code_discovery_provider import (
    DeepSeekCodeDiscoveryProvider,
    OpenAICodeDiscoveryProvider,
    discovery_provider_for,
)
from app.llm import openai as openai_module
from app.llm.openai import OpenAIClient
from app.llm.retry import LLMOutputInvalid

TOOLS = [{"type": "function", "function": {"name": "search_paper_code", "parameters": {"type": "object"}}}]


def _mock(monkeypatch, module, handler):
    original = httpx.Client
    monkeypatch.setattr(module.httpx, "Client",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_complete_tools_uses_the_responses_api_and_replays_reasoning(monkeypatch):
    seen = []
    output = [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"},
        {"type": "function_call", "call_id": "call_1", "name": "search_paper_code",
         "arguments": "{\"query_id\": \"q1\"}"},
    ]

    def handler(request):
        assert request.url.path == "/v1/responses"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "resp_1", "model": "gpt-5.6-luna", "status": "completed",
                                         "output": output, "usage": {"input_tokens": 10, "output_tokens": 5}})

    _mock(monkeypatch, openai_module, handler)
    client = OpenAIClient(api_key="TEST_ONLY", model="gpt-5.6-luna")
    messages = [{"role": "system", "content": "Use tools."}, {"role": "user", "content": "find code"}]
    response = client.complete_tools(messages, TOOLS, max_tokens=2000)
    first = seen[0]
    assert first["instructions"] == "Use tools." and first["input"] == [{"role": "user", "content": "find code"}]
    assert first["tools"][0] == {"type": "function", "name": "search_paper_code", "description": "",
                                 "parameters": {"type": "object"}}
    assert first["reasoning"] == {"effort": "medium"} and first["store"] is False
    assert first["include"] == ["reasoning.encrypted_content"] and first["max_output_tokens"] == 2000
    assert [(c.call_id, c.name) for c in response.tool_calls] == [("call_1", "search_paper_code")]
    assert response.usage.prompt_tokens == 10 and response.response_id == "resp_1"
    # Next turn: the provider's own items are replayed verbatim before the tool result.
    messages += [response.assistant_message, {"role": "tool", "tool_call_id": "call_1", "content": "{}"}]
    client.complete_tools(messages, TOOLS, tool_choice="none")
    assert seen[1]["input"][1:] == output + [{"type": "function_call_output", "call_id": "call_1", "output": "{}"}]
    assert seen[1]["tool_choice"] == "none"
    assert client.supports_native_tools is True


def test_chat_style_transcripts_from_other_providers_are_translated():
    from app.llm.openai import _responses_input

    instructions, items = _responses_input([
        {"role": "system", "content": "S"},
        {"role": "assistant", "content": "thinking aloud", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ])
    assert instructions == "S"
    assert items == [{"role": "assistant", "content": "thinking aloud"},
                     {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
                     {"type": "function_call_output", "call_id": "c1", "output": "result"}]


@pytest.mark.parametrize("body,error", [
    ({"status": "incomplete", "output": []}, "response_truncated"),
    ({"status": "completed", "output": [{"type": "function_call", "call_id": "", "name": "f", "arguments": "{}"}]},
     "invalid_tool_call"),
])
def test_invalid_tool_turns_are_rejected(monkeypatch, body, error):
    _mock(monkeypatch, openai_module, lambda request: httpx.Response(200, json=body))
    with pytest.raises(LLMOutputInvalid, match=error):
        OpenAIClient(api_key="TEST_ONLY").complete_tools([{"role": "user", "content": "x"}], TOOLS)


def _provider(monkeypatch, handler):
    monkeypatch.setattr(get_settings(), "openai_api_key", "TEST_ONLY")
    return OpenAICodeDiscoveryProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=lambda host: ["93.184.216.34"],
    )


def test_openai_search_returns_cited_pages_as_candidates(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["tools"] == [{"type": "web_search"}] and "MathVista" in body["input"]
        text = "The official MathVista repository is on GitHub."
        return httpx.Response(200, json={"output": [{"type": "web_search_call"}, {"type": "message", "content": [{
            "type": "output_text", "text": text, "annotations": [
                {"type": "url_citation", "url": "https://github.com/lupantech/MathVista?utm_source=openai",
                 "title": "MathVista",
                 "start_index": 0, "end_index": len(text)},
                {"type": "url_citation", "url": "http://127.0.0.1/private", "title": "x",
                 "start_index": 0, "end_index": 1},
            ]}]}]})

    result = _provider(monkeypatch, handler).search('"MathVista" code', count=5)
    assert result.status == "ok"
    assert [hit["url"] for hit in result.hits] == ["https://github.com/lupantech/MathVista"]
    assert result.rejected_result_count == 1  # non-public URLs are filtered by the shared guard


def test_openai_fetch_reads_public_pages_and_rechecks_redirects(monkeypatch):
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://mathvista.github.io/"})
        if request.url.host == "mathvista.github.io":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<title>MathVista</title><a href='https://github.com/lupantech/MathVista'>Code</a>")
        return httpx.Response(301, headers={"location": "http://169.254.169.254/latest"})

    provider = _provider(monkeypatch, handler)
    page = provider.fetch("https://example.org/old")
    assert page.status == "ok" and page.title == "MathVista"
    assert "[Code](https://github.com/lupantech/MathVista)" in page.text
    blocked = provider.fetch("https://example.org/metadata")
    assert blocked.status == "access_denied"


def test_discovery_transport_follows_the_selected_model(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "TEST_ONLY")
    monkeypatch.setattr(settings, "deepseek_api_key", "TEST_ONLY")
    assert isinstance(discovery_provider_for(OpenAIClient(api_key="TEST_ONLY")), OpenAICodeDiscoveryProvider)
    monkeypatch.setattr(settings, "deepseek_enabled", False)

    class Other:
        pass

    assert discovery_provider_for(Other()) is None
    monkeypatch.setattr(settings, "deepseek_enabled", True)
    assert type(discovery_provider_for(Other())) is DeepSeekCodeDiscoveryProvider
    assert provider_module.OPENAI_PROVIDER_VERSION == "openai-code-discovery-1.0"


def test_deepseek_model_option_is_hidden_unless_enabled(monkeypatch):
    from app.api.routes_options import list_models

    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "TEST_ONLY")
    monkeypatch.setattr(settings, "openai_api_key", "TEST_ONLY")
    monkeypatch.setattr(settings, "deepseek_enabled", False)
    available = {m.model_id for m in list_models() if m.is_available}
    assert "model_standard_v1" not in available and "model_gpt_v1" in available
