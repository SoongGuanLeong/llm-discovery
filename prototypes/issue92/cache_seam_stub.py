"""Prototype stub for #92 — in-pipeline cache lookup before LLM.

Decision (from #91 + #90 + handoff):
- Seam: pipeline.evaluate_model early return, right after resolve_model,
  before EvidenceCollector/Judge. Rejected: discover_provider wrapper
  (too coarse, needs benchmark context) and build_all loop (duplicates merge logic).
- Hit policy: strong-only. moderate/weak/none = miss -> full LLM path.
- Store: benchmarks+pricing only (slim). Raw provider model_id stays in yaml
  (Ephemeral Report) for Bifrost routing; store key is normalize_store_key(raw_id).
- Benchmarks immutable: null->fill only, no delta rebuild. Pricing TTL 14d:
  if stale (>14d) re-average from catalog pricing groups (aggregate_pricing),
  no LLM. Else copy verbatim.
- Invalidation precedence (mod ADR 0007): Identity -> new-key Churn -> Pricing TTL only.
  Evidence/AA delta disabled.

Throwaway — not for production. Mirrors prior prototype/pipeline_cache_prototype.py
but tightened to #91 decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from llm_discovery.model_info_store import (
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    aggregate_pricing,
    is_stale,
    normalize_store_key,
)

TTL_DAYS = 14  # per #91 + CONTEXT Record TTL (pricing-only)

# --- Hit classification (strong-only per #91 Q1 A) ---

def classify_hit(record: ModelInfoRecord | None) -> str:
    if record is None:
        return "miss"
    lvl = (record.evidence_level or "none").lower()
    return "strong_hit" if lvl == "strong" else "miss"


def _pricing_is_stale(record: ModelInfoRecord) -> bool:
    # pricing TTL 14d — reuse store Δ is_stale semantics on _meta.last_updated
    return is_stale(record._meta.last_updated, TTL_DAYS)


def _refresh_pricing_if_stale(
    cached: ModelInfoRecord,
    fresh_observations: list[dict[str, Any]] | None = None,
) -> PricingSnapshot | None:
    """If stale, re-average pricing from catalog observations; else copy verbatim.
    fresh_observations = list of {blended,input,output,provider} from catalogs.
    When None / empty and stale, return cached verbatim (catalog miss -> no change).
    """
    if not _pricing_is_stale(cached):
        return cached.pricing
    if not fresh_observations:
        return cached.pricing
    agg = aggregate_pricing(fresh_observations)
    if agg is None:
        return cached.pricing
    return PricingSnapshot.from_dict(agg)


def _gap_fill_benchmarks(
    cached_bm: dict[str, Any],
    fresh_bm: dict[str, Any] | None,
) -> dict[str, Any]:
    """Immutable benchmarks: null->fill only. No delta rebuild per #91 Q3.
    Fresh profile scores fill only when cached score is None/missing.
    """
    if not fresh_bm or not fresh_bm.get("scores"):
        return cached_bm
    out = dict(cached_bm)
    scores = dict(out.get("scores", {}))
    for k, v in fresh_bm["scores"].items():
        if k not in scores or scores[k] is None:
            scores[k] = v
        # else keep cached verbatim — even if fresh differs
    out["scores"] = scores
    # raw_benchmarks union (dedup by string) — gap-fill style
    if fresh_bm.get("raw_benchmarks"):
        seen = set(str(x) for x in out.get("raw_benchmarks", []))
        merged = list(out.get("raw_benchmarks", []))
        for rb in fresh_bm["raw_benchmarks"]:
            if str(rb) not in seen:
                merged.append(rb)
                seen.add(str(rb))
        out["raw_benchmarks"] = merged
    return out


def build_cached_keep_record(
    raw_model_id: str,
    provider_name: str,
    cached: ModelInfoRecord,
    fresh_pricing_obs: list[dict[str, Any]] | None = None,
    fresh_bm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strong-hit copy: skip LLM, preserve raw id for yaml/Bifrost.
    - provider_model_id = raw_model_id (exact case/prefix/free per #90)
    - cache_key = normalized key (for provenance)
    - benchmarks = gap-fill only, pricing = re-avg if stale else verbatim
    """
    cache_key = normalize_store_key(raw_model_id)
    pricing = _refresh_pricing_if_stale(cached, fresh_pricing_obs)
    bm_dict = cached.benchmarks.to_dict() if cached.benchmarks else {"scores": {}, "raw_benchmarks": []}
    bm_dict = _gap_fill_benchmarks(bm_dict, fresh_bm)

    return {
        "provider_model_id": raw_model_id,  # raw for Bifrost POST {model: raw_id}
        "cache_key": cache_key,
        "aa_model_id": cached.aa_model_id,
        "aa_score": cached.aa_score,
        "coding_score": cached.coding_score,
        "benchmarks": bm_dict,
        "pricing": pricing.to_dict() if pricing else {},
        "evidence": list(cached.evidence),
        "evidence_level": cached.evidence_level,
        "confidence": cached.confidence,
        "tier": cached.tier,
        "decision": "keep",
        "cached": True,
        "cache_hit_level": "strong",
        "reason": "cache_hit:strong:pricing_ttl_14d" if _pricing_is_stale(cached) else "cache_hit:strong",
        # provenance — store slim, so yaml keeps provider binding
        "provider": provider_name,
    }


# --- Seam: evaluate_model early return (chosen) ---

def evaluate_model_with_cache(
    model: dict[str, Any],
    provider_name: str,
    aa: Any,
    models_dev: Any,
    evaluator: Any,
    min_score: float,
    max_score: float,
    cache: Any | None = None,
    store: ModelInfoStore | None = None,
    fresh_pricing_obs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Thin seam: identical to pipeline.evaluate_model but with pre-LLM hit.

    Order:
      1. resolve_model
      2. normalize_store_key + store.get
      3. classify strong_hit / pricing TTL guard
      4a. strong_hit -> build_cached_keep_record (skip EvidenceCollector+Judge+Gate)
      4b. miss -> full pipeline then should_cache gate writes back via store.put

    Alternatives rejected:
      - discover_provider wrapper: needs per-model benchmark context, duplicates
        stale/pricing logic, hides seam from unit tests.
      - build_all loop: too late, forces ThreadPool cross-provider lock for merge.

    Fallback (catalog churn / identity):
      - Miss (new normalized key not in store) -> full LLM.
      - Identity fail (UUID/hallucinated) already blocked by Accurate-Enough Gate,
        never reaches cache write (should_cache false).
      - Pricing-only TTL never forces LLM — just re-avg.
    """
    from llm_discovery.evidence_collector import EvidenceCollector
    from llm_discovery.judge import Judge
    from llm_discovery.model_resolver import resolve_model
    from llm_discovery.policy_gate import PolicyGate

    raw_id = model["id"]
    resolution = resolve_model(raw_id, aa, models_dev, cache)

    # --- cache check AFTER resolve, BEFORE evidence ---
    cache_key = normalize_store_key(raw_id)
    cached = store.get(cache_key) if store is not None and cache_key else None
    hit = classify_hit(cached)

    if hit == "strong_hit":
        # Optional: build fresh_bm for gap-fill from local cache (no LLM)
        fresh_bm = None
        if cache is not None:
            try:
                from llm_discovery.benchmarks import BenchmarkDataCache, build_benchmark_profile
                if isinstance(cache, BenchmarkDataCache):
                    profile = build_benchmark_profile(raw_id, provider_name, cache)
                    fresh_bm = profile.to_dict() if profile.scores else None
            except Exception:
                fresh_bm = None
        return build_cached_keep_record(raw_id, provider_name, cached, fresh_pricing_obs, fresh_bm)

    # --- miss: full pipeline ---
    packet = EvidenceCollector(provider_name).collect(model, cache, models_dev, resolution)
    if packet.is_specialized():
        # keep vision exception parity with pipeline.py
        from llm_discovery.pipeline import _is_vision_only, _is_coding_capable, _is_cheap_or_free, deterministic_drop_record
        if _is_vision_only(packet.deterministic_flags) and _is_coding_capable(resolution, cache, raw_id, provider_name) and _is_cheap_or_free(resolution, raw_id, models_dev):
            pass
        else:
            reason = packet.deterministic_flags[0] if packet.deterministic_flags else "specialized_model"
            return deterministic_drop_record(raw_id, reason, cache)

    judge = Judge(evaluator)
    try:
        llm_result = judge.evaluate(provider_name, model, packet, cache)
    except Exception as exc:
        profile = getattr(judge, "_last_profile", None)
        return PolicyGate(min_score, max_score, cache).error_record(raw_id, exc, provider_name, profile=profile)

    gate = PolicyGate(min_score, max_score, cache)
    rec = gate.apply(llm_result, resolution, raw_id, provider_name, profile=getattr(judge, "_last_profile", None))

    # write-back only if Keeper (strong gate passes) — mirrors backfill is_accurate_enough
    if store is not None and rec.get("decision") == "keep" and rec.get("evidence_level") == "strong":
        try:
            # from_provider_record expects provider/evaluated_at
            mir = ModelInfoRecord.from_provider_record(rec, provider=provider_name, evaluated_at=datetime.now(UTC).isoformat())
            # merge via store.put semantics (gap-fill + pricing avg handled by ModelInfoStore)
            store.put(cache_key, mir)
        except Exception:
            pass
    return rec
