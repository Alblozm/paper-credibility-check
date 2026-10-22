from __future__ import annotations

import copy

import httpx
import pytest

from app.config import get_settings
from app.llm.fixture import load_example_report


@pytest.fixture(autouse=True)
def deterministic_test_providers(monkeypatch, tmp_path):
    """自动测试不读取开发者本机的真实 provider 配置或消耗 API 额度。"""
    def forbidden_network(_transport, request):
        raise httpx.ConnectError("Real network is disabled in automated tests.", request=request)

    async def forbidden_async_network(_transport, request):
        raise httpx.ConnectError("Real network is disabled in automated tests.", request=request)

    # MockTransport and ASGI test transports remain available; actual sockets do not.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async_network)
    settings = get_settings()
    monkeypatch.setattr(settings, "parser_provider", "stub")
    monkeypatch.setattr(settings, "llm_provider", "fixture")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    # Existing transport tests still exercise the DeepSeek client; production hides it by default.
    monkeypatch.setattr(settings, "deepseek_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "credibility_workers", 1)
    # Existing credibility fixtures place parsed papers under a temporary repo_root;
    # tests of the production default (backend live parsing) opt out explicitly.
    monkeypatch.setattr(settings, "credibility_parse_source", "benchmark")
    monkeypatch.setattr(settings, "credibility_code_model_enabled", False)
    # Live-parse fixtures inject claim clients only; gate tests opt back in explicitly.
    monkeypatch.setattr(settings, "credibility_eligibility_gate_enabled", False)
    monkeypatch.setattr(settings, "credibility_release_classifier_enabled", False)
    monkeypatch.setattr(settings, "credibility_paper_link_classifier_enabled", False)
    monkeypatch.setattr(settings, "credibility_reference_field_parser_enabled", False)
    monkeypatch.setattr(settings, "credibility_reference_agent_enabled", False)
    # Baseline unit fixtures isolate their original provider; multisource
    # integration tests explicitly enable and inject every external transport.
    monkeypatch.setattr(settings, "reference_multisource_enabled", False)
    monkeypatch.setattr(settings, "crossref_state_path", str(tmp_path / "crossref.sqlite3"))
    monkeypatch.setattr(settings, "crossref_min_interval_seconds", 0)
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "crossref_mailto", SecretStr(""))
    monkeypatch.setenv("CREDIBILITY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("CREDIBILITY_CROSSREF_WAIT_BUDGET_SECONDS", "0")
    from app.credibility.references import _LOOKUP

    monkeypatch.setattr(_LOOKUP, "retry_wait_budget_seconds", 0)
    # Credibility tests also use isolated durable storage, never the local archive.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.credibility import runtime
    from app.storage.credibility_tasks import CredibilityTaskStore

    engine = create_engine(f"sqlite:///{tmp_path / 'credibility.db'}", connect_args={"check_same_thread": False})
    store = CredibilityTaskStore(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(runtime, "get_task_store", lambda: store)
    yield_store = engine
    # Tests should not add fixture reviews to the developer's local archive.
    from app.api import routes_assessments

    monkeypatch.setattr(routes_assessments, "save_assessment", lambda snapshot: None)
    yield
    yield_store.dispose()


@pytest.fixture
def example_report() -> dict:
    """契约 §19 的示例报告，每个测试拿到独立副本。"""
    return copy.deepcopy(load_example_report())
