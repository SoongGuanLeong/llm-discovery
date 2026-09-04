"""Vision-capable coding exception — conditional deterministic vision drop (ADR 0003)."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm_discovery.benchmarks import BenchmarkDataCache
import llm_discovery.pipeline as pip
from llm_discovery.pipeline import (
    VISION_AA_CODING_MIN,
    VISION_AA_INTEL_MIN,
    VISION_BENCH_MIN,
    VISION_CHEAP_THRESHOLD,
    VISION_CODING_SCORE_MIN,
    _is_coding_capable,
    _is_cheap_or_free,
    _is_vision_free_model,
    _is_vision_only,
    evaluate_model,
)


class _FakeModelsDev:
    def __init__(self, mapping):
        self.models = mapping

    def get_model(self, mid):
        for k, v in self.models.items():
            if mid == k or mid.endswith(k) or k in mid:
                return v
        return None


class _FakeEval:
    def evaluate(self, request, evidence_packet=None):  # noqa: ARG002
        from llm_discovery.evaluation import ModelEvaluation

        return ModelEvaluation(
            canonical_name="test",
            coding=True,
            aa_relevance="strong",
            confidence=0.9,
            decision="keep",
            evidence_level="strong",
            evidence=[],
            coding_assessment=None,
        )


def _res(pricing, evals):
    return SimpleNamespace(aa_model={"id": "test", "name": "test", "slug": "test", "evaluations": evals, "pricing": pricing})


def test_vision_only_predicate():
    assert _is_vision_only(["specialized_model:vision"]) is True
    assert _is_vision_only(["specialized_model:vision", "specialized_model:vision"]) is True
    assert _is_vision_only(["specialized_model:embedding"]) is False
    assert _is_vision_only(["specialized_model:vision", "specialized_model:embedding"]) is False
    assert _is_vision_only([]) is False


def test_threshold_constants():
    assert VISION_AA_CODING_MIN == 45.0
    assert VISION_AA_INTEL_MIN == 55.0
    assert VISION_CODING_SCORE_MIN == 35.0
    assert VISION_BENCH_MIN == 50.0
    assert VISION_CHEAP_THRESHOLD == 1.2


def test_cheap_or_free_threshold():
    assert _is_cheap_or_free(_res({"price_1m_blended_3_to_1": 0.23}, {}), "m", None) is True
    assert _is_cheap_or_free(_res({"price_1m_blended_3_to_1": 1.13}, {}), "m", None) is True
    assert _is_cheap_or_free(_res({"price_1m_blended_3_to_1": 1.2}, {}), "m", None) is True
    assert _is_cheap_or_free(_res({"price_1m_blended_3_to_1": 1.21}, {}), "m", None) is False


def test_free_via_model_id_and_pricing_zero():
    assert _is_vision_free_model("nararouter/qwen3.8-27b-free", SimpleNamespace(aa_model=None), None) is True
    assert _is_vision_free_model("openrouter/free", SimpleNamespace(aa_model=None), None) is True
    assert _is_vision_free_model(
        "m", _res({"price_1m_blended_3_to_1": 0, "price_1m_input_tokens": 0, "price_1m_output_tokens": 0}, {}), None
    ) is True
    # null AA not free -> not cheap
    assert _is_cheap_or_free(SimpleNamespace(aa_model=None), "Qwen/Qwen3.8-27B", None) is False


def test_coding_capable_aa_thresholds():
    assert _is_coding_capable(_res({}, {"artificial_analysis_coding_index": 68.1}), None, "m", "p") is True
    assert _is_coding_capable(_res({}, {"artificial_analysis_coding_index": 45.0}), None, "m", "p") is True
    assert _is_coding_capable(_res({}, {"artificial_analysis_coding_index": 44.9}), None, "m", "p") is False
    assert _is_coding_capable(_res({}, {"artificial_analysis_intelligence_index": 55.8}), None, "m", "p") is True
    assert _is_coding_capable(_res({}, {"artificial_analysis_intelligence_index": 54.9}), None, "m", "p") is False


def test_coding_capable_via_cache_swe():
    cache = BenchmarkDataCache(cache_path=Path("/tmp/test_vision_bm.json"))
    cache._data = {
        "m1": {"benchmarks": {"swe_bench_verified": {"score": 61.7}}, "raw_benchmarks": []},
        "m2": {"benchmarks": {"aa_intelligence": {"score": 14.4}}, "raw_benchmarks": []},
    }
    cache._loaded = True
    assert _is_coding_capable(SimpleNamespace(aa_model=None), cache, "m1", "p") is True
    assert _is_coding_capable(SimpleNamespace(aa_model=None), cache, "m2", "p") is False


def test_evaluate_vision_coding_cheap_bypasses_drop():
    with patch.object(pip, "resolve_model", lambda *a, **k: _res({"price_1m_blended_3_to_1": 0.8}, {"artificial_analysis_coding_index": 68.1})):
        md = _FakeModelsDev({"qwen3.8-27b": {"id": "qwen3.8-27b", "name": "Qwen", "description": "vision-language model for coding"}})
        rec = evaluate_model({"id": "Qwen/Qwen3.8-27B"}, "modelscope", None, md, _FakeEval(), 24.0, 45.0, cache=None)
        assert rec["decision"] == "keep"
        assert rec["source"] == "llm"


def test_evaluate_vision_coding_expensive_stays_dropped():
    with patch.object(pip, "resolve_model", lambda *a, **k: _res({"price_1m_blended_3_to_1": 5.0}, {"artificial_analysis_coding_index": 68.1})):
        md = _FakeModelsDev({"qwen3.8-27b": {"id": "qwen3.8-27b", "name": "Qwen", "description": "vision-language model for coding"}})
        rec = evaluate_model({"id": "Qwen/Qwen3.8-27B"}, "modelscope", None, md, _FakeEval(), 24.0, 45.0, cache=None)
        assert rec["decision"] == "drop"
        assert rec["source"] == "deterministic"


def test_evaluate_vision_not_coding_cheap_stays_dropped():
    with patch.object(pip, "resolve_model", lambda *a, **k: _res({"price_1m_blended_3_to_1": 0.7}, {"artificial_analysis_coding_index": 20, "artificial_analysis_intelligence_index": 14})):
        md = _FakeModelsDev({"qwen3-vl": {"id": "qwen3-vl", "name": "Qwen", "description": "vision-language instruct model"}})
        rec = evaluate_model({"id": "Qwen/Qwen3-VL-235B-A22B-Instruct"}, "modelscope", None, md, _FakeEval(), 24.0, 45.0, cache=None)
        assert rec["decision"] == "drop"


def test_evaluate_embedding_stays_dropped_even_if_coding_cheap():
    with patch.object(pip, "resolve_model", lambda *a, **k: _res({"price_1m_blended_3_to_1": 0.3}, {"artificial_analysis_coding_index": 70})):
        rec = evaluate_model({"id": "Qwen/Qwen3-Embedding-8B"}, "modelscope", None, _FakeModelsDev({}), _FakeEval(), 24.0, 45.0, cache=None)
        assert rec["decision"] == "drop"


def test_evaluate_null_pricing_free_id_with_swe_bypasses():
    cache = BenchmarkDataCache(cache_path=Path("/tmp/test_vision_free.json"))
    cache._data = {"my-model-free": {"benchmarks": {"swe_bench_verified": {"score": 61.7}}, "raw_benchmarks": []}}
    cache._loaded = True
    with patch.object(pip, "resolve_model", lambda *a, **k: SimpleNamespace(aa_model=None)):
        md = _FakeModelsDev({"my-model-free": {"id": "my-model-free", "name": "my", "description": "vision-language model for coding"}})
        rec = evaluate_model({"id": "my-model-free"}, "prov", None, md, _FakeEval(), 24.0, 45.0, cache=cache)
        assert rec["decision"] == "keep"


def test_evaluate_null_pricing_not_free_stays_dropped():
    cache = BenchmarkDataCache(cache_path=Path("/tmp/test_vision_null.json"))
    cache._data = {"Qwen/Qwen3.8-27B": {"benchmarks": {"swe_bench_verified": {"score": 61.7}}, "raw_benchmarks": []}}
    cache._loaded = True
    with patch.object(pip, "resolve_model", lambda *a, **k: SimpleNamespace(aa_model=None)):
        md = _FakeModelsDev({"qwen3.8-27b": {"id": "qwen3.8-27b", "name": "Qwen", "description": "vision-language model for coding"}})
        rec = evaluate_model({"id": "Qwen/Qwen3.8-27B"}, "modelscope", None, md, _FakeEval(), 24.0, 45.0, cache=cache)
        assert rec["decision"] == "drop"
