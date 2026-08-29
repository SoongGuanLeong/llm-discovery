import json
from pathlib import Path
from typing import Any


class ArtificialAnalysisCatalog:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text())
        self.models: list[dict[str, Any]] = data["models"]

    def search(self, query: str) -> list[dict[str, Any]]:
        query = query.lower()

        models = [model for model in self.models if query in model["name"].lower() or query in model["slug"].lower()]

        return sorted(
            models,
            key=lambda model: model["evaluations"].get("artificial_analysis_intelligence_index") or 0,
            reverse=True,
        )

    def filter(self, min_score: float | None = None) -> list[dict[str, Any]]:
        models = self.models

        if min_score is not None:
            models = [
                model
                for model in models
                if (score := model.get("evaluations", {}).get("artificial_analysis_intelligence_index")) is not None
                and score >= min_score
            ]

        return sorted(
            models,
            key=lambda model: model["evaluations"].get("artificial_analysis_intelligence_index") or 0,
            reverse=True,
        )

    def get_by_id(self, model_id: str) -> dict[str, Any] | None:
        return next(
            (model for model in self.models if model["id"] == model_id),
            None,
        )


class ModelsDevCatalog:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text())
        self.models: dict[str, dict[str, Any]] = data["models"]
        self.providers: dict[str, dict[str, Any]] = data["providers"]

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        return self.models.get(model_id)

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self.providers.get(provider_id)

    def providers_for_model(self, model_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": provider_id,
                "name": provider["name"],
                "api": provider.get("api"),
            }
            for provider_id, provider in self.providers.items()
            if model_id in provider.get("models", {})
        ]

    def models_for_provider(self, provider_id: str) -> dict[str, dict[str, Any]]:
        provider = self.providers.get(provider_id)

        if provider is None:
            return {}

        return {model_id: self.models[model_id] for model_id in provider.get("models", {}) if model_id in self.models}
