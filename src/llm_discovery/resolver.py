"""Re-export shim for model resolution (T6 collapse).

Canonical implementation lives in llm_discovery.model_matching.ModelMatcher.
This module is retained for import compatibility: `from .resolver import
resolve_model, ModelResolution, _normalize` continues to work.

The extraction loop now lives in exactly one place (ModelMatcher.match),
which already populates ModelResolution.aa_model so callers need no second
lookup. See pipeline.py for the eliminated re-derivation loop.
"""

from typing import Any

from .model_matching import ModelResolution, ModelMatcher, _normalize  # noqa: F401  re-export

__all__ = ["ModelResolution", "_normalize", "resolve_model"]


def resolve_model(
    provider_model_id: str,
    aa: Any,
    models_dev: Any = None,
    benchmark_cache: Any = None,
) -> ModelResolution:
    """Resolve provider model ID to AA catalog entry via ModelMatcher."""
    matcher = ModelMatcher(aa_catalog=aa, models_dev_catalog=models_dev, benchmark_cache=benchmark_cache)
    return matcher.match(provider_model_id)
