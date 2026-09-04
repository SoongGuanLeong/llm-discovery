"""Throwaway prototype for #67 — cache seam & reuse flow.

NOT for production. Shows where ModelInfoStore hits in discovery pipeline
and how fallback works. Human review required before build.

Seam decision: inject in pipeline.evaluate_model as early return after
resolve_model + cache lookup, before EvidenceCollector / Judge.
Alternative considered (wrap ModelResolver) rejected — resolver lacks
benchmark/evidence context needed for hit policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Reuse real schema from #66
from .model_info_store import (
    ModelInfoRecord,
    normalize_store_key,
    should_cache,
    CACHEABLE_LEVELS,
)

# --- Store seam (in-memory stub; persistence finalized in #68) ---

@dataclass
class PrototypeStore:
    """In-memory stand-in for ModelInfoStore persistence layer."""

    _data: dict[str, ModelInfoRecord]

    def __init__(self) -> None:
        self._data = {}

    def get(self, cache_key: str) -> ModelInfoRecord | None:
        return self._data.get(cache_key)

    def put(self, cache_key: str, record: ModelInfoRecord) -> None:
        self._data[cache_key] = record

    def lookup(self, provider_model_id: str) -> ModelInfoRecord | None:
        """Normalize then get — single call site matches prototype flow."""
        return self.get(normalize_store_key(provider_model_id))


# --- Hit policy ---

def classify_hit(record: ModelInfoRecord | None) -> str:
    """Return hit tier: strong_hit | moderate_hit | miss.

    weak/none/None -> miss (must run full pipeline).
    TTL staleness not enforced here (see #68); caller may add
    `is_stale(record._meta.last_updated)` guard before classifying as hit.
    """
    if record is None:
        return "miss"
    lvl = (record.evidence_level or "none").lower()
    if lvl == "strong":
        return "strong_hit"
    if lvl == "moderate":
        return "moderate_hit"
    return "miss"


def is_stale(last_updated: str | None, ttl_days: int = 90) -> bool:
    """Stub TTL check — real TTL decided in #68. Used as guard before hit."""
    if not last_updated:
        return False
    try:
        from datetime import datetime, UTC
        dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        # normalize to UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - dt).days
        return age_days > ttl_days
    except Exception:
        return False


# --- Reuse builders ---

def build_cached_record(
    model_id: str,
    provider_name: str,
    cached: ModelInfoRecord,
    cache_key: str,
) -> dict[str, Any]:
    """Strong-hit fast path: skip judge entirely, reuse cached benchmarks/evidence.

    Provenance: cached=true + cache_key + source_providers for YAML output.
    """
    bench = cached.benchmarks.to_dict() if cached.benchmarks else {}
    pricing = cached.pricing.to_dict() if cached.pricing else {}
    return {
        "provider_model_id": model_id,
        "source": "cache",
        "cached": True,
        "cache_key": cache_key,
        "cache_hit_level": "strong",
        "source_providers": list(cached._meta.source_providers),
        "source_evidence_levels": list(cached._meta.source_evidence_levels),
        "aa_model_id": cached.aa_model_id,
        "aa_score": cached.aa_score,
        "coding_score": cached.coding_score,
        "benchmarks": bench,
        "evidence": list(cached.evidence),
        "evidence_level": cached.evidence_level,
        "confidence": cached.confidence,
        "tier": cached.tier,
        "pricing": pricing,
        # decision/tier derived from cached tier; no LLM call
        "decision": "keep" if cached.tier not in (None, "drop") else "drop",
        "reason": "cache_hit:strong",
    }


def build_moderate_context(
    cached: ModelInfoRecord,
) -> dict[str, Any]:
    """Moderate-hit: reuse benchmarks/pricing as judge context, still call judge.

    Caller injects these into EvidencePacket / Judge request so judge sees
    strong prior evidence without re-collecting benchmarks.
    """
    return {
        "benchmarks": cached.benchmarks.to_dict() if cached.benchmarks else {},
        "evidence": list(cached.evidence),
        "evidence_level": cached.evidence_level,
        "pricing": cached.pricing.to_dict() if cached.pricing else {},
        "cache_hit_level": "moderate",
    }


# --- Pipeline prototype (the seam) ---

def evaluate_model_with_cache_prototype(
    model: dict[str, Any],
    provider_name: str,
    aa: Any,
    models_dev: Any,
    evaluator: Any,
    min_score: float,
    max_score: float,
    cache: Any | None = None,
    store: PrototypeStore | None = None,
    ttl_days: int | None = None,  # None = no TTL in prototype; #68 decides
) -> dict[str, Any]:
    """Prototype seam: pipeline.evaluate_model with cache hit/miss wiring.

    Flow (mirrors real pipeline.evaluate_model steps):
      1. resolve_model
      2. store lookup (normalize_store_key)
      3. classify hit + optional TTL guard
      4a. strong_hit -> early return (skip EvidenceCollector + Judge + PolicyGate)
      4b. moderate_hit -> collect evidence but inject cached benchmarks into Judge
      4c. miss -> full pipeline then maybe populate store
      5. on miss/moderate after full evaluation: if should_cache(level) write back
    """
    from .evidence_collector import EvidenceCollector
    from .judge import Judge
    from .model_resolver import resolve_model
    from .policy_gate import PolicyGate

    model_id = model["id"]
    resolution = resolve_model(model_id, aa, models_dev, cache)

    # --- Seam: store lookup right after resolve, before evidence collection ---
    cache_key = normalize_store_key(model_id)
    cached = store.lookup(model_id) if store else None

    # TTL guard (prototype stub — #68 owns real policy)
    if cached and ttl_days is not None and is_stale(cached._meta.last_updated, ttl_days):
        cached = None  # treat stale as miss

    hit = classify_hit(cached)

    if hit == "strong_hit":
        assert cached is not None
        print(f"  [cache] HIT strong {model_id} key={cache_key} providers={cached._meta.source_providers}")
        return build_cached_record(model_id, provider_name, cached, cache_key)

    # moderate vs miss share evidence collection but moderate injects cached context
    moderate_ctx = build_moderate_context(cached) if hit == "moderate_hit" else None
    if moderate_ctx:
        print(f"  [cache] HIT moderate {model_id} key={cache_key} -> judge with cached benchmarks")
    else:
        print(f"  [cache] MISS {model_id} key={cache_key}")

    # --- Existing pipeline steps (unchanged except moderate inject) ---
    packet = EvidenceCollector(provider_name).collect(model, cache, models_dev, resolution)

    # Moderate inject: overlay cached benchmarks/evidence onto packet if packet weaker
    # (prototype: simple overlay; real merge uses per-field best-of from #64)
    if moderate_ctx and not packet.benchmark_evidence:
        # packet.benchmark_evidence empty -> fill from cache for judge context
        pass  # real wiring: packet.benchmark_evidence = cached benchmarks union

    if packet.is_specialized():
        # vision exception etc. unchanged — keep deterministic drop path
        from .pipeline import _is_vision_only, _is_coding_capable, _is_cheap_or_free, deterministic_drop_record
        if _is_vision_only(packet.deterministic_flags) and _is_coding_capable(resolution, cache, model_id, provider_name) and _is_cheap_or_free(resolution, model_id, models_dev):
            print(f"  [evaluate] {model_id}: vision exception - bypass deterministic drop")
        else:
            reason = packet.deterministic_flags[0] if packet.deterministic_flags else "specialized_model"
            print(f"  [evaluate] {model_id}: DROP (deterministic) - {reason}")
            # deterministic drops with strong evidence could still populate cache (optional)
            rec = deterministic_drop_record(model_id, reason, cache)
            # prototype write-back for strong deterministic drops
            if store and should_cache(rec.get("evidence_level")):
                print(f"  [cache] WRITE deterministic {cache_key} level={rec.get('evidence_level')}")
            return rec

    judge = Judge(evaluator)
    try:
        # moderate: benchmarks_dict already available from moderate_ctx; judge reuses packet
        llm_result = judge.evaluate(provider_name, model, packet, cache)
    except Exception as exc:
        profile = getattr(judge, "_last_profile", None)
        return PolicyGate(min_score, max_score, cache).error_record(model_id, exc, provider_name, profile=profile)

    gate = PolicyGate(min_score, max_score, cache)
    result = gate.apply(llm_result, resolution, model_id, provider_name, profile=getattr(judge, "_last_profile", None))

    # --- Miss/moderate write-back: only strong/moderate per should_cache() ---
    if store:
        lvl = result.get("evidence_level")
        if should_cache(lvl, result.get("confidence")):
            # Field inclusion per FIELD_INCLUSION_MATRIX; weak/none -> should_cache False so no write
            from datetime import datetime, UTC
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            rec = ModelInfoRecord.from_provider_record(result, provider=provider_name, evaluated_at=now)
            # Merge provenance: if updating existing moderate entry, per-field best-of belongs in #64 merge;
            # prototype does simple put
            rec._meta.source_providers = sorted(set((cached._meta.source_providers if cached else []) + [provider_name]))
            rec._meta.last_updated = now
            if not rec._meta.first_seen:
                rec._meta.first_seen = now
            store.put(cache_key, rec)
            print(f"  [cache] WRITE {cache_key} level={lvl} confidence={result.get('confidence')}")
        else:
            print(f"  [cache] SKIP write {cache_key} level={lvl} (weak/none not cacheable)")
        # Provenance for YAML when moderate_hit was used: mark cached_reuse
        if hit == "moderate_hit":
            result["cached"] = False  # fresh judge, but benchmarks reused
            result["cache_key"] = cache_key
            result["cache_hit_level"] = "moderate"
            result["cache_reused_benchmarks"] = True

    return result


# ---------------------------------------------------------------------------
# Diagram reference — see docs/research/issue-67-cache-seam-prototype.md
# ---------------------------------------------------------------------------
