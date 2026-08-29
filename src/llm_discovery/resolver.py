import re
from dataclasses import dataclass
from typing import Any

from .catalogs import ArtificialAnalysisCatalog


@dataclass(frozen=True)
class ModelResolution:
    provider_model_id: str
    aa_model: dict[str, Any] | None
    method: str


def _normalize(value: str) -> str:
    value = value.lower().strip()

    # Provider prefixes are not part of the model identity.
    value = value.rsplit("/", 1)[-1]

    # Common provider naming differences.
    value = value.replace(".", "-")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def resolve_model(
    provider_model_id: str,
    aa: ArtificialAnalysisCatalog,
) -> ModelResolution:
    # 1. Exact slug match after removing provider namespace.
    provider_slug = provider_model_id.rsplit("/", 1)[-1]

    exact = [model for model in aa.models if model.get("slug") == provider_slug]

    if len(exact) == 1:
        return ModelResolution(
            provider_model_id=provider_model_id,
            aa_model=exact[0],
            method="exact_slug",
        )

    # 2. Conservative normalized match.
    normalized = _normalize(provider_model_id)

    candidates = [model for model in aa.models if _normalize(model.get("slug", "")) == normalized]

    if len(candidates) == 1:
        return ModelResolution(
            provider_model_id=provider_model_id,
            aa_model=candidates[0],
            method="normalized_slug",
        )

    return ModelResolution(
        provider_model_id=provider_model_id,
        aa_model=None,
        method="unresolved",
    )
