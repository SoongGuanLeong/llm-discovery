"""Model resolution from provider model IDs to the Artificial Analysis catalog.

Resolution is deterministic and offline:

1. Exact slug match (after stripping the provider namespace, e.g. groq/).
2. Conservative normalized match (lowercase, dot/separator normalization).
3. Otherwise unresolved.

This module is intentionally pure - it performs no network or LLM calls and
is fully exercised by the offline T2 characterisation tests.
"""

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
    """Normalize a model ID or slug to a comparable canonical form.

    Strips the provider namespace (groq/mix -> mix), lowercases, and
    collapses separators (., _, spaces, dashes) into single dashes so that
    Meta.Llama-3.3 70B and Llama---3.3 both normalize to
    meta-llama-3-3 / llama-3-3.
    """
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
    models_dev: Any = None,
    benchmark_cache: Any = None,
) -> ModelResolution:
    """Resolve a provider model ID to an AA catalog entry.

    Returns a ModelResolution where aa_model is the matched catalog entry (or
    None) and method is one of 'exact_slug', 'normalized_slug', 'unresolved'.

    models_dev and benchmark_cache are accepted (and currently ignored) so the
    committed evaluate_model call site resolve_model(model_id, aa, models_dev,
    cache) stays compatible. The deterministic slug/normalized match is
    sufficient and reproducible.
    """
    # 1. Exact slug match after removing the provider namespace.
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

    candidates = [
        model
        for model in aa.models
        if _normalize(model.get("slug", "")) == normalized
    ]

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
