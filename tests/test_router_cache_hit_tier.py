"""Regression: router ids must stay keep+flash on the cache-hit reuse path.

`PolicyGate.apply` (post-LLM), `_deterministic_router_record` (pre-LLM) and the
write-time gate all force router ids to keep/flash. The strong-cache-hit path
(`EvaluatorCoordinator.evaluate` -> `_build_cached_strong_record`) bypassed that
override: it re-derived tier with `categorize_model`, which returns `uncertain`
when both aa_score and coding_score are None. The 2026-09-18 cline run shows the
symptom — `openrouter/free` in `keep` with `tier: uncertain`.
"""
from datetime import UTC, datetime

import pytest

from llm_discovery.evaluator import EvaluatorCoordinator
from llm_discovery.model_info_store import ModelInfoRecord, ModelInfoStore, normalize_store_key

ROUTER_IDS = [
    "openrouter/free",
    "kilo-auto/free",
    "orcarouter/free",
    "some-vendor/router-x:free",
]


def _router_store(tmp_path, model_id: str) -> ModelInfoStore:
    store = ModelInfoStore(tmp_path / "model_info_store.json")
    rec = ModelInfoRecord.from_provider_record(
        {
            "provider_model_id": model_id,
            "decision": "keep",
            "tier": "flash",
            "coding": True,
            "confidence": 1.0,
            "evidence_level": "strong",
            "evidence": ["Router model: always keep (routing meta-model)"],
            "pricing": None,
        },
        provider="cline",
        evaluated_at=datetime.now(UTC).isoformat(),
    )
    store.put(normalize_store_key(model_id), rec)
    return store


@pytest.mark.parametrize("model_id", ROUTER_IDS)
def test_router_cache_hit_keeps_flash(tmp_path, model_id):
    store = _router_store(tmp_path, model_id)
    coord = EvaluatorCoordinator(
        provider_name="cline",
        aa=None,
        models_dev=None,
        evaluator=None,
        min_score=24.0,
        max_score=45.0,
        store=store,
    )

    rec = coord.evaluate({"id": model_id})

    assert rec["source"] == "cache", "expected strong cache-hit path"
    assert rec["decision"] == "keep"
    assert rec["tier"] == "flash"
    assert rec["coding"] is True
    assert rec["evidence_level"] == "strong"
