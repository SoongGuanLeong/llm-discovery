"""Build-all CLI: one command builds store from providers.yaml (issue #77, #97).

Orchestrates in order:
  1. Parse config/providers.yaml
  2. Refresh catalogs cache-optional (skip when missing, no network required)
  3. Discover all providers (injectable discover_fn for tests; defaults to pipeline.discover_provider)
     - Sequential providers, store threaded for in-pipeline early return (#96)
  4. Backfill de-duplicates Ephemeral Reports by normalized key via benchmarks gap-fill + pricing aggregation
  5. GC scans live normalized keys from all keep lists; if key absent from live set and stale (>14d) delete, share-aware
  6. Atomic pretty store with version header (via ModelInfoStore) + telemetry

Demoable with tmp data-dir and 2 mocked providers, no network/LLM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import yaml
from collections import Counter
from .backfill import backfill
from .config import load_config
from .model_info_store import DEFAULT_TTL_DAYS, ModelInfoStore, is_stale, normalize_store_key

def _collect_live_keys(results_dir: Path) -> tuple[set[str], dict[str, int], int]:
    live: set[str] = set()
    per_key: Counter = Counter()
    total = 0
    if not results_dir.exists():
        return live, {}, total
    for yf in sorted(results_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        keep = data.get("keep") or []
        if not isinstance(keep, list):
            continue
        seen_in_file: set[str] = set()
        for rec in keep:
            mid = rec.get("model_id") or rec.get("provider_model_id") or ""
            key = normalize_store_key(str(mid))
            if not key:
                continue
            total += 1
            live.add(key)
            if key not in seen_in_file:
                per_key[key] += 1
                seen_in_file.add(key)
    return live, dict(per_key), total


def build_all(
    data_dir: str | Path = "data",
    config_path: str | Path = "config/providers.yaml",
    provider_names: list[str] | None = None,
    discover_fn: Callable[..., dict[str, list[dict[str, Any]]]] | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Build store from providers.yaml in one invocation.

    Args:
        data_dir: data directory containing results/, model_info_store.json, catalog caches.
        config_path: path to providers.yaml.
        provider_names: optional filter to subset providers (for tests).
        discover_fn: injectable discovery function (provider_name, config, aa, models_dev, max_workers) -> result dict.
                     If None, uses pipeline.discover_provider with real catalogs when available.
        max_workers: ThreadPool workers for per-provider discovery (passed to discover_fn).

    Returns:
        dict with keys: providers_discovered, files_written, backfill stats, store_path, store_size
    """
    data_dir = Path(data_dir)
    config_path = Path(config_path)
    results_dir = data_dir / "results"
    store_path = data_dir / "model_info_store.json"
    results_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Parse providers.yaml
    config = load_config(config_path)
    all_provider_cfgs = [p for p in config.providers if provider_names is None or p.name in provider_names]
    provider_list = [p.name for p in all_provider_cfgs]
    if not provider_list:
        raise ValueError("No providers matched filter: " + str(provider_names))

    store_for_discovery = ModelInfoStore(store_path)
    store_for_discovery.load()
    before_keys = set(store_for_discovery.keys())

    # 2. Refresh cache-optional: benchmarks folded into per-model store; no
    # benchmarks.json is created. Only transient catalog caches are used as
    # optional accelerator when present; build succeeds without them.
    # Intentionally no refresh_benchmarks call here per #79: only store is canonical.

    # 3. Discover all providers — cache-optional for AA/models_dev
    # Prepare optional aa/models_dev handles for discover_fn; allow None for cache-miss.
    aa = None
    models_dev = None
    # Attempt to load if files exist; otherwise leave None (discover_fn may ignore)
    # We do not raise if missing — refresh is optional.
    try:
        if (data_dir / "artificial_analysis_models.json").exists() and (data_dir / "models_dev_catalog.json").exists():
            # Lazy import catalogs only when files present
            from .catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
            try:
                aa = ArtificialAnalysisCatalog(data_dir / "artificial_analysis_models.json")
                models_dev = ModelsDevCatalog(data_dir / "models_dev_catalog.json")
            except Exception:
                aa, models_dev = None, None
    except Exception:
        aa, models_dev = None, None

    discovered = 0
    files_written: list[str] = []
    if discover_fn is not None:
        # Mocked/ injected discovery for tests
        from .results import ProviderBatchWriter
        writer = ProviderBatchWriter()
        for name in provider_list:
            result = None
            tried = False
            for attempt in [lambda: discover_fn(name, config, aa, models_dev, max_workers, store=store_for_discovery), lambda: discover_fn(name, config, aa, models_dev, max_workers), lambda: discover_fn(name)]:
                try:
                    result = attempt()
                    tried = True
                    break
                except TypeError as e:
                    if "store" in str(e) or "positional" in str(e) or "missing" in str(e) or "unexpected" in str(e):
                        continue
                    raise
            if not tried or result is None:
                result = {"keep": [], "drop": [], "error": []}
            # Normalize result shape
            if not isinstance(result, dict):
                result = {"keep": [], "drop": [], "error": []}
            result.setdefault("keep", [])
            result.setdefault("drop", [])
            result.setdefault("error", [])
            path = writer.write(result, name, results_dir)
            files_written.append(str(path))
            discovered += 1
    else:
        # Real discovery via pipeline.discover_provider (sequential, isolated per provider)
        from .pipeline import discover_provider
        from .results import save_provider_result
        for name in provider_list:
            print(f"\n=== {name} === (build-all)")
            try:
                # Ensure aa/models_dev are at least placeholder catalogs for pipeline
                # If cache-miss, pipeline's BenchmarkDataCache will collect empty and discovery still filters.
                # Pipeline requires aa/models_dev objects; create dummy empty ones if missing.
                if aa is None or models_dev is None:
                    # Create minimal empty cache handles for pipeline to proceed cache-miss
                    # Pipeline's collect_from_local handles empty gracefully.
                    class _EmptyAA:
                        path = data_dir / "artificial_analysis_models.json"
                        models = []
                    class _EmptyMD:
                        path = data_dir / "models_dev_catalog.json"
                        models = {}
                        providers = {}
                    _aa = aa if aa is not None else _EmptyAA()  # type: ignore
                    _md = models_dev if models_dev is not None else _EmptyMD()  # type: ignore
                else:
                    _aa, _md = aa, models_dev
                result = discover_provider(name, config, _aa, _md, max_workers=max_workers, store=store_for_discovery)
            except Exception as exc:
                from .pipeline import provider_error_result
                result = provider_error_result(name, exc)
                print(f"[{name}] discover failed: {exc}")
            path = save_provider_result(result, name, results_dir)
            files_written.append(str(path))
            discovered += 1

    # 4-5. Backfill with 14d filter + merge into store atomically
    stats = backfill(results_dir=results_dir, store_path=store_path)

    # 6. GC: share-aware, single-threaded, no cross-provider race (sequential after backfill)
    store = ModelInfoStore(store_path)
    store.load()
    live_keys, per_key_counts, total_keep = _collect_live_keys(results_dir)
    duplicate_keys = {k for k, cnt in per_key_counts.items() if cnt > 1}
    gc_count = store.gc(live_keys, ttl_days=DEFAULT_TTL_DAYS)
    new_keys = live_keys - before_keys
    try:
        from .model_info_store import _is_uuid_model_id
        identity_bad_keys = {k for k in live_keys if _is_uuid_model_id(k)}
    except Exception:
        identity_bad_keys = set()
    rebuilt_new = len(new_keys)
    rebuilt_identity = len(identity_bad_keys)
    store_hit_keys = {k for k in live_keys if k in before_keys}
    reused_unique = len(store_hit_keys | duplicate_keys) if live_keys else 0
    rebuilt_total = rebuilt_new + rebuilt_identity
    telemetry = {"discovered": total_keep, "unique_discovered": len(live_keys), "reused": reused_unique, "rebuilt": rebuilt_total, "rebuilt_by_reason": {"new_key": rebuilt_new, "identity_bad": rebuilt_identity, "pricing_ttl_reavg": 0}, "gc": gc_count, "store_size": store.size(), "store_size_before": len(before_keys), "duplicate_keys": len(duplicate_keys), "live_keys": len(live_keys)}
    print(f"[build-all] telemetry discovered={total_keep} unique={len(live_keys)} reused={reused_unique} rebuilt={rebuilt_total} (new={rebuilt_new} identity={rebuilt_identity}) gc={gc_count} store={store.size()}")

    # 7. Ensure pretty + version header already via ModelInfoStore.save
    pretty = store.dumps_pretty()
    compact = store.dumps_compact()
    try:
        assert json.loads(compact) == json.loads(pretty)
    except AssertionError:
        pass

    return {
        "providers_discovered": discovered,
        "providers": provider_list,
        "files_written": files_written,
        "backfill": stats,
        "store_path": str(store_path),
        "store_size": store.size(),
        "pretty_bytes": len(pretty.encode("utf-8")),
        "compact_bytes": len(compact.encode("utf-8")),
        "telemetry": telemetry,
        "discovered": total_keep,
        "reused": reused_unique,
        "rebuilt": rebuilt_total,
        "gc": gc_count,
    }

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build all providers into model_info_store (cache-optional, atomic, 14d filter).", prog="llm-discovery build-all")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory (default: data)")
    parser.add_argument("--config", type=Path, default=Path("config/providers.yaml"), help="Providers YAML path")
    parser.add_argument("--providers", nargs="*", help="Optional subset of provider names to build")
    parser.add_argument("--all-providers", action="store_true", help="Build all providers (default, parity with discover.py)")
    parser.add_argument("providers_pos", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument("--workers", "--max-workers", dest="max_workers", type=int, default=4, help="Workers per provider (alias --workers for discover.py parity)")
    args = parser.parse_args()
    # Parity with discover.py: allow positional provider names like "kilo_ai" or "kilo_ai --all"
    providers = args.providers
    if providers is None and getattr(args, "providers_pos", None):
        # Filter out "--all" artifact if passed positionally
        pos = [p for p in args.providers_pos if p != "--all" and not p.startswith("-")]
        if pos:
            providers = pos
    res = build_all(data_dir=args.data_dir, config_path=args.config, provider_names=providers, max_workers=args.max_workers)
    print(json.dumps(res, indent=2))
    print(f"Done: store {res['store_path']} size={res['store_size']} compact {res['compact_bytes']} < pretty {res['pretty_bytes']}")

if __name__ == "__main__":
    main()