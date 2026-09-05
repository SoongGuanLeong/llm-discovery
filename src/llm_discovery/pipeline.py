"""End-to-end discovery pipeline — thin coordinator.

evaluate_model is now <30 lines coordinating four seamed adapters:
  EvidenceCollector.collect(), ModelResolver.resolve(), Judge.evaluate(), PolicyGate.apply()

Other entry points (discover_single, discover_provider, discover_all_providers)
retain isolation but delegate per-model work to evaluate_model.
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Thin coordinator seams — 4 adapters (12 → 4 explicit seam imports)
from .discovery import discover_cloudflare_models, discover_models
from .evidence_collector import EvidenceCollector
from .judge import Judge
from .model_info_store import (
    ModelInfoStore,
    PricingSnapshot,
    aggregate_pricing,
    is_stale,
    normalize_store_key,
)
from .model_resolver import ModelResolver, resolve_model
from .policy_gate import PolicyGate
from .secrets import load_all_secrets, load_discovery_secrets, load_shared_secrets  # noqa: keep aliases for patch compat

TTL_DAYS = 14  # Record TTL for pricing reuse per CONTEXT / #91


# ---------------------------------------------------------------------------
# In-pipeline cache helpers (issue #96) — strong-only, pricing TTL 14d, benchmarks gap-fill
# ---------------------------------------------------------------------------

def classify_hit(record: Any | None) -> str:
    """Strong-only hit classification.

    Slim v2 store holds only Keepers (benchmarks+pricing+_meta); moderate/weak
    never written, so existence without evidence_level implies Keeper.  When
    evidence_level present (legacy/mock), enforce strong-only.
    Returns "strong_hit" or "miss".
    """
    if record is None:
        return "miss"
    lvl = getattr(record, "evidence_level", None)
    if lvl is None:
        lvl = record.get("evidence_level") if isinstance(record, dict) else None
    if lvl is None or str(lvl).strip() == "":
        # Slim Keeper — no evidence_level persisted, existence == strong
        return "strong_hit"
    lvl_norm = str(lvl).strip().lower()
    return "strong_hit" if lvl_norm == "strong" else "miss"


def _pricing_is_stale(record: Any) -> bool:
    """Pricing TTL 14d via _meta.last_updated (is_stale)."""
    try:
        last = getattr(record._meta, "last_updated", None) if hasattr(record, "_meta") else None
        if last is None and isinstance(record, dict):
            last = record.get("_meta", {}).get("last_updated") if isinstance(record.get("_meta"), dict) else None
        return is_stale(last, TTL_DAYS)
    except Exception:
        return False


def _refresh_pricing_if_stale(
    cached: Any,
    fresh_observations: list[dict[str, Any]] | None = None,
) -> Any:
    """If stale, re-average pricing from catalog observations; else verbatim copy.

    fresh_observations = list of {blended,input,output,provider} from catalogs.
    When None/empty and stale, return cached verbatim (catalog miss -> no change).
    """
    if not _pricing_is_stale(cached):
        return cached.pricing if hasattr(cached, "pricing") else cached.get("pricing") if isinstance(cached, dict) else None
    if not fresh_observations:
        return cached.pricing if hasattr(cached, "pricing") else cached.get("pricing") if isinstance(cached, dict) else None
    agg = aggregate_pricing(fresh_observations)
    if agg is None:
        return cached.pricing if hasattr(cached, "pricing") else None
    return PricingSnapshot.from_dict(agg)


def _gap_fill_benchmarks(
    cached_bm: dict[str, Any],
    fresh_bm: dict[str, Any] | None,
) -> dict[str, Any]:
    """Immutable benchmarks: null->fill only. No delta rebuild per #91 Q3.

    Fresh profile scores fill only when cached score missing/None.
    raw_benchmarks union deduped by string repr.
    """
    if not fresh_bm or not fresh_bm.get("scores"):
        return cached_bm
    out = dict(cached_bm)
    scores = dict(out.get("scores", {}))
    for k, v in fresh_bm["scores"].items():
        if k not in scores or scores[k] is None:
            scores[k] = v
        # else keep cached verbatim — even if fresh differs (immutable)
    out["scores"] = scores
    if fresh_bm.get("raw_benchmarks"):
        seen = set(str(x) for x in out.get("raw_benchmarks", []))
        merged = list(out.get("raw_benchmarks", []))
        for rb in fresh_bm["raw_benchmarks"]:
            if str(rb) not in seen:
                merged.append(rb)
                seen.add(str(rb))
        out["raw_benchmarks"] = merged
    # Preserve coverage fields if cached lacks them but fresh has them (gap-fill)
    for cov_key in ("benchmark_coverage", "coverage_with_supplements"):
        if out.get(cov_key) is None and fresh_bm.get(cov_key) is not None:
            out[cov_key] = fresh_bm[cov_key]
    return out


def build_cached_keep_record(
    raw_model_id: str,
    provider_name: str,
    cached: Any,
    fresh_pricing_obs: list[dict[str, Any]] | None = None,
    fresh_bm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strong-hit copy: skip LLM, preserve raw id for yaml/Bifrost.

    - provider_model_id = raw_model_id (exact case/prefix/free per #90)
    - cache_key = normalized key (for provenance)
    - benchmarks = gap-fill only, pricing = re-avg if stale else verbatim
    Slim-aware: cached fields aa_model_id etc may be None; keep None.
    """
    cache_key = normalize_store_key(raw_model_id)
    pricing_snap = _refresh_pricing_if_stale(cached, fresh_pricing_obs)
    # Normalize pricing snapshot to dict shape
    if hasattr(pricing_snap, "to_dict"):
        pricing_dict = pricing_snap.to_dict()
    elif isinstance(pricing_snap, dict):
        pricing_dict = pricing_snap
    elif pricing_snap is not None:
        pricing_dict = {"blended": pricing_snap}
    else:
        pricing_dict = {}
    # Benchmarks
    if hasattr(cached, "benchmarks") and cached.benchmarks:
        bm_dict = cached.benchmarks.to_dict() if hasattr(cached.benchmarks, "to_dict") else dict(cached.benchmarks)
    elif isinstance(cached, dict) and cached.get("benchmarks"):
        bm_dict = dict(cached["benchmarks"])
    else:
        bm_dict = {"scores": {}, "raw_benchmarks": []}
    bm_dict = _gap_fill_benchmarks(bm_dict, fresh_bm)
    # Legacy fields (may be None in slim)
    aa_model_id = getattr(cached, "aa_model_id", None) if not isinstance(cached, dict) else cached.get("aa_model_id")
    aa_score = getattr(cached, "aa_score", None) if not isinstance(cached, dict) else cached.get("aa_score")
    coding_score = getattr(cached, "coding_score", None) if not isinstance(cached, dict) else cached.get("coding_score")
    evidence = list(getattr(cached, "evidence", []) or (cached.get("evidence", []) if isinstance(cached, dict) else []))
    evidence_level = getattr(cached, "evidence_level", None) if not isinstance(cached, dict) else cached.get("evidence_level")
    if not evidence_level:
        evidence_level = "strong"
    confidence = getattr(cached, "confidence", None) if not isinstance(cached, dict) else cached.get("confidence")
    if confidence is None:
        confidence = 0.9
    tier = getattr(cached, "tier", None) if not isinstance(cached, dict) else cached.get("tier")
    if not tier:
        tier = "flash"
    stale = _pricing_is_stale(cached)
    return {
        "provider_model_id": raw_model_id,  # raw for Bifrost POST {model: raw_id}
        "cache_key": cache_key,
        "aa_model_id": aa_model_id,
        "aa_score": aa_score,
        "coding_score": coding_score,
        "benchmarks": bm_dict,
        "pricing": pricing_dict,
        "evidence": evidence,
        "evidence_level": evidence_level,
        "confidence": confidence,
        "tier": tier,
        "decision": "keep",
        "cached": True,
        "cache_hit_level": "strong",
        "reason": "cache_hit:strong:pricing_ttl_14d" if stale else "cache_hit:strong",
        "provider": provider_name,
        "source": "cache",
        "coding": True,
    }


VISION_CHEAP_THRESHOLD = 1.2
VISION_CODING_SCORE_MIN = 35.0
VISION_AA_CODING_MIN = 45.0
VISION_AA_INTEL_MIN = 55.0
VISION_BENCH_MIN = 50.0


def _is_vision_only(flags: list[str]) -> bool:
    """True only if every deterministic flag is vision — no embedding/tts/etc."""
    if not flags:
        return False
    return all(f == "specialized_model:vision" for f in flags)


def _is_vision_free_model(model_id: str, resolution: Any, models_dev: Any) -> bool:
    lower = model_id.lower()
    if "free" in lower:
        return True
    aa_model = getattr(resolution, "aa_model", None) if resolution else None
    if aa_model:
        pricing = aa_model.get("pricing") or {}
        blended = pricing.get("price_1m_blended_3_to_1")
        inp = pricing.get("price_1m_input_tokens")
        out = pricing.get("price_1m_output_tokens")
        if blended == 0 or (inp == 0 and out == 0):
            return True
    # models_dev has no pricing field; free detection via model_id substring is sufficient
    return False


def _is_cheap_or_free(resolution: Any, model_id: str, models_dev: Any) -> bool:
    if _is_vision_free_model(model_id, resolution, models_dev):
        return True
    aa_model = getattr(resolution, "aa_model", None) if resolution else None
    if aa_model:
        pricing = aa_model.get("pricing") or {}
        blended = pricing.get("price_1m_blended_3_to_1")
        if blended is not None and blended <= VISION_CHEAP_THRESHOLD:
            return True
    return False


def _is_coding_capable(resolution: Any, cache: Any, model_id: str, provider_name: str) -> bool:
    aa_model = getattr(resolution, "aa_model", None) if resolution else None
    if aa_model:
        evals = aa_model.get("evaluations") or {}
        aa_coding = evals.get("artificial_analysis_coding_index")
        aa_intel = evals.get("artificial_analysis_intelligence_index")
        if aa_coding is not None and aa_coding >= VISION_AA_CODING_MIN:
            return True
        if aa_intel is not None and aa_intel >= VISION_AA_INTEL_MIN:
            return True
    if cache is not None:
        from .benchmarks import build_benchmark_profile, compute_coding_score

        profile = build_benchmark_profile(model_id, provider_name, cache)
        if profile.scores:
            coding_score, _, _ = compute_coding_score(profile)
            if coding_score is not None and coding_score >= VISION_CODING_SCORE_MIN:
                return True
            for key in ("swe_bench_verified", "swe_bench_pro", "terminal_bench", "terminal_bench_2_1"):
                val = profile.scores.get(key)
                if val:
                    score = val.get("score") if isinstance(val, dict) else getattr(val, "score", None)
                    if score is not None and score >= VISION_BENCH_MIN:
                        return True
            # AA indexes also mirrored in benchmark cache
            aa_coding_bm = profile.scores.get("aa_coding")
            if aa_coding_bm:
                s = aa_coding_bm.get("score") if isinstance(aa_coding_bm, dict) else None
                if s is not None and s >= VISION_AA_CODING_MIN:
                    return True
            aa_intel_bm = profile.scores.get("aa_intelligence")
            if aa_intel_bm:
                s = aa_intel_bm.get("score") if isinstance(aa_intel_bm, dict) else None
                if s is not None and s >= VISION_AA_INTEL_MIN:
                    return True
    return False


def evaluate_model(
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
    """Judge one model and apply tiering (thin coordinator + cache seam per #96).

    Early return right after resolve_model and before EvidenceCollector when
    store holds strong Keeper (slim v2). Hit = strong-only; moderate/weak = miss.
    Pricing stale (>14d) re-averaged via aggregate_pricing, benchmarks gap-fill only,
    raw provider_model_id preserved verbatim for Ephemeral Report / Bifrost.
    """
    model_id = model["id"]  # raw verbatim per #90
    print(f"  [evaluate] {model_id}: starting...")
    resolution = resolve_model(model_id, aa, models_dev, cache)
    # --- In-pipeline cache check (issue #96) — after resolve, before evidence ---
    if store is not None:
        try:
            cache_key = normalize_store_key(model_id)
            if cache_key:
                cached = store.get(cache_key)
                hit = classify_hit(cached)
                if hit == "strong_hit":
                    assert cached is not None
                    # Fresh benchmarks for gap-fill from BenchmarkDataCache (no LLM)
                    fresh_bm: dict[str, Any] | None = None
                    if cache is not None:
                        try:
                            from .benchmarks import BenchmarkDataCache, build_benchmark_profile

                            if isinstance(cache, BenchmarkDataCache):
                                profile = build_benchmark_profile(model_id, provider_name, cache)
                                fresh_bm = profile.to_dict() if profile.scores else None
                        except Exception:
                            fresh_bm = None
                    # Derive fresh pricing obs from resolution if caller did not supply
                    obs = fresh_pricing_obs
                    if obs is None:
                        try:
                            aa_model = getattr(resolution, "aa_model", None)
                            if aa_model and aa_model.get("pricing"):
                                p = aa_model["pricing"]
                                cand = {
                                    "blended": p.get("price_1m_blended_3_to_1", p.get("blended")),
                                    "input": p.get("price_1m_input_tokens", p.get("input")),
                                    "output": p.get("price_1m_output_tokens", p.get("output")),
                                    "provider": provider_name,
                                }
                                if cand["blended"] is not None or cand["input"] is not None or cand["output"] is not None:
                                    obs = [cand]
                        except Exception:
                            obs = fresh_pricing_obs
                    print(f"  [cache] HIT strong {model_id} key={cache_key}")
                    return build_cached_keep_record(model_id, provider_name, cached, obs, fresh_bm)
                else:
                    if cached is not None:
                        print(f"  [cache] MISS moderate/weak {model_id} key={cache_key} -> full pipeline")
        except Exception as exc:  # cache seam never breaks pipeline
            print(f"  [cache] lookup failed {model_id}: {exc} -> full pipeline")
    packet = EvidenceCollector(provider_name).collect(model, cache, models_dev, resolution)
    if packet.is_specialized():
        if _is_vision_only(packet.deterministic_flags) and _is_coding_capable(resolution, cache, model_id, provider_name) and _is_cheap_or_free(resolution, model_id, models_dev):
            print(f"  [evaluate] {model_id}: vision exception - bypass deterministic drop (coding+cheap)")
        else:
            reason = packet.deterministic_flags[0] if packet.deterministic_flags else "specialized_model"
            print(f"  [evaluate] {model_id}: DROP (deterministic) - {reason}")
            return deterministic_drop_record(model_id, reason, cache)
    judge = Judge(evaluator)
    try:
        llm_result = judge.evaluate(provider_name, model, packet, cache)
    except Exception as exc:  # noqa: BLE001 — judge/transport errors → error, not drop
        print(f"  [evaluate] {model_id}: ERROR - {exc}")
        profile = getattr(judge, "_last_profile", None)
        return PolicyGate(min_score, max_score, cache).error_record(model_id, exc, provider_name, profile=profile)
    gate = PolicyGate(min_score, max_score, cache)
    return gate.apply(llm_result, resolution, model_id, provider_name, profile=getattr(judge, "_last_profile", None))


def _llm_error_record(model_id: str, exc: Exception, coding_score: float = 0.0, benchmarks: dict = None) -> dict[str, Any]:
    """Judge failure → decision=error, tier=error (NOT drop)."""
    return {
        "provider_model_id": model_id,
        "source": "llm_error",
        "coding": False,
        "canonical_name": None,
        "aa_model_id": None,
        "aa_name": None,
        "aa_slug": None,
        "aa_score": None,
        "coding_score": coding_score,
        "benchmarks": benchmarks if benchmarks is not None else {},
        "confidence": 0.0,
        "decision": "error",
        "tier": "error",
        "evidence_level": "none",
        "evidence": [f"LLM evaluation failed: {exc}"],
        "coding_assessment": None,
    }


def deterministic_drop_record(model_id: str, reason: str, cache=None) -> dict[str, Any]:
    """Pre-filter drop (specialised / non-coding models)."""
    from .benchmarks import BenchmarkDataCache, build_benchmark_profile, compute_coding_score

    profile = build_benchmark_profile(model_id, "", cache)
    benchmarks_dict = profile.to_dict() if profile.scores else {}
    coding_score, _, _ = compute_coding_score(profile) if profile.scores else (None, 0.0, [])
    return {
        "provider_model_id": model_id,
        "source": "deterministic",
        "coding": False,
        "canonical_name": None,
        "aa_model_id": None,
        "aa_name": None,
        "aa_slug": None,
        "aa_score": None,
        "coding_score": coding_score if profile.scores else None,
        "benchmarks": benchmarks_dict,
        "confidence": 1.0,
        "decision": "drop",
        "tier": "drop",
        "evidence_level": "strong",
        "evidence": [reason],
        "coding_assessment": None,
    }


_deterministic_drop_record = deterministic_drop_record


def _resolve_provider_config(provider_name: str, config: Any) -> Any:
    """Return the provider config entry or raise a clear, config-level error."""
    for provider in config.providers:
        if provider.name == provider_name:
            return provider
    configured = [p.name for p in config.providers]
    raise ValueError(
        f"Provider not found in config: {provider_name!r}. "
        f"Configured providers: {configured or 'none'}. "
        "Add it under 'providers' in config/providers.yaml."
    )


def _aa_score(aa_model: dict[str, Any] | None) -> float | None:
    if aa_model is None:
        return None
    return aa_model.get("evaluations", {}).get("artificial_analysis_intelligence_index")


def _aa_match(resolution: Any) -> dict[str, Any] | None:
    """The deterministic AA match handed to the judge as verified context."""
    if resolution.aa_model is None:
        return {"matched": False, "model_id": None, "score": None}
    aa_model = resolution.aa_model
    return {
        "matched": True,
        "model_id": aa_model["id"],
        "score": _aa_score(aa_model),
    }


def _aa_candidates(resolution: Any) -> list[dict[str, Any]]:
    """Legacy: deterministic AA match(es) for backward compatibility."""
    if resolution.aa_model is None:
        return []
    aa_model = resolution.aa_model
    return [
        {
            "id": aa_model["id"],
            "name": aa_model["name"],
            "slug": aa_model["slug"],
            "score": _aa_score(aa_model),
        }
    ]


def pick_tracer_model(
    models: list[dict[str, Any]],
    aa: Any,
    min_score: float,
) -> dict[str, Any]:
    """Deterministically pick ONE provider model to trace."""
    from .model_resolver import resolve_model as _resolve

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for model in sorted(models, key=lambda m: m["id"]):
        score = _aa_score(_resolve(model["id"], aa).aa_model)
        if score is not None:
            scored.append((score, model["id"], model))
    if not scored:
        return sorted(models, key=lambda m: m["id"])[0]
    preferred = [t for t in scored if t[0] >= min_score]
    pool = preferred or scored
    pool.sort(key=lambda t: (-t[0], t[1]))
    return pool[0][2]


def _auto_free_record(provider_name: str) -> dict[str, Any]:
    """Auto-free provider: skip evaluation, return auto:free routing recommendation."""
    return {
        "provider_model_id": "auto:free",
        "source": "auto_free",
        "coding": True,
        "canonical_name": None,
        "aa_model_id": None,
        "aa_name": None,
        "aa_slug": None,
        "aa_score": None,
        "coding_score": None,
        "benchmarks": {},
        "confidence": 1.0,
        "decision": "keep",
        "tier": "max",
        "evidence_level": "strong",
        "evidence": [f"Provider {provider_name} uses auto_free discovery strategy"],
        "coding_assessment": None,
    }


def discover_single(
    provider_name: str,
    config: Any,
    aa: Any,
    models_dev: Any,
) -> dict[str, Any]:
    """T2 tracer bullet: enumerate a provider, evaluate ONE model, return record."""
    from .benchmarks import BenchmarkDataCache
    from .llm import LocalLLMEvaluator
    from .provider import resolve_provider
    from .search import make_searcher

    provider_config = _resolve_provider_config(provider_name, config)
    provider = resolve_provider(provider_config, models_dev)
    if provider.discovery_strategy == "bazaarlink":
        return _auto_free_record(provider_name)
    load_all_secrets(config.infisical)
    llm_api_key = os.environ.get(config.judge_llm.secret)
    if not llm_api_key:
        raise RuntimeError(f"Missing API key environment variable: {config.judge_llm.secret}")
    api_key = os.environ.get(provider.secret)
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {provider.secret}")
    base_url = os.path.expandvars(provider.base_url)
    # NaraRouter true-free filtering: branch before generic free rule
    if provider.discovery_strategy == "nararouter":
        from .discovery import discover_nararouter_models, get_nararouter_free_allowlist

        include_as_dropped = bool(getattr(provider_config, "include_paid_gated_as_dropped", False) or getattr(provider, "include_paid_gated_as_dropped", False))
        if include_as_dropped:
            allowlist = get_nararouter_free_allowlist()
            raw = discover_models(base_url, api_key)
            eval_models = [m for m in raw if m["id"] in allowlist]
            dropped_models = [m for m in raw if m["id"] not in allowlist]
            for m in dropped_models:
                m["_drop_reason"] = "paid_gated_free"
            print(f"[{provider_name}] NaraRouter true-free filter (include_paid_gated_as_dropped): raw {len(raw)} -> true-free {len(eval_models)} dropped_paid_gated {len(dropped_models)}")
        else:
            eval_models = discover_nararouter_models(base_url, api_key)
            dropped_models: list[dict[str, Any]] = []
            # discover_nararouter_models already logs raw -> true-free
    elif provider.discovery == "cloudflare":
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        models = discover_cloudflare_models(account_id, api_key)
        eval_models, dropped_models = _split_by_free_rule(models, provider_name)
        if dropped_models:
            print(f"[{provider_name}] Free-model filter: dropped {len(dropped_models)} non-free, keeping {len(eval_models)} free")
    else:
        models = discover_models(base_url, api_key)
        eval_models, dropped_models = _split_by_free_rule(models, provider_name)
        if dropped_models:
            print(f"[{provider_name}] Free-model filter: dropped {len(dropped_models)} non-free, keeping {len(eval_models)} free")
    if not eval_models:
        raise RuntimeError(f"No models to evaluate for {provider_name!r} after filtering (all {len(dropped_models)} dropped)")
    model = pick_tracer_model(eval_models, aa, config.artificial_analysis.min_score)
    searcher = make_searcher(
        brave_api_key=os.environ.get("BRAVE_API_KEY"),
        disabled=os.environ.get("DISABLE_WEB_SEARCH") == "1",
    )
    evaluator = LocalLLMEvaluator(
        base_url=config.judge_llm.base_url,
        model=config.judge_llm.model,
        api_key=llm_api_key,
        min_score=config.artificial_analysis.min_score,
        search_web=searcher.search,
    )
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, models_dev)
    return evaluate_model(
        model=model,
        provider_name=provider_name,
        aa=aa,
        models_dev=models_dev,
        evaluator=evaluator,
        min_score=config.artificial_analysis.min_score,
        max_score=config.artificial_analysis.max_score,
        cache=cache,
    )


def discover_provider(
    provider_name: str,
    config: Any,
    aa: Any,
    models_dev: Any,
    max_workers: int = 4,
    store: ModelInfoStore | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """T3 path: evaluate every model for a provider in parallel.

    store optional for in-pipeline cache reuse per #96 (strong-only, TTL 14d).
    When provided, evaluate_model early-returns on hit before LLM.
    """
    print(f"[{provider_name}] Starting discovery...")
    from .benchmarks import BenchmarkDataCache
    from .llm import LocalLLMEvaluator
    from .provider import resolve_provider
    from .search import make_searcher

    provider_config = _resolve_provider_config(provider_name, config)
    provider = resolve_provider(provider_config, models_dev)
    if provider.discovery_strategy == "bazaarlink":
        print(f"[{provider_name}] bazaarlink strategy -> auto:free")
        return {
            "keep": [_auto_free_record(provider_name)],
            "drop": [],
            "error": [],
        }
    load_all_secrets(config.infisical)
    llm_api_key = os.environ.get(config.judge_llm.secret)
    if not llm_api_key:
        raise RuntimeError(f"Missing API key environment variable: {config.judge_llm.secret}")
    api_key = os.environ.get(provider.secret)
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {provider.secret}")
    base_url = os.path.expandvars(provider.base_url)
    try:
        if provider.discovery_strategy == "nararouter":
            from .discovery import discover_nararouter_models, get_nararouter_free_allowlist

            include_as_dropped = bool(getattr(provider_config, "include_paid_gated_as_dropped", False) or getattr(provider, "include_paid_gated_as_dropped", False))
            if include_as_dropped:
                allowlist = get_nararouter_free_allowlist()
                raw = discover_models(base_url, api_key)
                eval_models = [m for m in raw if m["id"] in allowlist]
                dropped_models = [m for m in raw if m["id"] not in allowlist]
                for m in dropped_models:
                    m["_drop_reason"] = "paid_gated_free"
                print(f"[{provider_name}] NaraRouter true-free filter (include_paid_gated_as_dropped): raw {len(raw)} -> true-free {len(eval_models)} dropped_paid_gated {len(dropped_models)}")
                # keep eval_models as filtered; dropped_models holds paid-gated for optional SKIP logging
            else:
                eval_models = discover_nararouter_models(base_url, api_key)
                dropped_models: list[dict[str, Any]] = []
            print(f"[{provider_name}] Discovered {len(eval_models)} true-free models (NaraRouter allowlist)")
        elif provider.discovery == "cloudflare":
            account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
            print(f"[{provider_name}] Discovering models via Cloudflare API...")
            models = discover_cloudflare_models(account_id, api_key)
            print(f"[{provider_name}] Discovered {len(models)} models")
            eval_models, dropped_models = _split_by_free_rule(models, provider_name)
            if dropped_models:
                print(f"[{provider_name}] Free-model filter: dropped {len(dropped_models)} non-free, keeping {len(eval_models)} free")
        else:
            print(f"[{provider_name}] Discovering models from {base_url}/models ...")
            models = discover_models(base_url, api_key)
            print(f"[{provider_name}] Discovered {len(models)} models")
            eval_models, dropped_models = _split_by_free_rule(models, provider_name)
            if dropped_models:
                print(f"[{provider_name}] Free-model filter: dropped {len(dropped_models)} non-free, keeping {len(eval_models)} free")
    except Exception as exc:  # noqa: BLE001 — provider-level failure
        print(f"[{provider_name}] Discovery failed: {exc}")
        return provider_error_result(provider_name, exc)
    searcher = make_searcher(
        brave_api_key=os.environ.get("BRAVE_API_KEY"),
        disabled=os.environ.get("DISABLE_WEB_SEARCH") == "1",
    )
    evaluator = LocalLLMEvaluator(
        base_url=config.judge_llm.base_url,
        model=config.judge_llm.model,
        api_key=llm_api_key,
        min_score=config.artificial_analysis.min_score,
        search_web=searcher.search,
    )
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, models_dev)
    key_signals = ("aa_intelligence", "swe_bench_verified", "livecodebench", "humaneval")
    coverage_stats = {sig: sum(1 for e in cache._data.values() if sig in e.get("benchmarks", {})) for sig in key_signals}
    print(f"[{provider_name}] Benchmark cache: {len(cache._data)} models | coverage: {coverage_stats}")
    print(f"[{provider_name}] Evaluating {len(eval_models)} model(s) with {max_workers} worker(s)...")
    result: dict[str, list[dict[str, Any]]] = {"keep": [], "drop": [], "error": []}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_model = {
            pool.submit(
                evaluate_model,
                model=model,
                provider_name=provider_name,
                aa=aa,
                models_dev=models_dev,
                evaluator=evaluator,
                min_score=config.artificial_analysis.min_score,
                max_score=config.artificial_analysis.max_score,
                cache=cache,
                store=store,
            ): model
            for model in eval_models
        }
        completed = 0
        for future in as_completed(future_to_model):
            model = future_to_model[future]
            completed += 1
            try:
                evaluation = future.result()
            except Exception as exc:  # noqa: BLE001 — catch-all for thread errors
                evaluation = _llm_error_record(model["id"], exc)
            decision = evaluation["decision"]
            tier = evaluation.get("tier", "?")
            print(f"[{provider_name}] [{completed}/{len(eval_models)}] {decision.upper():4} {tier:5} {model['id']}")
            if decision == "keep":
                result["keep"].append(evaluation)
            elif decision == "drop":
                result["drop"].append(evaluation)
            else:
                result["error"].append(evaluation)
    # Dropped models are completely omitted: no LLM, no YAML (generic) or paid-gated excluded (nararouter)
    if dropped_models:
        for m in dropped_models:
            reason = m.get("_drop_reason", "free-model-rule" if provider.discovery_strategy != "nararouter" else "paid_gated_free")
            print(f"[{provider_name}] SKIP ({reason}) {m['id']}")
    for bucket in result.values():
        bucket.sort(key=lambda r: r["provider_model_id"])
    print(f"[{provider_name}] Done: KEEP={len(result['keep'])} DROP={len(result['drop'])} ERROR={len(result['error'])}")
    return result


def discover_all_providers(
    config: Any,
    aa: Any,
    models_dev: Any,
    max_workers: int = 4,
    output_dir: Path = Path("data/results"),
    store: ModelInfoStore | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """T3 path for every configured provider."""
    output_dir.mkdir(parents=True, exist_ok=True)
    from .results import save_provider_result

    # Single secret injection for the batch — per-provider calls are no-ops via idempotent cache.
    load_all_secrets(config.infisical)

    all_results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for provider_config in config.providers:
        name = provider_config.name
        print(f"\n=== {name} ===")
        try:
            result = discover_provider(name, config, aa, models_dev, max_workers, store=store)
        except Exception as exc:  # noqa: BLE001 — provider is isolated boundary
            _log_provider_error(name, exc)
            result = provider_error_result(name, exc)
            all_results[name] = result
            save_provider_result(result, name, output_dir)
            continue
        all_results[name] = result
        keep = len(result["keep"])
        drop = len(result["drop"])
        err = len(result.get("error", []))
        print(f"  KEEP: {keep}  DROP: {drop}  ERROR: {err}")
        save_provider_result(result, name, output_dir)
    return all_results


def _log_provider_error(name: str, exc: Exception) -> None:
    """Print a clear, stage-aware error line for a failed provider."""
    stage, detail = classify_provider_error(exc)
    print(f"\n=== {name} ===")
    print(f"ERROR: {stage} failed")
    print(f"      {detail}")


def classify_provider_error(exc: Exception) -> tuple[str, str]:
    """Return a short stage label and a human-readable detail string."""
    msg = str(exc)
    if "Missing API key" in msg or "401" in msg or "403" in msg:
        return ("authentication", msg)
    if "LLM" in msg or "evaluation" in msg.lower():
        return ("evaluation", msg)
    if "404" in msg:
        return ("discovery", f"HTTP 404 — endpoint may not exist. {msg}")
    if "connection" in msg.lower() or "refused" in msg.lower():
        return ("discovery", f"Connection error: {msg}")
    if "timeout" in msg.lower():
        return ("discovery", f"Timeout: {msg}")
    return ("unknown", msg)


_classify_provider_error = classify_provider_error


def provider_error_result(name: str, exc: Exception) -> dict[str, list[dict[str, Any]]]:
    """Return an error-shaped result for a provider that failed entirely."""
    stage, detail = classify_provider_error(exc)
    return {
        "keep": [],
        "drop": [],
        "error": [
            {
                "provider_model_id": name,
                "source": "provider_error",
                "coding": False,
                "canonical_name": None,
                "aa_model_id": None,
                "aa_name": None,
                "aa_slug": None,
                "aa_score": None,
                "confidence": 0.0,
                "decision": "error",
                "tier": "error",
                "stage": stage,
                "evidence": [detail],
            }
        ],
    }


_provider_error_result = provider_error_result


FREE_MARKERS = (":free", "-free", "_free", "/free")


def _is_free_model(model: dict[str, Any] | str, provider_name: str | None = None) -> bool:
    """Return True if model is free (ADR 0004 navy-scoped).

    Generic: id contains any FREE_MARKERS.
    Navy_ai scoped: marker OR premium is False (identity check).
    Missing/None/string premium -> marker-only fallback. Str model -> marker-only.
    """
    if isinstance(model, dict):
        model_id = str(model.get("id", ""))
        is_marker = any(marker in model_id for marker in FREE_MARKERS)
        if is_marker:
            return True
        if provider_name == "navy_ai" and model.get("premium") is False:
            return True
        return False
    model_id = str(model)
    return any(marker in model_id for marker in FREE_MARKERS)


def _has_free_name(models: list[dict[str, Any]], provider_name: str | None = None) -> bool:
    """Return True if any model qualifies as free under provider rule."""
    return any(_is_free_model(m, provider_name) for m in models)


def _split_by_free_rule(
    models: list[dict[str, Any]],
    provider_name: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split models into (keep, dropped) by free-model rule (provider-aware).

    Generic (default): if any id contains a free marker (``:free``, ``-free``, ``_free``),
    only free models kept.
    Navy_ai (provider_name=="navy_ai"): marker OR premium is False.
    Default provider_name="" => generic marker-only, zero regression for others.
    Dropped models must NOT be sent to LLM nor written to YAML.
    """
    # Normalize provider_name for _is_free_model (None vs "" both generic)
    pn = provider_name or None
    if not _has_free_name(models, pn):
        return models, []
    free_models = [m for m in models if _is_free_model(m, pn)]
    non_free = [m for m in models if m not in free_models]
    return free_models, non_free


def _apply_free_model_rule(
    models: list[dict[str, Any]], provider_name: str = ""
) -> list[dict[str, Any]]:
    """Legacy mutating helper — now delegates to _split_by_free_rule.

    Mutates dropped models with ``_deterministic_drop`` / ``_drop_reason`` for
    backward compatibility, but callers should prefer ``_split_by_free_rule``
    which filters BEFORE LLM evaluation.
    """
    free_models, non_free = _split_by_free_rule(models, provider_name)
    if not non_free:
        return models
    reason = f"free-model-rule: all non-free models dropped because {free_models[0]['id'] if free_models else 'a free model'} has a free marker in its id"
    for m in non_free:
        m["_deterministic_drop"] = True
        m["_drop_reason"] = reason
    return models
