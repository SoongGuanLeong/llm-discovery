"""Judge seam — LLM evaluation adapter.

Wraps LocalLLMEvaluator (or any fake with evaluate(request, packet) -> ModelEvaluation)
behind Judge.evaluate() so pipeline is a thin coordinator:

    judge = Judge(evaluator)
    llm_result = judge.evaluate(provider_name, model, packet, cache)

Request building (ModelEvaluationRequest) lives here, not in pipeline.
"""
from typing import Any

from .benchmarks import BenchmarkDataCache, build_benchmark_profile
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
    ) -> ModelEvaluation:
        """Build request from packet + cache, delegate to underlying evaluator."""
        model_id = model["id"]
        # Benchmarks for LLM context — same as pipeline used (profile.to_dict if scores else {})
        profile = build_benchmark_profile(model_id, provider_name, cache)
        benchmarks_dict = profile.to_dict() if profile.scores else {}
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

    # Backward compat: expose direct delegate for tests that call evaluator.evaluate
    def _post(self, *a, **kw):
        return self.evaluator._post(*a, **kw) if hasattr(self.evaluator, "_post") else None
