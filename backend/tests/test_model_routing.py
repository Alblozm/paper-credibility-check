from app.llm.deepseek import DeepSeekClient
from app.llm.fixture import FixtureLLMClient
from app.llm.openai import OpenAIClient
from app.pipeline.orchestrator import build_llm_client


def test_build_llm_client_routes_the_requested_model(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "deepseek-key")
    monkeypatch.setattr(settings, "openai_api_key", "openai-key")

    assert isinstance(build_llm_client("model_standard_v1"), DeepSeekClient)
    assert isinstance(build_llm_client("model_gpt_v1"), OpenAIClient)
    assert isinstance(build_llm_client("model_fixture_v1"), FixtureLLMClient)
