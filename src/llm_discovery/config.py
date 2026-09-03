from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

CONFIG_PATH = Path("config/providers.yaml")

_ALLOWED_DISCOVERY_STRATEGIES = {None, "bazaarlink", "nararouter"}


class ProviderConfig(BaseModel):
    name: str
    base_url: str | None = None
    secret: str
    discovery: str = "openai"
    discovery_strategy: str | None = None
    # Optional variant for NaraRouter: if true, paid-gated-free ids are
    # returned as dropped with reason "paid_gated_free" instead of excluded.
    include_paid_gated_as_dropped: bool = False

    @field_validator("discovery_strategy")
    @classmethod
    def _validate_strategy(cls, v: str | None) -> str | None:
        if v not in _ALLOWED_DISCOVERY_STRATEGIES:
            raise ValueError(
                f"discovery_strategy must be one of {sorted(s for s in _ALLOWED_DISCOVERY_STRATEGIES if s is not None)} or omitted, got {v!r}"
            )
        return v


class ArtificialAnalysisConfig(BaseModel):
    min_score: float = 24
    max_score: float = 45


class InfisicalConfig(BaseModel):
    environment: str = "dev"
    shared_project_id_env: str = "LLM_SHARED_PROJECT_ID"
    discovery_project_id_env: str = "LLM_DISCOVERY_PROJECT_ID"


class JudgeLLMConfig(BaseModel):
    base_url: str
    model: str
    secret: str


class AppConfig(BaseModel):
    artificial_analysis: ArtificialAnalysisConfig
    infisical: InfisicalConfig
    judge_llm: JudgeLLMConfig
    providers: list[ProviderConfig]


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    data = yaml.safe_load(path.read_text())
    return AppConfig.model_validate(data)
