"""End-to-end discovery pipeline.

Two entry points share one per-model engine (evaluate_model) so the T1 flash/max
logic lives in exactly one place:

- discover_single  - T2 tracer bullet: ONE provider model -> one record.
- discover_provider - T3 path: every provider model -> {keep, drop, error}.

The LLM judge (LocalLLMEvaluator) is the integration seam: it needs the provider
key + judge key from the user's local Infisical. Unit tests inject a fake
evaluator; the real path is run by the user (see issue #3).

Architecture:
- Evidence is collected DETERMINISTICALLY from local catalogs + web search
- LLM judge receives STRUCTURED evidence and SYNTHESIZES a decision
- Evidence polarity (positive/negative/neutral) is explicitly represented
- "unknown" is a last resort after multiple evidence-gathering attempts
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from .benchmarks import BenchmarkDataCache, build_benchmark_profile, compute_coding_score, has_critical_weakness
from .categorize import categorize_model
from .discovery import discover_models, discover_cloudflare_models
from .evaluation import ModelEvaluationRequest
from .llm import LocalLLMEvaluator
from .provider import resolve_provider
from .resolver import resolve_model
from .results import save_provider_result
from .search import make_searcher
from .secrets import load_discovery_secrets, load_shared_secrets
from .evidence import EvidenceCollector


def evaluate_model(
    model: dict[str, Any],
    provider_name: str,
    aa: ArtificialAnalysisCatalog,
    models_dev: ModelsDevCatalog,
    evaluator: LocalLLMEvaluator,
    min_score: float,
    max_score: float,
    cache: BenchmarkDataCache | None = None,
) -> dict[str, Any]:
    """Judge one model and apply the T1 flash/max tiering.

    Deterministic pre-filtering drops specialised / non-coding models before
    the judge is called.  Judge failures produce "decision: "error"" (not
    "decision: "drop"") so they can be retried or reviewed.

    Returns a record dict with:
      - decision: keep | drop | error
      - tier: max | flash | drop | error
      - aa_score, aa_model_id, aa_name, aa_slug (verified against local catalog)
    """
    model_id = model["id"]
    print(f"  [evaluate] {model_id}: starting...")

    # --- Build benchmark profile from local catalogs ------------------------
    profile = build_benchmark_profile(model_id, provider_name, cache)
    benchmarks_dict = profile.to_dict() if profile.scores else {}
    coding_score, score_confidence, score_reasons = compute_coding_score(profile) if profile.scores else (None, 0.0, ["No benchmark data"])
    has_weakness, weakness_reason = has_critical_weakness(profile) if profile.scores else (False, None)

    if profile.scores:
        print(f"  [evaluate] {model_id}: benchmarks={profile.available_benchmarks()}, coding_score={coding_score}, confidence={score_confidence}")

    # --- Build structured evidence packet ---
    resolution = resolve_model(model_id, aa, models_dev, cache)
    evidence_packet = EvidenceCollector(provider_name).collect(model, cache, models_dev, resolution)

    # --- Deterministic pre-filter: specialised models -----------------------
    if evidence_packet.is_specialized():
        reason = evidence_packet.deterministic_flags[0] if evidence_packet.deterministic_flags else "specialized_model"
        print(f"  [evaluate] {model_id}: DROP (deterministic) - {reason}")
        return _deterministic_drop_record(model_id, reason, cache)

    # --- AA match ---
    aa_match = evidence_packet.aa_match

    # --- Determine if we need additional evidence gathering ---
    # Stage 1: deterministic evidence is already in evidence_packet
    # Stage 2: if evidence is weak, we could trigger web search (handled by LLM evaluator)
    has_strong_evidence = evidence_packet.has_strong_evidence()
    has_negative_evidence = evidence_packet.has_negative_evidence()

    request = ModelEvaluationRequest(
        provider=provider_name,
        model_id=model_id,
        provider_metadata=model,
        aa_match=aa_match,
        benchmarks=benchmarks_dict,
    )

    print(f"  [evaluate] {model_id}: calling LLM judge (evidence: {len(evidence_packet.benchmarks)} benchmarks, strong={has_strong_evidence})...")
    try:
        llm_result = evaluator.evaluate(request, evidence_packet)
    except Exception as exc:  # noqa: BLE001 — judge/transport errors → error, not drop
        print(f"  [evaluate] {model_id}: ERROR - {exc}")
        error_rec = _llm_error_record(model_id, exc)
        error_rec["coding_score"] = coding_score
        error_rec["benchmarks"] = benchmarks_dict
        return error_rec

    # Deterministic AA fields from resolution (already carries aa_model, no second lookup)
    aa_model = resolution.aa_model
    if aa_model is not None:
        aa_model_id = aa_model.get("id")
        aa_name = aa_model.get("name")
        aa_slug = aa_model.get("slug")
        verified_score = _aa_score(aa_model)
    else:
        aa_model_id = None
        aa_name = None
        aa_slug = None
        verified_score = None

    evaluation: dict[str, Any] = {
        "provider_model_id": model_id,
        "source": "llm",
        "coding": llm_result.coding,
        "canonical_name": llm_result.canonical_name,
        "aa_model_id": aa_model_id,
        "aa_name": aa_name,
        "aa_slug": aa_slug,
        "aa_score": verified_score,
        "coding_score": coding_score,
        "benchmarks": benchmarks_dict,
        "confidence": llm_result.confidence,
        "decision": llm_result.decision,  # keep | drop | unknown
        "evidence_level": llm_result.evidence_level,
        "evidence": llm_result.evidence,
        "coding_assessment": llm_result.coding_assessment.model_dump() if llm_result.coding_assessment else None,
    }

    # Critical weakness check: SWE-bench < 20% forces drop
    if has_weakness:
        print(f"  [evaluate] {model_id}: CRITICAL WEAKNESS - {weakness_reason}")
        evaluation["critical_weakness"] = weakness_reason

    # --- Deterministic coding override ---
    # If we have strong benchmark evidence of coding capability, override LLM's coding=False
    # Strong evidence = coding_score >= 35 (coding_min) OR SWE-bench >= 50% OR Terminal-Bench >= 50%
    deterministic_coding = llm_result.coding
    deterministic_coding_reason = None
    if not deterministic_coding and profile.scores:
        # Check coding_score (multi-signal weighted)
        if coding_score is not None and coding_score >= 35.0:
            deterministic_coding = True
            deterministic_coding_reason = f"coding_score={coding_score:.1f} >= 35 (coding_min)"
        # Check SWE-bench specifically
        elif benchmarks_dict.get("swe_bench_verified", {}).get("score", 0) >= 50.0:
            sb_score = benchmarks_dict["swe_bench_verified"]["score"]
            deterministic_coding = True
            deterministic_coding_reason = f"SWE-bench Verified={sb_score:.1f}% >= 50%"
        # Check Terminal-Bench specifically
        elif benchmarks_dict.get("terminal_bench", {}).get("score", 0) >= 50.0:
            tb_score = benchmarks_dict["terminal_bench"]["score"]
            deterministic_coding = True
            deterministic_coding_reason = f"Terminal-Bench={tb_score:.1f}% >= 50%"
        # Check Terminal-Bench 2.1
        elif benchmarks_dict.get("terminal_bench_2_1", {}).get("score", 0) >= 50.0:
            tb_score = benchmarks_dict["terminal_bench_2_1"]["score"]
            deterministic_coding = True
            deterministic_coding_reason = f"Terminal-Bench 2.1={tb_score:.1f}% >= 50%"

    if deterministic_coding != llm_result.coding and deterministic_coding_reason:
        print(f"  [evaluate] {model_id}: OVERRIDE LLM non-coding -> coding (deterministic: {deterministic_coding_reason})")
        evaluation["evidence"] = evaluation.get("evidence", []) + [f"Deterministic override: {deterministic_coding_reason}"]

    tier = categorize_model(
        coding=deterministic_coding,
        aa_score=verified_score,
        min_score=min_score,
        max_score=max_score,
        judge_decision=llm_result.decision,
        model_id=model_id,
        coding_score=coding_score if profile.scores else None,
        has_critical_weakness=has_weakness,
    )
    evaluation["tier"] = tier

    # Python policy: map LLM decision to final decision
    # keep -> keep, drop -> drop, unknown -> drop (insufficient evidence)
    # error preserved
    # HARD GATE: coding=False or tier=drop forces drop regardless of LLM decision
    # BUT: deterministic coding evidence overrides LLM non-coding AND LLM drop
    if not deterministic_coding:
        evaluation["decision"] = "drop"
        evaluation["tier"] = "drop"
        evaluation.setdefault("evidence", []).append("Model assessed as non-coding (LLM + deterministic); forced drop")
    elif tier == "drop":
        evaluation["decision"] = "drop"
        evaluation.setdefault("evidence", []).append("Tier assessment below minimum; forced drop")
    elif deterministic_coding and not llm_result.coding:
        # Deterministic evidence proves coding capability, override LLM non-coding
        evaluation["decision"] = "keep"
        evaluation.setdefault("evidence", []).append("Deterministic evidence overrides LLM assessment")
    elif llm_result.decision == "error":
        evaluation["decision"] = "error"
    elif llm_result.decision == "keep":
        evaluation["decision"] = "keep"
    elif llm_result.decision == "drop":
        evaluation["decision"] = "drop"
    elif llm_result.decision == "unknown":
        evaluation["decision"] = "drop"
        evaluation["tier"] = "drop"
        evaluation.setdefault("evidence", []).append("Insufficient evidence to determine coding quality; defaulted to drop")
    else:
        evaluation["decision"] = "drop"

    print(f"  [evaluate] {model_id}: {evaluation['decision'].upper()} {tier} (coding={llm_result.coding}, coding_score={coding_score}, aa_score={verified_score}, evidence_level={llm_result.evidence_level})")
    return evaluation


def _llm_error_record(model_id: str, exc: Exception, coding_score: float = 0.0, benchmarks: dict = None) -> dict[str, Any]:
    """Judge failure → decision=error, tier=error (NOT drop).

    This ensures failed evaluations are surfaced for retry / manual review
    instead of being silently excluded from the output catalog.
    """
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


def _deterministic_drop_record(model_id: str, reason: str, cache=None) -> dict[str, Any]:
    """Pre-filter drop (specialised / non-coding models, free-model-rule)."""
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
    """The deterministic AA match handed to the judge as verified context.
    
    Returns a dict with matched status, model_id, and score, or None if no match.
    """
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
    aa: ArtificialAnalysisCatalog,
    min_score: float,
) -> dict[str, Any]:
    """Deterministically pick ONE provider model to trace.

    Prefers models that resolve to an AA model whose intelligence index is at or
    above min_score; among those (or, failing that, among all resolvable scored
    models) it picks the highest score, tie-broken by model id ascending. If
    nothing resolves, it returns the first model by id. Selection uses only the
    offline AA catalog, so it is reproducible across runs.
    """
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for model in sorted(models, key=lambda m: m["id"]):
        score = _aa_score(resolve_model(model["id"], aa).aa_model)
        if score is not None:
            scored.append((score, model["id"], model))

    if not scored:
        return sorted(models, key=lambda m: m["id"])[0]

    preferred = [t for t in scored if t[0] >= min_score]
    pool = preferred or scored
    pool.sort(key=lambda t: (-t[0], t[1]))
    return pool[0][2]


def _auto_free_record(provider_name: str) -> dict[str, Any]:
    """Auto-free provider: skip evaluation, return auto:free routing recommendation.

    Some providers (e.g. BazaarLink) act as dynamic free-model routers rather
    than exposing a static catalog.  Evaluating every underlying model is
    pointless and wasteful; instead we store "auto:free" as the selected
    model identifier so downstream consumers know to route requests through
    the provider's own free-model selection.
    """
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
    aa: ArtificialAnalysisCatalog,
    models_dev: ModelsDevCatalog,
) -> dict[str, Any]:
    """T2 tracer bullet: enumerate a provider, evaluate ONE model, return record."""
    provider_config = _resolve_provider_config(provider_name, config)
    provider = resolve_provider(provider_config, models_dev)

    if provider.discovery_strategy == "bazaarlink":
        return _auto_free_record(provider_name)

    load_shared_secrets(config.infisical)
    load_discovery_secrets(config.infisical)

    llm_api_key = os.environ.get(config.judge_llm.secret)
    if not llm_api_key:
        raise RuntimeError(
            f"Missing API key environment variable: {config.judge_llm.secret}"
        )
    api_key = os.environ.get(provider.secret)
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {provider.secret}")

    base_url = os.path.expandvars(provider.base_url)

    if provider.discovery == "cloudflare":
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        models = discover_cloudflare_models(account_id, api_key)
    else:
        models = discover_models(base_url, api_key)

    # Free-model rule: DISABLED - too aggressive, drops all non-free models
    # if any free model exists. Each model should be evaluated on its own merits.
    # models = _apply_free_model_rule(models)
    eval_models = models
    dropped_models = []

    model = pick_tracer_model(eval_models, aa, config.artificial_analysis.min_score)

    # Deterministic facts flow FROM pipeline TO LLM. Web search is enabled by
    # default via DuckDuckGo (no key); set DISABLE_WEB_SEARCH=1 to disable.
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

    # Build benchmark cache
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
    aa: ArtificialAnalysisCatalog,
    models_dev: ModelsDevCatalog,
    max_workers: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """T3 path: evaluate every model for a provider in parallel.

    Judge calls run concurrently via a thread pool (I/O-bound HTTP).  Each
    model evaluation is independent, so ordering is normalised afterwards for
    deterministic output.
    """
    print(f"[{provider_name}] Starting discovery...")
    provider_config = _resolve_provider_config(provider_name, config)
    provider = resolve_provider(provider_config, models_dev)

    if provider.discovery_strategy == "bazaarlink":
        print(f"[{provider_name}] bazaarlink strategy -> auto:free")
        return {
            "keep": [_auto_free_record(provider_name)],
            "drop": [],
            "error": [],
        }

    load_shared_secrets(config.infisical)
    load_discovery_secrets(config.infisical)

    llm_api_key = os.environ.get(config.judge_llm.secret)
    if not llm_api_key:
        raise RuntimeError(
            f"Missing API key environment variable: {config.judge_llm.secret}"
        )
    api_key = os.environ.get(provider.secret)
    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {provider.secret}")

    base_url = os.path.expandvars(provider.base_url)

    try:
        if provider.discovery == "cloudflare":
            account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
            print(f"[{provider_name}] Discovering models via Cloudflare API...")
            models = discover_cloudflare_models(account_id, api_key)
        else:
            print(f"[{provider_name}] Discovering models from {base_url}/models ...")
            models = discover_models(base_url, api_key)
    except Exception as exc:  # noqa: BLE001 — provider-level failure
        print(f"[{provider_name}] Discovery failed: {exc}")
        return _provider_error_result(provider_name, exc)

    print(f"[{provider_name}] Discovered {len(models)} models")

    # Free-model rule: DISABLED - too aggressive
    # free_count = sum(1 for m in models if ":free" in m.get("id", ""))
    # if free_count > 0:
    #     print(f"[{provider_name}] Free-model rule triggered: {free_count} model(s) have ':free' in id")
    # models = _apply_free_model_rule(models)
    eval_models = models
    dropped_models = []

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

    # Build benchmark cache once for all models
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

    # Add deterministically dropped models to drop bucket
    for m in dropped_models:
        record = _deterministic_drop_record(m["id"], m.get("_drop_reason", "free-model-rule"), cache)
        result["drop"].append(record)
        print(f"[{provider_name}] DROP (deterministic) {m['id']} - {m.get('_drop_reason', 'free-model-rule')}")

    # Deterministic ordering for idempotent output.
    for bucket in result.values():
        bucket.sort(key=lambda r: r["provider_model_id"])

    print(f"[{provider_name}] Done: KEEP={len(result['keep'])} DROP={len(result['drop'])} ERROR={len(result['error'])}")
    return result


def discover_all_providers(
    config: Any,
    aa: ArtificialAnalysisCatalog,
    models_dev: ModelsDevCatalog,
    max_workers: int = 4,
    output_dir: Path = Path("data/results"),
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """T3 path for every configured provider.

    Each provider is an isolated failure boundary: a broken endpoint
    (e.g. HTTP 404) for one provider must never abort the run for the
    others.  Failed providers are recorded with stage + error details
    instead of being silently swallowed.

    Each provider's YAML file is saved immediately after evaluation
    completes so results are visible progressively — not only after
    the entire batch finishes.  A progress summary line is printed
    for each provider.

    Returns "{provider_name: {"keep": [...], "drop": [...], "error": [...]}}"
    or "{"status": "error", "stage": "discovery", "error": {...}}" for
    providers that failed before evaluation could start.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    all_results: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for provider_config in config.providers:
        name = provider_config.name
        print(f"\n=== {name} ===")
        try:
            result = discover_provider(
                name, config, aa, models_dev, max_workers
            )
        except Exception as exc:  # noqa: BLE001 — provider is isolated boundary
            _log_provider_error(name, exc)
            result = _provider_error_result(name, exc)
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
    stage, detail = _classify_provider_error(exc)
    print(f"\n=== {name} ===")
    print(f"ERROR: {stage} failed")
    print(f"      {detail}")


def _classify_provider_error(exc: Exception) -> tuple[str, str]:
    """Return a short stage label and a human-readable detail string.

    The stage tells the user *where* the failure occurred:
    "discovery"  — HTTP error or transport issue during model listing
    "authentication" — missing or invalid API key
    "evaluation"  — the LLM judge failed (empty response, bad JSON, etc.)
    "unknown"     — everything else
    """
    msg = str(exc)
    # Authentication / missing key.
    if "Missing API key" in msg or "401" in msg or "403" in msg:
        return ("authentication", msg)
    # The LLM judge failed during evaluation (check first — timeout
    # inside an evaluation message should still be evaluation).
    if "LLM" in msg or "evaluation" in msg.lower():
        return ("evaluation", msg)
    # HTTP-level failures during model discovery.
    if "404" in msg:
        return ("discovery", f"HTTP 404 — endpoint may not exist. {msg}")
    if "connection" in msg.lower() or "refused" in msg.lower():
        return ("discovery", f"Connection error: {msg}")
    if "timeout" in msg.lower():
        return ("discovery", f"Timeout: {msg}")
    return ("unknown", msg)


def _provider_error_result(name: str, exc: Exception) -> dict[str, list[dict[str, Any]]]:
    """Return an error-shaped result for a provider that failed entirely."""
    stage, detail = _classify_provider_error(exc)
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


def _has_free_name(models: list[dict[str, Any]]) -> bool:
    """Return True if any model id contains the literal substring ':free'."""
    return any(":free" in m.get("id", "") for m in models)


def _apply_free_model_rule(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop every model that does NOT have ':free' in its id if at least one
    model DOES have ':free' in its id.  The free model(s) themselves keep
    their normal evaluation path.

    This is a deterministic local filter (no LLM).
    """
    if not _has_free_name(models):
        return models
    free_models = [m for m in models if ":free" in m.get("id", "")]
    non_free = [m for m in models if m not in free_models]
    reasons = [f"free-model-rule: all non-free models dropped because {free_models[0]['id'] if free_models else 'a free model'} has ':free' in its id"]
    for m in non_free:
        m["_deterministic_drop"] = True
        m["_drop_reason"] = ";".join(reasons)
    return models
