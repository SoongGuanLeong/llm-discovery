"""End-to-end discovery pipeline — thin coordinator.

evaluate_model is now <30 lines coordinating four seamed adapters:
  EvidenceCollector.collect(), ModelResolver.resolve(), Judge.evaluate(), PolicyGate.apply()

Other entry points (discover_single, discover_provider, discover_all_providers)
retain isolation but delegate per-model work to evaluate_model.
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Thin coordinator seams — 4 adapters (12 → 4 explicit seam imports)
from .discovery import discover_cloudflare_models, discover_models
from .evidence_collector import EvidenceCollector
from .judge import Judge
from .model_resolver import ModelResolver, resolve_model
from .policy_gate import PolicyGate
from .secrets import load_all_secrets, load_discovery_secrets, load_shared_secrets  # noqa: keep aliases for patch compat


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
) -> dict[str, Any]:
    """Judge one model and apply tiering (thin coordinator, <30 lines).

    Coordinates four adapters. All policy/benchmark/categorize logic lives
    in PolicyGate; evidence in EvidenceCollector; resolution in ModelResolver;
    LLM request + transport in Judge.
    """
    model_id = model["id"]
    print(f"  [evaluate] {model_id}: starting...")
    resolution = resolve_model(model_id, aa, models_dev, cache)
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
) -> dict[str, list[dict[str, Any]]]:
    """T3 path: evaluate every model for a provider in parallel."""
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
            result = discover_provider(name, config, aa, models_dev, max_workers)
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
