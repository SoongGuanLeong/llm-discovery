"""Model resolution seam — thin coordinator adapter.

Canonical implementation lives in model_matching.ModelMatcher. This module
provides ModelResolver as the seam the pipeline coordinator calls:
    ModelResolver(aa, models_dev, cache).resolve(model_id) -> ModelResolution

Preserves resolver.py import compatibility via re-export.
"""
from typing import Any

from .model_matching import ModelMatcher, ModelResolution, _normalize, normalize_model_id

__all__ = ["ModelResolver", "ModelResolution", "resolve_model", "_normalize", "normalize_model_id"]


class ModelResolver:
    """Thin adapter for deterministic AA model resolution."""

    def __init__(self, aa: Any = None, models_dev: Any = None, cache: Any = None):
        self.aa = aa
        self.models_dev = models_dev
        self.cache = cache
        self._matcher = ModelMatcher(
            aa_catalog=aa, models_dev_catalog=models_dev, benchmark_cache=cache
        )

    def resolve(self, provider_model_id: str) -> ModelResolution:
        """Resolve provider model ID to AA catalog entry."""
        return self._matcher.match(provider_model_id)


def resolve_model(
    provider_model_id: str,
    aa: Any,
    models_dev: Any = None,
    benchmark_cache: Any = None,
) -> ModelResolution:
    """Module-level helper for import compatibility."""
    return ModelResolver(aa, models_dev, benchmark_cache).resolve(provider_model_id)
