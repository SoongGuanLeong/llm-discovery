from pathlib import Path

import yaml
from pydantic import BaseModel

CONFIG_PATH = Path("config/providers.yaml")


class ProviderConfig(BaseModel):
    name: str
    base_url: str | None = None
    secret: str


class ArtificialAnalysisConfig(BaseModel):
    min_score: float = 25


class InfisicalConfig(BaseModel):
    environment: str = "dev"
    shared_project_id_env: str = "INFISICAL_SHARED_PROJECT_ID"
    discovery_project_id_env: str = "INFISICAL_DISCOVERY_PROJECT_ID"


class LocalLLMConfig(BaseModel):
    base_url: str
    model: str
    secret: str


class AppConfig(BaseModel):
    artificial_analysis: ArtificialAnalysisConfig
    infisical: InfisicalConfig
    local_llm: LocalLLMConfig
    providers: list[ProviderConfig]


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    data = yaml.safe_load(path.read_text())
    return AppConfig.model_validate(data)
