"""Judge seam — LLM evaluation adapter.

Wraps LocalLLMEvaluator (or any fake with evaluate(request, packet) -> ModelEvaluation)
behind Judge.evaluate() so pipeline is a thin coordinator:

    judge = Judge(evaluator)
    llm_result = judge.evaluate(provider_name, model, packet, cache)

Request building (ModelEvaluationRequest) lives here, not in pipeline.
"""
from typing import Any

from .benchmarks import build_benchmark_profile
from .evaluation import ModelEvaluation, ModelEvaluationRequest


class Judge:
    """Thin adapter: builds ModelEvaluationRequest and delegates to evaluator."""

    def __init__(self, evaluator: Any):
        self.evaluator = evaluator

    def evaluate(
        self,
        provider_name: str,
        model: dict[str, Any],
        packet: Any,
        cache: Any = None,
        profile: Any = None,
    ) -> ModelEvaluation:
        """Build request from packet + cache, delegate to underlying evaluator.

        profile is optional — when provided by coordinator (dedup), reuse instead
        of rebuilding. Stored as _last_profile for pipeline to forward to PolicyGate.
        """
        model_id = model["id"]
        # Benchmarks for LLM context — reuse coordinator profile when available
        if profile is None:
            profile = build_benchmark_profile(model_id, provider_name, cache)
        benchmarks_dict = profile.to_dict() if profile.scores else {}
        self._last_profile = profile
        self._last_benchmarks = benchmarks_dict
        # AA match comes from evidence_packet (already resolved deterministically)
        aa_match = packet.aa_match if packet and packet.aa_match is not None else {"matched": False, "model_id": None, "score": None}
        request = ModelEvaluationRequest(
            provider=provider_name,
            model_id=model_id,
            provider_metadata=model,
            aa_match=aa_match,
            benchmarks=benchmarks_dict,
        )
        return self.evaluator.evaluate(request, packet)
