"""Issue #290 -- one persistence seam in EvaluatorCoordinator.

Phase 3 of #285.  The seam under test is ``evaluate()``.  Persistence is
observed through a fake store adapter passed into the constructor, never by
patching a module path or a class attribute (Phase 3 testing decisions).

Behaviour contract these tests pin:

* every store write goes through the one seam, so the fake adapter records
  exactly one write per persisted record on the router, deterministic-strong
  and judge paths -- including paths that ``evaluate()`` returns early from,
  which proves the write is not deferred to an end-of-function call;
* the two reuse paths (strong cache hit, candidate cache hit) return the
  cached record without writing again -- re-persisting would reset the TTL;
* the ``llm7`` turbo -> flash override keeps its exact per-path semantics;
* a store failure never changes the verdict.
"""
from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from llm_discovery.benchmarks import BenchmarkDataCache
from llm_discovery.candidate_store import CandidateStore
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.evaluation import ModelEvaluation
from llm_discovery.evaluator import EvaluatorCoordinator, resolve_cache_identity
from llm_discovery.model_info_store import (
    BenchmarkSnapshot,
    JudgeSnapshot,
    ModelInfoRecord,
    PricingSnapshot,
    StoreMeta,
    normalize_store_key,
)


class RecordingStore:
    """Keeper-store adapter that records every write (issue #290 seam)."""

    def __init__(self, seed: dict | None = None) -> None:
        self._data = dict(seed or {})
        self.writes: list[tuple[str, object]] = []

    def get(self, key):
        return self._data.get(key)

    def put(self, key, record):
        self.writes.append((key, record))
        self._data[key] = record

    def __contains__(self, key):
        return key in self._data

    def delete(self, key):
        return self._data.pop(key, None) is not None

    def _ensure_loaded(self):
        return None


class ExplodingStore(RecordingStore):
    """Store whose writes always fail -- persistence must stay best-effort."""

    def put(self, key, record):
        raise RuntimeError("store unavailable")


class StaticJudge:
    """Evaluator double returning a fixed ModelEvaluation."""

    def __init__(self, result: ModelEvaluation) -> None:
        self.result = result
        self.calls = 0

    def evaluate(self, request, packet=None):
        self.calls += 1
        return self.result


def _aa(tmp_path, models):
    path = Path(tmp_path) / "aa.json"
    path.write_text(json.dumps({"source": "test", "models": models}))
    return ArtificialAnalysisCatalog(path)


def _md(tmp_path):
    path = Path(tmp_path) / "md.json"
    path.write_text(json.dumps({"models": {}, "providers": {}}))
    return ModelsDevCatalog(path)


def _cache(**data):
    cache = BenchmarkDataCache()
    cache._loaded = True
    cache._data.update(data)
    return cache


def _coord(tmp_path, aa, cache, store, *, evaluator=None, candidate_store=None, provider="test"):
    return EvaluatorCoordinator(
        provider_name=provider,
        aa=aa,
        models_dev=_md(tmp_path),
        evaluator=evaluator,
        min_score=24,
        max_score=45,
        cache=cache,
        store=store,
        candidate_store=candidate_store,
    )


def _strong_judge_result():
    return ModelEvaluation(
        coding=True,
        decision="keep",
        confidence=0.9,
        evidence_level="strong",
        evidence=["http://example.com/llm"],
    )


def _seeded_strong_record(decision="keep"):
    now = datetime.now(UTC).isoformat()
    return ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(
            scores={"aa_intelligence": {"score": 60, "source": "https://example.com"}},
            raw_benchmarks=[],
            benchmark_coverage=0.5,
        ),
        pricing=PricingSnapshot(blended=0.5, input=0.3, output=1.0),
        _meta=StoreMeta(first_seen=now, last_updated=now, version=2),
        judge=JudgeSnapshot(
            evidence_level="strong",
            evidence=["https://example.com"],
            confidence=0.9,
            coding=True,
            canonical_name="Cached Model",
            tier="flash",
            decision=decision,
            judge_model="judge-x",
        ),
    )


# --------------------------------------------------------------------------
# One write per persisted record, on every generating path
# --------------------------------------------------------------------------


def test_router_path_persists_exactly_once(tmp_path):
    store = RecordingStore()
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), store)

    rec = coord.evaluate({"id": "my-router-model"})

    assert rec["decision"] == "keep"
    # The router path returns early; a deferred write would never run here.
    assert len(store.writes) == 1
    key, written = store.writes[0]
    assert key == resolve_cache_identity("my-router-model", None)
    assert isinstance(written, ModelInfoRecord)


def test_deterministic_strong_path_persists_exactly_once(tmp_path):
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-strong",
                "name": "Strong",
                "slug": "strong-model",
                "evaluations": {"artificial_analysis_intelligence_index": 60},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        ],
    )
    cache = _cache(
        **{"strong-model": {"benchmarks": {"swe_bench_verified": {"score": 60, "source": "https://example.com"}}}}
    )
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store)

    rec = coord.evaluate({"id": "strong-model"})

    assert rec["evidence_level"] == "strong"
    assert rec["decision"] == "keep"
    assert len(store.writes) == 1
    assert store.writes[0][0] == normalize_store_key("strong-model")


def test_deterministic_strong_drop_path_persists_exactly_once(tmp_path):
    # AA 60 gives strong evidence, but SWE-bench 10 is a critical weakness ->
    # decision drop, which must still be persisted exactly once.
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-weak",
                "name": "Weak",
                "slug": "weak-strong-model",
                "evaluations": {"artificial_analysis_intelligence_index": 60},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        ],
    )
    cache = _cache(
        **{"weak-strong-model": {"benchmarks": {"swe_bench_verified": {"score": 10, "source": "https://example.com"}}}}
    )
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store)

    rec = coord.evaluate({"id": "weak-strong-model"})

    assert rec["decision"] == "drop"
    assert len(store.writes) == 1


def test_judge_path_persists_exactly_once(tmp_path):
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-mid",
                "name": "Mid",
                "slug": "mid-model",
                "evaluations": {"artificial_analysis_intelligence_index": 30},
                "pricing": {
                    "price_1m_blended_3_to_1": 0.5,
                    "price_1m_input_tokens": 0.3,
                    "price_1m_output_tokens": 1.0,
                },
            }
        ],
    )
    cache = _cache(
        **{"mid-model": {"benchmarks": {"swe_bench_verified": {"score": 35, "source": "https://example.com"}}}}
    )
    judge = StaticJudge(_strong_judge_result())
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store, evaluator=judge)

    rec = coord.evaluate({"id": "mid-model"})

    assert judge.calls == 1
    assert rec["decision"] == "keep"
    assert len(store.writes) == 1


# --------------------------------------------------------------------------
# Reuse paths must not write again (that would reset the TTL)
# --------------------------------------------------------------------------


def test_strong_cache_hit_reuses_without_writing(tmp_path):
    model_id = "cached-strong-model"
    store = RecordingStore(seed={normalize_store_key(model_id): _seeded_strong_record()})
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), store)

    rec = coord.evaluate({"id": model_id})

    assert rec["cached"] is True
    assert rec["source"] == "cache"
    assert store.writes == []


def test_candidate_cache_hit_reuses_without_keeper_write(tmp_path):
    cstore = CandidateStore(Path(tmp_path) / "candidates.json")
    store = RecordingStore()
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), store, candidate_store=cstore)

    first = coord.evaluate({"id": "weak-model-xyz"})

    assert first["evidence_level"] == "weak"
    assert store.writes == []  # weak never enters the Keeper store
    assert cstore.size() == 1

    coord2 = _coord(tmp_path, _aa(tmp_path, []), _cache(), store, candidate_store=cstore)
    second = coord2.evaluate({"id": "weak-model-xyz"})

    assert second["source"] == "candidate_cache"
    assert store.writes == []


def test_repeated_evaluation_writes_each_record_exactly_once(tmp_path):
    """One write per returned record: re-evaluating a persisted record reuses
    it without a second write (a duplicate would reset the TTL)."""
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-strong",
                "name": "Strong",
                "slug": "strong-model",
                "evaluations": {"artificial_analysis_intelligence_index": 60},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        ],
    )
    cache = _cache(
        **{"strong-model": {"benchmarks": {"swe_bench_verified": {"score": 60, "source": "https://example.com"}}}}
    )
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store)

    coord.evaluate({"id": "strong-model"})
    assert len(store.writes) == 1

    coord2 = _coord(tmp_path, aa, cache, store)
    second = coord2.evaluate({"id": "strong-model"})

    assert second["cached"] is True
    assert len(store.writes) == 1


# --------------------------------------------------------------------------
# llm7 turbo -> flash override: one helper, per-path semantics preserved
# --------------------------------------------------------------------------


def test_llm7_turbo_deterministic_strong_is_flash(tmp_path):
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-llm7",
                "name": "LLM7",
                "slug": "llm7-model",
                "evaluations": {"artificial_analysis_intelligence_index": 60},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        ],
    )
    cache = _cache(
        **{"llm7-model": {"benchmarks": {"swe_bench_verified": {"score": 60, "source": "https://example.com"}}}}
    )
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store, provider="llm7")

    rec = coord.evaluate({"id": "llm7-model", "tier": "turbo"})

    assert rec["tier"] == "flash"
    assert rec["decision"] == "keep"


def test_llm7_turbo_cache_hit_is_flash(tmp_path):
    model_id = "llm7-cached-model"
    store = RecordingStore(seed={normalize_store_key(model_id): _seeded_strong_record()})
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), store, provider="llm7")

    rec = coord.evaluate({"id": model_id, "tier": "turbo"})

    assert rec["cached"] is True
    assert rec["tier"] == "flash"
    assert rec["decision"] == "keep"


def test_llm7_turbo_does_not_override_cached_drop(tmp_path):
    # A cached drop verdict must survive the tier override untouched.
    model_id = "llm7-dropped-model"
    store = RecordingStore(seed={normalize_store_key(model_id): _seeded_strong_record(decision="drop")})
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), store, provider="llm7")

    rec = coord.evaluate({"id": model_id, "tier": "turbo"})

    assert rec["decision"] == "drop"
    assert rec["tier"] == "drop"


def test_llm7_turbo_judge_path_is_flash(tmp_path):
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-mid",
                "name": "Mid",
                "slug": "mid-model",
                "evaluations": {"artificial_analysis_intelligence_index": 30},
                "pricing": {
                    "price_1m_blended_3_to_1": 0.5,
                    "price_1m_input_tokens": 0.3,
                    "price_1m_output_tokens": 1.0,
                },
            }
        ],
    )
    cache = _cache(
        **{"mid-model": {"benchmarks": {"swe_bench_verified": {"score": 35, "source": "https://example.com"}}}}
    )
    judge = StaticJudge(_strong_judge_result())
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store, evaluator=judge, provider="llm7")

    rec = coord.evaluate({"id": "mid-model", "tier": "turbo"})

    assert rec["tier"] == "flash"
    assert rec["decision"] == "keep"


def test_non_llm7_turbo_is_not_overridden(tmp_path):
    aa = _aa(
        tmp_path,
        [
            {
                "id": "aa-strong",
                "name": "Strong",
                "slug": "strong-model",
                "evaluations": {"artificial_analysis_intelligence_index": 60},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        ],
    )
    cache = _cache(
        **{"strong-model": {"benchmarks": {"swe_bench_verified": {"score": 60, "source": "https://example.com"}}}}
    )
    store = RecordingStore()
    coord = _coord(tmp_path, aa, cache, store, provider="other-provider")

    rec = coord.evaluate({"id": "strong-model", "tier": "turbo"})

    assert rec["tier"] != "flash"


# --------------------------------------------------------------------------
# Persistence is best-effort; no hidden state on the instance
# --------------------------------------------------------------------------


def test_store_failure_does_not_change_verdict(tmp_path):
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), ExplodingStore())

    rec = coord.evaluate({"id": "my-router-model"})

    assert rec["decision"] == "keep"


def test_evaluate_leaves_no_recovery_state_on_instance(tmp_path):
    coord = _coord(tmp_path, _aa(tmp_path, []), _cache(), RecordingStore())

    coord.evaluate({"id": "weak-model-xyz"})

    assert "_pending_recovery_attempts" not in vars(coord)
