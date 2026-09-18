"""End-to-end discovery pipeline — thin coordinator.

evaluate_model is now <30 lines coordinating four seamed adapters:
  EvidenceCollector.collect(), ModelResolver.resolve(), Judge.evaluate(), PolicyGate.apply()

Other entry points (discover_single, discover_provider, discover_all_providers)
retain isolation but delegate per-model work to evaluate_model.
"""
import hashlib
import json
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

# Thin coordinator seams — 4 adapters (12 → 4 explicit seam imports)
from .discovery import discover_cloudflare_models, discover_models
from .evidence_collector import EvidenceCollector
from .judge import Judge
from .gate import _is_router_model_id, is_accurate_enough
from .model_info_store import ModelInfoStore
from .model_resolver import ModelResolver, resolve_model
from .categorize import categorize_model
from .policy_gate import PolicyGate
from .secrets import load_all_secrets, load_discovery_secrets, load_shared_secrets  # noqa: keep aliases for patch compat
from .search_throttle import PROBE_CACHE_DEFAULT_PATH, get_search_accounting, make_cached_search

from .evaluator import EvaluatorCoordinator  # Coordinator for evaluate_model (issue #96)

TTL_DAYS = 28  # Record TTL for pricing reuse per CONTEXT / #91


# Cache helpers now canonical in evaluator.py (Ticket 05 contract).
# Pipeline no longer defines duplicates; imports removed because pipeline no longer uses them directly.
# Callers should import from llm_discovery.evaluator.

VISION_CHEAP_THRESHOLD = 1.2
VISION_CODING_SCORE_MIN = 35.0
VISION_AA_CODING_MIN = 45.0
VISION_AA_INTEL_MIN = 55.0
VISION_BENCH_MIN = 50.0

# issue #232: bounded web recovery — at most this many targeted web searches
# per judge evaluation (first-party model card/docs, then benchmark evidence).
JUDGE_MAX_SEARCHES = 2


def _is_vision_only(flags: list[str]) -> bool:
    return EvaluatorCoordinator._is_vision_only(flags)


def _is_vision_free_model(model_id: str, resolution: Any, models_dev: Any) -> bool:
    return EvaluatorCoordinator._is_vision_free_model(model_id, resolution, models_dev)


def _is_cheap_or_free(resolution: Any, model_id: str, models_dev: Any) -> bool:
    return EvaluatorCoordinator._is_cheap_or_free(resolution, model_id, models_dev)


def _is_coding_capable(resolution: Any, cache: Any, model_id: str, provider_name: str) -> bool:
    return EvaluatorCoordinator._is_coding_capable(resolution, cache, model_id, provider_name)


def _build_alternate_judge_route(config: Any) -> Any:
    """issue #233: optional alternate judge route from config.judge_llm.alternate.

    Returns a JudgeRoute (llm.JudgeRoute) or None when no alternate route is
    configured. The alternate secret env var is resolved at build start, like
    the primary judge secret (load_all_secrets already ran).
    """
    from .llm import JudgeRoute

    alt_cfg = getattr(config.judge_llm, "alternate", None)
    if alt_cfg is None:
        return None
    alt_key = os.environ.get(alt_cfg.secret) if alt_cfg.secret else None
    return JudgeRoute(
        base_url=os.path.expandvars(alt_cfg.base_url),
        model=alt_cfg.model,
        api_key=alt_key,
        timeout=getattr(alt_cfg, "timeout", 120) or 120,
    )


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
    catalog_stale: bool = False,
    candidate_store: Any = None,
    force_judge: bool = False,
) -> dict[str, Any]:
    """Judge one model and apply tiering (thin coordinator + cache seam per #96).

    Thin shim that delegates to EvaluatorCoordinator.evaluate().
    The coordinator holds shared state; evaluate() accepts only the per-model input.

    Early return right after resolve_model and before EvidenceCollector when
    store holds strong Keeper (slim v2). Hit = strong-only; moderate/weak = miss.
    Pricing stale (>28d) re-averaged via aggregate_pricing, benchmarks gap-fill only,
    raw provider_model_id preserved verbatim for Ephemeral Report.

    catalog_stale: when True (catalog fetched_at > 28d TTL), cached drop results with
    strong/moderate evidence are reused, but cached keep results are re-evaluated.

    force_judge (issue #242): when True, skip Keeper and Candidate reuse, always
    run fresh deterministic evidence plus LLM judge where pipeline calls for one.
    """
    coord = EvaluatorCoordinator(
        provider_name=provider_name,
        aa=aa,
        models_dev=models_dev,
        evaluator=evaluator,
        min_score=min_score,
        max_score=max_score,
        cache=cache,
        store=store,
        catalog_stale=catalog_stale,
        candidate_store=candidate_store,
        force_judge=force_judge,
    )
    return coord.evaluate(model)
def _llm_error_record(model_id: str, exc: Exception, coding_score: float = 0.0, benchmarks: dict = None) -> dict[str, Any]:
    """Judge failure → decision=error, tier=error (NOT drop). Delegates to EvaluatorCoordinator."""
    return EvaluatorCoordinator(provider_name="", aa=None, models_dev=None, evaluator=None, min_score=24.0, max_score=45.0)._llm_error_record(model_id, exc, coding_score, benchmarks)


def deterministic_drop_record(model_id: str, reason: str, cache=None) -> dict[str, Any]:
    """Pre-filter drop (specialised / non-coding models). Delegates to EvaluatorCoordinator."""
    return EvaluatorCoordinator(provider_name="", aa=None, models_dev=None, evaluator=None, min_score=24.0, max_score=45.0, cache=cache).deterministic_drop_record(model_id, reason, cache)


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
    return EvaluatorCoordinator._aa_score(aa_model)


def _aa_match(resolution: Any) -> dict[str, Any] | None:
    """The deterministic AA match handed to the judge as verified context. Delegates to EvaluatorCoordinator."""
    return EvaluatorCoordinator._aa_match(resolution)


def _aa_candidates(resolution: Any) -> list[dict[str, Any]]:
    """Legacy: deterministic AA match(es) for backward compatibility. Delegates to EvaluatorCoordinator."""
    return EvaluatorCoordinator._aa_candidates(resolution)


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
    """Auto-free provider: skip evaluation, return auto:free routing recommendation. Delegates to EvaluatorCoordinator."""
    return EvaluatorCoordinator(provider_name=provider_name, aa=None, models_dev=None, evaluator=None, min_score=24.0, max_score=45.0)._auto_free_record(provider_name)


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
    judge_secret_name = getattr(config.judge_llm, "secret", None)
    llm_api_key: str | None = None
    if judge_secret_name:
        llm_api_key = os.environ.get(judge_secret_name)  # type: ignore[arg-type]
        if not llm_api_key:
            raise RuntimeError(f"Missing API key environment variable: {judge_secret_name}")
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
        elif provider_name in PROBE_FREE_PROVIDERS:
            # No free via marker/pricing: pay-per-token gateways expose a console
            # pricing endpoint listing truly-free models (tags/ratio==0); try it
            # first since the live probe can't tell free from funded-paid there.
            pricing_split = _split_free_by_pricing_endpoint(base_url, models, provider_name)
            if pricing_split is not None:
                eval_models, dropped_models = pricing_split
            else:
                probed_free, probed_paid = _probe_free_models(base_url, api_key, models, provider_name)
                if probed_paid:
                    eval_models, dropped_models = probed_free, probed_paid
        if dropped_models:
            print(f"[{provider_name}] Free-model filter: dropped {len(dropped_models)} non-free, keeping {len(eval_models)} free")
    # QwenCloud dated dedup: drop dated variant where undated base exists (after free/probe, all discovery branches)
    if provider_name == "qwencloud" and "eval_models" in locals() and eval_models:
        try:
            _keep, _dropped = _dedupe_qwencloud_dated_models(eval_models, provider_name)
            if _dropped:
                eval_models = _keep
                dropped_models = list(dropped_models) + list(_dropped) if "dropped_models" in locals() and dropped_models is not None else list(_dropped)
        except Exception as _e:
            print(f"[{provider_name}] qwencloud dedup failed: {_e}")
    if not eval_models:
        raise RuntimeError(f"No models to evaluate for {provider_name!r} after filtering (all {len(dropped_models)} dropped)")
    model = pick_tracer_model(eval_models, aa, config.artificial_analysis.min_score)
    searcher = make_searcher(
        brave_api_key=os.environ.get("BRAVE_API_KEY"),
        disabled=os.environ.get("DISABLE_WEB_SEARCH") == "1",
    )
    searcher = make_cached_search(*get_search_accounting(), searcher)  # issue #225: budget + canonical cache
    evaluator = LocalLLMEvaluator(
        base_url=config.judge_llm.base_url,
        model=config.judge_llm.model,
        api_key=llm_api_key,
        min_score=config.artificial_analysis.min_score,
        search_web=searcher,
        max_searches=JUDGE_MAX_SEARCHES,  # issue #232: bounded recovery — at most 2 targeted web searches
        timeout=getattr(config.judge_llm, "timeout", 120) or 120,
        alternate=_build_alternate_judge_route(config),
    )
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, models_dev)
    # Ticket 06: per-provider coordinator seam (was pipeline.evaluate_model shim)
    coordinator = EvaluatorCoordinator(
        provider_name=provider_name,
        aa=aa,
        models_dev=models_dev,
        evaluator=evaluator,
        min_score=config.artificial_analysis.min_score,
        max_score=config.artificial_analysis.max_score,
        cache=cache,
        store=None,
    )
    return coordinator.evaluate(model)


def discover_provider(
    provider_name: str,
    config: Any,
    aa: Any,
    models_dev: Any,
    max_workers: int = 4,
    store: ModelInfoStore | None = None,
    catalog_stale: bool = False,
    candidate_store: Any = None,
    force_judge: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """T3 path: evaluate every model for a provider in parallel.

    store optional for in-pipeline cache reuse per #96 (strong-only, TTL 28d).
    When provided, evaluate_model early-returns on hit before LLM.

    catalog_stale: when True, cached drops with strong/moderate evidence are
    reused but cached keeps are re-evaluated (catalog fetched_at > 28d TTL).

    candidate_store (issue #222): optional weak/none Candidate cache (60-90d
    TTL). When provided, weak/none evaluations are cached there and reused on
    identical evidence_hash; results route to the "uncertain" bucket.

    force_judge (issue #242): when True, skip Keeper and Candidate reuse for
    this provider, always run fresh judge where pipeline calls for one.
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
    judge_secret_name = getattr(config.judge_llm, "secret", None)
    llm_api_key: str | None = None
    if judge_secret_name:
        llm_api_key = os.environ.get(judge_secret_name)  # type: ignore[arg-type]
        if not llm_api_key:
            raise RuntimeError(f"Missing API key environment variable: {judge_secret_name}")
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
            elif provider_name in PROBE_FREE_PROVIDERS:
                pricing_split = _split_free_by_pricing_endpoint(base_url, models, provider_name)
                if pricing_split is not None:
                    eval_models, dropped_models = pricing_split
                else:
                    probed_free, probed_paid = _probe_free_models(base_url, api_key, models, provider_name)
                    if probed_paid:
                        eval_models, dropped_models = probed_free, probed_paid
                if dropped_models:
                    print(f"[{provider_name}] Probe free filter: dropped {len(dropped_models)} non-free, keeping {len(eval_models)} free")
    except Exception as exc:  # noqa: BLE001 — provider-level failure
        print(f"[{provider_name}] Discovery failed: {exc}")
        return provider_error_result(provider_name, exc)
    # QwenCloud dated dedup: drop dated variant where undated base exists (after free/probe filtering)
    if provider_name == "qwencloud" and "eval_models" in locals() and eval_models:
        try:
            _keep, _dropped = _dedupe_qwencloud_dated_models(eval_models, provider_name)
            if _dropped:
                eval_models = _keep
                dropped_models = list(dropped_models) + list(_dropped) if "dropped_models" in locals() and dropped_models is not None else list(_dropped)
        except Exception as _e:
            print(f"[{provider_name}] qwencloud dedup failed: {_e}")
    searcher = make_searcher(
        brave_api_key=os.environ.get("BRAVE_API_KEY"),
        disabled=os.environ.get("DISABLE_WEB_SEARCH") == "1",
    )
    searcher = make_cached_search(*get_search_accounting(), searcher)  # issue #225: budget + canonical cache
    evaluator = LocalLLMEvaluator(
        base_url=config.judge_llm.base_url,
        model=config.judge_llm.model,
        api_key=llm_api_key,
        min_score=config.artificial_analysis.min_score,
        search_web=searcher,
        max_searches=JUDGE_MAX_SEARCHES,  # issue #232: bounded recovery — at most 2 targeted web searches
        timeout=getattr(config.judge_llm, "timeout", 120) or 120,
        alternate=_build_alternate_judge_route(config),
    )
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, models_dev)
    key_signals = ("aa_intelligence", "swe_bench_verified", "livecodebench", "humaneval")
    coverage_stats = {sig: sum(1 for e in cache._data.values() if sig in e.get("benchmarks", {})) for sig in key_signals}
    print(f"[{provider_name}] Benchmark cache: {len(cache._data)} models | coverage: {coverage_stats}")
    print(f"[{provider_name}] Evaluating {len(eval_models)} model(s) with {max_workers} worker(s)...")
    # Ticket 06: construct EvaluatorCoordinator per provider and reuse for all models
    coordinator = EvaluatorCoordinator(
        provider_name=provider_name,
        aa=aa,
        models_dev=models_dev,
        evaluator=evaluator,
        min_score=config.artificial_analysis.min_score,
        max_score=config.artificial_analysis.max_score,
        cache=cache,
        store=store,
        catalog_stale=catalog_stale,
        candidate_store=candidate_store,
        force_judge=force_judge,
    )
    # issue #220/#222: uncertain bucket for weak/none candidates (gate-failed keeps)
    result: dict[str, list[dict[str, Any]]] = {"keep": [], "drop": [], "uncertain": [], "error": []}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:

        def _evaluate_wrapper(m: dict[str, Any]):
            try:
                return coordinator.evaluate(m)
            except Exception as exc:  # noqa: BLE001 — thread boundary, map to error record
                return coordinator._llm_error_record(m["id"], exc)

        future_to_model = {
            pool.submit(_evaluate_wrapper, model): model
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
            elif decision == "uncertain":
                # issue #222: weak/none candidates are distinct from drops (no-evidence, cached 60-90d)
                result["uncertain"].append(evaluation)
            else:
                result["error"].append(evaluation)
    # Dropped models are completely omitted: no LLM, no YAML (generic) or paid-gated excluded (nararouter)
    if dropped_models:
        for m in dropped_models:
            reason = m.get("_drop_reason", "free-model-rule" if provider.discovery_strategy != "nararouter" else "paid_gated_free")
            print(f"[{provider_name}] SKIP ({reason}) {m['id']}")
    # --- Sibling heuristic post-pass: promote newer versions without aa_score if older kept exists ---
    # Handles same-batch siblings where older keep not yet in store during gate evaluation
    try:
        import re
        from llm_discovery.model_matching import ModelNormalizer
        from llm_discovery.categorize import categorize_model
        _num_re = re.compile(r"\d+(?:[\.\-]\d+)+")
        def _num(v: str) -> str:
            m = _num_re.search(v or "")
            return m.group(0) if m else ""
        def _ver_tuple(v: str):
            parts = re.split(r"[.\-]", v)
            out=[]
            for p in parts:
                mm=re.match(r"(\d+)", p)
                if mm:
                    out.append(int(mm.group(1)))
            return tuple(out)
        def _base(mid: str) -> str:
            norm = ModelNormalizer.normalize(mid)
            base = _num_re.sub("", norm)
            base = re.sub(r"-+", "-", base).strip("-")
            return base
        # Build set of kept bases+versions
        kept = result.get("keep", [])
        # For each keep with uncertain tier and no aa_score/coding_score, check older kept
        # Also check drops that are uncertain? Actually drops with no aa_score due to uncertain tier could be promoted if sibling exists
        # But spec says newer should be keep if older keep exists, so promote drops with sibling
        # Check all evaluations in keep + drop + uncertain where aa_score is None and coding_score is None/uncertain
        for evaluation in list(result.get("keep", [])) + list(result.get("drop", [])) + list(result.get("uncertain", [])):
            if evaluation.get("aa_score") is not None:
                continue
            if evaluation.get("coding_score") is not None:
                continue
            tier = evaluation.get("tier")
            if tier not in ("uncertain", "drop"):
                continue
            mid = evaluation.get("provider_model_id") or evaluation.get("model_id") or ""
            cur_num = _num(mid if mid else "")
            # try via signature if direct numeric not found
            if not cur_num:
                try:
                    sig = ModelNormalizer.extract_signature(mid)
                    cur_num = _num(sig.version)
                except Exception:
                    continue
            if not cur_num:
                continue
            cur_ver = _ver_tuple(cur_num)
            cur_base = _base(mid)
            promoted = False
            for k in kept:
                if k is evaluation:
                    continue
                kmid = k.get("provider_model_id") or k.get("model_id") or ""
                k_base = _base(kmid)
                if k_base != cur_base:
                    continue
                try:
                    k_sig = ModelNormalizer.extract_signature(kmid)
                    k_num = _num(k_sig.version) or _num(kmid)
                except Exception:
                    k_num = _num(kmid)
                if not k_num:
                    continue
                if _ver_tuple(k_num) < cur_ver:
                    promoted = True
                    break
            # also check store for older kept (covers cross-batch)
            if not promoted and store is not None:
                try:
                    from llm_discovery.policy_gate import _has_older_kept_sibling
                    if _has_older_kept_sibling(mid, store):
                        promoted = True
                except Exception:
                    pass
            if promoted:
                # promote tier to flash via categorize with sibling flag (respects flagship)
                try:
                    new_tier = categorize_model(
                        coding=bool(evaluation.get("coding", True)),
                        aa_score=None,
                        coding_score=None,
                        model_id=mid,
                        has_older_kept_sibling=True,
                    )
                except Exception:
                    new_tier = "flash"
                if tier != new_tier:
                    print(f"[{provider_name}] SIBLING post-promote {mid}: {tier} -> {new_tier} (older kept sibling)")
                    evaluation["tier"] = new_tier
                    # if was drop due to uncertain, flip to keep
                    if evaluation.get("decision") == "drop":
                        evaluation["decision"] = "keep"
                        # move between buckets
                        if evaluation in result.get("drop", []):
                            result["drop"].remove(evaluation)
                            result["keep"].append(evaluation)
                    # append evidence
                    evaluation.setdefault("evidence", []).append(f"Sibling heuristic (post-pass): newer version of kept model, promoted {tier}->{new_tier}")
        # also handle store-only older (if kept not in same batch but in store) - already covered inside loop via store check
    except Exception as e:
        print(f"[{provider_name}] sibling post-pass failed: {e}")
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


# "free/" is the prefix form (apinex: free/claude-opus-4.6); the rest are suffix forms.
FREE_MARKERS = (":free", "-free", "_free", "/free", "free/")


def _is_pricing_free(model: dict[str, Any]) -> bool:
    """Generic pricing==0 free detection (no hardcoded model names).

    Only checks prompt/completion/input/output pricing, not ancillary
    fields like request/image/web_search which are 0 for many paid models
    (e.g. kilo). Covers prompt/input/output/blended and flattened prices.
    """
    pricing = model.get("pricing")
    # Only check relevant pricing keys, not every value in dict
    relevant_keys = ("prompt", "completion", "input", "output", "input_cache_read", "input_cache_write", "price", "prices", "cost", "price_1m_input_tokens", "price_1m_output_tokens", "price_1m_blended_3_to_1", "prompt_price", "completion_price", "input_price", "output_price")
    if isinstance(pricing, dict):
        for k, v in pricing.items():
            if k not in relevant_keys and "prompt" not in k and "completion" not in k and "input" not in k and "output" not in k and "price" not in k:
                continue
            try:
                if float(v) == 0:
                    # Need to ensure it's actually prompt/completion/input/output, not request/image
                    # For kilo, efficient has prompt -1, free has 0, so this distinguishes
                    return True
            except (ValueError, TypeError):
                continue
    for _k in relevant_keys:
        val = model.get(_k)
        if val is not None:
            try:
                if float(val) == 0:
                    return True
            except (ValueError, TypeError):
                continue
        if isinstance(pricing, dict) and _k in pricing:
            try:
                if float(pricing[_k]) == 0:
                    return True
            except (ValueError, TypeError):
                continue
    return False


def _is_access_tier_free(model: dict[str, Any]) -> bool:
    """Check access_tier == free (xkiro: access_tier free vs paid/premium)."""
    tier = model.get("access_tier")
    if isinstance(tier, str) and tier.strip().lower() == "free":
        return True
    return False


def _is_free_model(model: dict[str, Any] | str, provider_name: str | None = None) -> bool:
    """Return True if model is free (provider-aware, no hardcoded model names).

    Generic: id contains any FREE_MARKERS (suffix :free/-free/_free//free or
    prefix free/) OR pricing == 0 OR access_tier == free.
    navy_ai: marker OR premium is False (identity check) OR pricing == 0.
    llm7: tier==turbo OR marker OR pricing == 0.
    agnes: marker OR -flash suffix OR pricing == 0.
    xkiro: no free filtering (keep all) per user request.
    All other providers (bai, bestvirtualgoods, vyceai, nvidia_nim,
    ollama_cloud, etc): marker OR pricing == 0 OR access_tier free — no hardcoded allowlists.
    Missing/None/string premium -> marker/pricing fallback. Str model -> marker-only.
    """
    if provider_name == "xkiro":
        return False
    if isinstance(model, dict):
        # kilo: isFree flag is authoritative when a real model descriptor is given
        if model.get("isFree") is True:
            return True
        model_id = str(model.get("id", ""))
        is_marker = any(marker in model_id for marker in FREE_MARKERS)
        if is_marker:
            return True

        # Provider-specific flags (non-name signals)
        if provider_name == "navy_ai" and model.get("premium") is False:
            return True
        if provider_name == "llm7" and model.get("tier") == "turbo":
            return True
        if provider_name == "agnes" and "-flash" in model_id:
            return True

        # Generic pricing == 0 — applies to ALL providers (replaces hardcoded BVG list and vyceai/xkiro scoping)
        if _is_pricing_free(model):
            return True
        if _is_access_tier_free(model):
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

    Generic (default): if any id contains a free marker OR pricing == 0,
    only free models kept (pricing check replaces hardcoded allowlists).
    navy_ai: marker OR premium is False OR pricing == 0.
    llm7: tier==turbo OR marker OR pricing == 0.
    agnes: -flash suffix OR marker OR pricing == 0.
    xkiro: no filtering per user request (keep all 83).
    All other providers: marker OR pricing == 0 (generic, no hardcoded names).
    Default provider_name="" => generic marker/pricing, zero regression.
    Dropped models must NOT be sent to LLM nor written to YAML — free filter
    always runs before LLM judgement.
    """
    if provider_name == "xkiro":
        return models, []
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


# Providers where /models gives no pricing/access_tier signal but live probe
# via /chat/completions can distinguish free (200/400/429) vs paid (402/403
# deposit/subscription). No hardcoded model names; probe is generic.
# xkiro dropped per user request — no free filtering (keep all 83).
PROBE_FREE_PROVIDERS = {"bai", "bestvirtualgoods", "nvidia_nim", "ollama_cloud", "vyceai"}

# Pay-per-token gateways (e.g. bestvirtualgoods) expose a public console pricing
# endpoint that tags truly-free models (tags=="free" or model_ratio==0). The
# chat-completions probe cannot distinguish free from funded-paid on such
# gateways (both return 200), so this endpoint is consulted first. Generic: any
# host serving the same JSON shape benefits; no model names hardcoded.
PRICING_ENDPOINT_PATH = "/api/pricing"
PRICING_ENDPOINT_TIMEOUT = 10.0
# xkiro explicitly excluded from free filtering
NO_FREE_FILTER_PROVIDERS = {"xkiro"}


PROBE_CACHE_TTL_DAYS = 7

_PROBE_CACHE_MISSING = object()


class ProbeCache:
    """7-day JSON cache of free/paid probe results per (base_url, model_id) (issue #225).

    Store shape: {sha256(base_url + "|" + model_id): {"is_free": true|false|null, "ts": iso}}.
    Load/save are best-effort (warn-only) so a corrupt or missing cache never fails a build.
    A fresh entry is served with ZERO httpx calls; after a real probe the result is stored
    (including None=unknown) so the next build within the TTL skips the probe.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(PROBE_CACHE_DEFAULT_PATH)
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None

    @staticmethod
    def _key(base_url: str, model_id: str) -> str:
        return hashlib.sha256(f"{base_url}|{model_id}".encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            self._data = {}
            try:
                if self.path.exists():
                    raw = json.loads(self.path.read_text())
                    if isinstance(raw, dict):
                        self._data = raw
            except Exception as exc:  # warn-only, never fail the build
                print(f"[probe-cache] WARN: load failed ({self.path}): {exc}; continuing without cache")
        return self._data

    def get_fresh(self, base_url: str, model_id: str, now: datetime | None = None) -> Any:
        """Cached is_free (True/False/None) when a fresh entry exists, else _PROBE_CACHE_MISSING."""
        if now is None:
            now = datetime.now(UTC)
        with self._lock:
            entry = self._load().get(self._key(base_url, model_id))
        if not isinstance(entry, dict):
            return _PROBE_CACHE_MISSING
        ts = entry.get("ts")
        if not isinstance(ts, str):
            return _PROBE_CACHE_MISSING
        try:
            age = now - datetime.fromisoformat(ts)
        except ValueError:
            return _PROBE_CACHE_MISSING
        if age > timedelta(days=PROBE_CACHE_TTL_DAYS):
            return _PROBE_CACHE_MISSING
        return entry.get("is_free")

    def put(self, base_url: str, model_id: str, is_free: bool | None, now: datetime | None = None) -> None:
        ts = (now if now is not None else datetime.now(UTC)).isoformat()
        with self._lock:
            data = self._load()
            data[self._key(base_url, model_id)] = {"is_free": is_free, "ts": ts}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, indent=2))
            except Exception as exc:  # warn-only, never fail the build
                print(f"[probe-cache] WARN: save failed ({self.path}): {exc}; in-memory entry kept")


_default_probe_cache: ProbeCache | None = None
_default_probe_cache_lock = threading.Lock()


def get_default_probe_cache() -> ProbeCache:
    """Process-wide ProbeCache at PROBE_CACHE_DEFAULT_PATH (lazy singleton)."""
    global _default_probe_cache
    with _default_probe_cache_lock:
        if _default_probe_cache is None:
            _default_probe_cache = ProbeCache()
        return _default_probe_cache


def _probe_model_is_free(base_url: str, api_key: str, model_id: str, timeout: float = 8.0, probe_cache: ProbeCache | None = None) -> bool | None:
    """Probe single model via chat completions to infer free vs paid.

    Returns True=free, False=paid, None=unknown.
    Free: 200 success, 429 rate-limit, 400 with max_tokens validation.
    Paid: 402/403 with deposit/subscription, 400 with insufficient balance/quota.
    404/5xx/timeout -> None.

    probe_cache (issue #225): 7-day cache per (base_url, model_id); a fresh entry is
    returned with zero httpx calls, and real probe results (including None) are stored.
    """
    if probe_cache is None:
        probe_cache = get_default_probe_cache()
    cached = probe_cache.get_fresh(base_url, model_id)
    if cached is not _PROBE_CACHE_MISSING:
        return cached
    result = _probe_model_is_free_live(base_url, api_key, model_id, timeout)
    probe_cache.put(base_url, model_id, result)
    return result


def _probe_model_is_free_live(base_url: str, api_key: str, model_id: str, timeout: float = 8.0) -> bool | None:
    """Probe single model via chat completions to infer free vs paid.

    Returns True=free, False=paid, None=unknown.
    Free: 200 success, 429 rate-limit, 400 with max_tokens validation.
    Paid: 402/403 with deposit/subscription, 400 with insufficient balance/quota.
    404/5xx/timeout -> None.
    """
    try:
        import httpx
    except ImportError:
        return None
    try:
        url = base_url.rstrip("/") + "/chat/completions"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            timeout=timeout,
        )
        code = resp.status_code
        text = resp.text.lower() if resp.text else ""
        # Paid billing signals take precedence even on 400
        if any(k in text for k in ("insufficient", "credit", "balance", "quota", "exhausted", "deposit required", "access restricted", "subscription", "activate your plan")):
            return False
        if code in (402, 403):
            return False
        if code == 200:
            return True
        if code == 429:
            return True
        if code == 400:
            # bai free models return 400 max_tokens validation; paid return 400 insufficient balance already handled above
            if "max_tokens" in text:
                return True
            # other 400 with invalid_request but not billing -> treat as free (model exists)
            if "invalid_request" in text and "max_tokens" not in text:
                # could be other validation, but still indicates model accessible
                return True
            return False
        if code == 404:
            return None
        if 200 <= code < 300:
            return True
        if 400 <= code < 500:
            return False
        return None
    except Exception:
        return None


def _fetch_pricing_free_ids(base_url: str, timeout: float = PRICING_ENDPOINT_TIMEOUT) -> set[str] | None:
    """Fetch the console pricing endpoint and return ids of truly-free models.

    Tries the /api/pricing console endpoint on both the API host and the web
    host (base_url host with the 'api.' prefix stripped). A row is free when
    its tags contain 'free' or its model_ratio is 0. Returns None when the
    endpoint is absent or unparsable so callers fall back to the live probe.
    """
    try:
        import httpx
    except ImportError:
        return None
    parsed = urlparse(base_url)
    if not parsed.hostname:
        return None
    hosts = [parsed.hostname]
    if parsed.hostname.startswith("api."):
        hosts.append(parsed.hostname[len("api."):])
    for host in hosts:
        url = f"{parsed.scheme or 'https'}://{host}{PRICING_ENDPOINT_PATH}"
        try:
            resp = httpx.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            payload = resp.json()
        except Exception:
            continue
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            continue
        free_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("model_name") or row.get("id") or row.get("model")
            if not isinstance(name, str) or not name:
                continue
            tags = row.get("tags")
            tags_free = isinstance(tags, str) and "free" in tags.lower()
            tags_free = tags_free or (isinstance(tags, list) and any(isinstance(t, str) and "free" in t.lower() for t in tags))
            ratio = row.get("model_ratio")
            ratio_free = isinstance(ratio, (int, float)) and ratio == 0
            if tags_free or ratio_free:
                free_ids.add(name)
        return free_ids
    return None


def _split_free_by_pricing_endpoint(
    base_url: str,
    models: list[dict[str, Any]],
    provider_name: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Split free vs paid using the console pricing endpoint, before live probe.

    Returns (free, paid) when the endpoint yields a decisive split (at least one
    free and at least one paid model), None otherwise (endpoint missing,
    unparsable, or non-mixed) so callers fall back to the probe.
    """
    free_ids = _fetch_pricing_free_ids(base_url)
    if free_ids is None:
        print(f"[{provider_name}] Pricing endpoint unavailable at {PRICING_ENDPOINT_PATH}, falling back to probe")
        return None
    free = [m for m in models if str(m.get("id", "")) in free_ids]
    paid = [m for m in models if str(m.get("id", "")) not in free_ids]
    if not free or not paid:
        print(f"[{provider_name}] Pricing endpoint non-mixed (free={len(free)} paid={len(paid)}), no filtering")
        return None
    print(f"[{provider_name}] Pricing endpoint filter: {len(free)} free, {len(paid)} paid")
    return free, paid


def _probe_free_models(
    base_url: str,
    api_key: str,
    models: list[dict[str, Any]],
    provider_name: str = "",
    max_workers: int = 8,
    timeout: float = 8.0,
    probe_cache: ProbeCache | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Live probe to split free vs paid when /models gives no signal.

    Concurrent probe via ThreadPool. Returns (free, paid). If probe yields
    no mixed results (all free, all paid, or all unknown), returns (models, [])
    to indicate no filtering (fallback to keep all). Never hardcodes model names.
    """
    if not models:
        return models, []
    # Only probe for providers in allowlist to avoid unnecessary calls for others
    if provider_name not in PROBE_FREE_PROVIDERS:
        return models, []
    print(f"[{provider_name}] Probe free filter: probing {len(models)} models via live /chat/completions ...")
    free: list[dict[str, Any]] = []
    paid: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    def _check(m: dict[str, Any]) -> tuple[dict[str, Any], bool | None]:
        mid = str(m.get("id", ""))
        res = _probe_model_is_free(base_url, api_key, mid, timeout=timeout, probe_cache=probe_cache)
        return m, res

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_check, m): m for m in models}
        for fut in as_completed(futures):
            m, is_free = fut.result()
            if is_free is True:
                free.append(m)
            elif is_free is False:
                paid.append(m)
            else:
                unknown.append(m)
    # If probe gave mixed free/paid, use it; else fallback
    if free and paid:
        # unknown treated as paid (conservative) or keep? For bai timeout case, unknown -> keep as free? But we treat unknown as paid to avoid inflating.
        # However timeout for free model (mimo-v2.5) was unknown, would be misclassified as paid. So treat unknown as free if we have some free already?
        # Instead, keep unknown with free to avoid dropping potentially free models that timed out.
        # But then we might keep too many. For now, put unknown with free.
        free.extend(unknown)
        print(f"[{provider_name}] Probe result: {len(free)} free, {len(paid)} paid (unknown {len(unknown)} treated as free)")
        return free, paid
    if free and not paid and not unknown:
        # all free -> no filtering needed
        print(f"[{provider_name}] Probe: all {len(free)} models appear free, no filtering")
        return models, []
    if not free and paid:
        # all paid -> no free to keep, keep all to avoid dropping everything (fallback)
        print(f"[{provider_name}] Probe: all models appear paid, no free detected, keeping all {len(models)}")
        return models, []
    # ambiguous (e.g. nvidia all 404 unknown) -> fallback
    print(f"[{provider_name}] Probe inconclusive (free={len(free)} paid={len(paid)} unknown={len(unknown)}), keeping all")
    return models, []
# --- QwenCloud dated dedup (provider-specific) ---
_QWENCLOUD_DATE_PATTERNS = [
    re.compile(r"[-_:/](\d{4}-\d{2}-\d{2})\s*$"),
    re.compile(r"[-_:/](\d{4}_\d{2}_\d{2})\s*$"),
    re.compile(r"[-_:/](\d{8})\s*$"),
    re.compile(r"[-_:/](\d{6})\s*$"),
    re.compile(r"[-_:/](\d{4})\s*$"),
]

def _qwencloud_base_without_date(model_id: str) -> str | None:
    if not isinstance(model_id, str) or not model_id:
        return None
    mid = model_id.strip()
    for pat in _QWENCLOUD_DATE_PATTERNS:
        m = pat.search(mid)
        if m:
            base = mid[: m.start()]
            if base and base != mid and re.search(r"[a-zA-Z]", base):
                return base
    return None

def _dedupe_qwencloud_dated_models(
    models: list[dict[str, Any]],
    provider_name: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if provider_name != "qwencloud":
        return models, []
    if not models:
        return models, []
    id_set = {str(m.get("id", "")).strip() for m in models}
    keep: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for m in models:
        mid = str(m.get("id", "")).strip()
        base = _qwencloud_base_without_date(mid)
        if base is not None and base in id_set:
            md = dict(m)
            md["_drop_reason"] = f"qwencloud-dated-dedup: {mid} -> {base} exists"
            dropped.append(md)
            continue
        keep.append(m)
    if dropped:
        print(f"[{provider_name}] QwenCloud dated dedup: dropped {len(dropped)} dated variant(s) where base exists, keeping {len(keep)}")
        for d in dropped:
            print(f"[{provider_name}]   DEDUP DROP {d['id']} -> {_qwencloud_base_without_date(str(d['id']))}")
    return keep, dropped
