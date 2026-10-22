"""集中配置。默认走轻量栈：SQLite + FastAPI 内置后台任务 + fixture 模型。

升级到完整版只改 .env，不改代码。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.validation.degradation import DegradationPolicy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./data/pcc.db"
    task_runner: str = "inline"          # inline | celery
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "fixture"        # default model: fixture | deepseek | openai
    # DeepSeek is offered as an option for internal testing; GPT stays the default (LLM_PROVIDER=openai).
    deepseek_enabled: bool = True
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_thinking_enabled: bool = True
    deepseek_reasoning_effort: str = "medium"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: str = "medium"
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2

    # Optional GitHub rate-limit credentials; never serialize into public settings or fingerprints.
    github_token: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)

    # Provider-wide state must be shared by every worker, independently of task databases.
    # Contact identification is sent only in User-Agent, never reports or fingerprints.
    crossref_mailto: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    crossref_state_path: str = str(Path(__file__).resolve().parents[1] / "data" / "crossref" / "state.sqlite3")
    crossref_min_interval_seconds: float = Field(default=1.0, ge=0, le=60)
    crossref_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    crossref_cache_ttl_seconds: float = Field(default=604800.0, gt=0)
    crossref_branch_deadline_seconds: float = Field(default=600.0, gt=0, le=3600)
    reference_multisource_enabled: bool = True
    reference_fallback_deadline_seconds: float = Field(default=600.0, gt=0, le=3600)

    # 可信度核查读哪份论文全文：live＝后端自行解析（默认，与五维一致，适用于任意输入论文）；
    # benchmark＝优先读 benchmark 数据 data/data/parsed，仅用于回归测试与历史验收重放。
    credibility_parse_source: str = "live"  # live | benchmark
    credibility_workers: int = Field(default=3, ge=1, le=3)
    credibility_lease_seconds: int = Field(default=1800, ge=1)
    # Live-parsed papers pass the same English ML gate as the five-dimension review.
    credibility_eligibility_gate_enabled: bool = True
    # Release claims are labelled by the selected model before code reconciliation.
    credibility_release_classifier_enabled: bool = True
    # Printed paper URLs are labelled by the selected model (own code/project page or third party).
    credibility_paper_link_classifier_enabled: bool = True
    # Raw reference entries are split into fields by the selected model, grounded in the printed text.
    credibility_reference_field_parser_enabled: bool = True
    # References still unresolved after the deterministic pipeline go to a bounded tool-using agent;
    # the program accepts a proposed source only if its text contains the cited title and an author.
    credibility_reference_agent_enabled: bool = True
    credibility_code_model_enabled: bool = True
    credibility_code_tool_mode: str = "prefetch"
    credibility_code_max_rounds: int = Field(default=3, ge=1, le=6)
    credibility_code_max_tool_calls: int = Field(default=6, ge=1, le=20)
    credibility_code_max_characters: int = Field(default=60000, ge=1000, le=150000)
    credibility_code_max_output_tokens: int = Field(default=16000, ge=1000, le=16000)
    credibility_code_discovery_enabled: bool = True
    credibility_code_discovery_max_queries: int = Field(default=3, ge=1, le=3)
    credibility_code_discovery_max_fetches: int = Field(default=4, ge=1, le=4)
    credibility_code_discovery_max_rounds: int = Field(default=4, ge=2, le=4)
    credibility_code_discovery_max_tool_calls: int = Field(default=8, ge=1, le=8)

    parser_provider: str = "stub"        # stub | benchmark | generic | grobid
    grobid_url: str = "http://localhost:8070"
    arxiv_cache_dir: str = "./data/arxiv"
    arxiv_timeout_seconds: float = 45.0
    benchmark_parsed_dir: str = "../data/data/parsed/paper_001/v3"
    benchmark_source_record: str = "../data/data/source_snapshots/paper_001/source_record.json"
    benchmark_arxiv_id: str = "2307.08122v3"

    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 30
    max_page_count: int = 120

    evidence_degradation_policy: DegradationPolicy = DegradationPolicy.TO_UNCERTAIN

    # 必须与 data/rubrics/v0.1/rubric.yaml 完全一致：
    # 那边的 rubric_versions 表拿 (rubric_id, rubric_version) 做外键，差一个后缀就插不进去。
    rubric_id: str = "iclr_inspired_due_diligence"
    rubric_version: str = "0.1-draft"
    rubric_dir: str = str(Path(__file__).resolve().parent / "rubrics" / "v0.1")


@lru_cache
def get_settings() -> Settings:
    return Settings()
