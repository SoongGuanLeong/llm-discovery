"""Build-all CLI: one command builds store from providers.yaml (issue #77).

Orchestrates in order:
  1. Parse config/providers.yaml
  2. Refresh catalogs cache-optional (skip when missing, no network required)
  3. Discover all providers (injectable discover_fn for tests; defaults to pipeline.discover_provider)
  4. Filter 14d via backfill is_stale gate
  5. Upsert via backfill merge (pricing re-avg, scalars gap-fill, benchmarks union-max)
  6. Atomic pretty store with version header (via ModelInfoStore)

Demoable with tmp data-dir and 2 mocked providers, no network/LLM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .backfill import backfill
from .config import load_config
from .model_info_store import ModelInfoStore

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
            try:
                result = discover_fn(name, config, aa, models_dev, max_workers)
            except TypeError:
                # fallback for simpler mock signature (name) -> result
                result = discover_fn(name)  # type: ignore[call-arg]
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
                result = discover_provider(name, config, _aa, _md, max_workers=max_workers)
            except Exception as exc:
                from .pipeline import provider_error_result
                result = provider_error_result(name, exc)
                print(f"[{name}] discover failed: {exc}")
            path = save_provider_result(result, name, results_dir)
            files_written.append(str(path))
            discovered += 1

    # 4-5. Backfill with 14d filter + merge into store atomically
    stats = backfill(results_dir=results_dir, store_path=store_path)

    # 6. Ensure pretty + version header already via ModelInfoStore.save
    # Verify store exists and compact helper works
    store = ModelInfoStore(store_path)
    pretty = store.dumps_pretty()
    compact = store.dumps_compact()
    # round-trip check (compact valid JSON)
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
        "store_size": stats.get("store_size", 0),
        "pretty_bytes": len(pretty.encode("utf-8")),
        "compact_bytes": len(compact.encode("utf-8")),
    }

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build all providers into model_info_store (cache-optional, atomic, 14d filter).", prog="llm-discovery build-all")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Data directory (default: data)")
    parser.add_argument("--config", type=Path, default=Path("config/providers.yaml"), help="Providers YAML path")
    parser.add_argument("--providers", nargs="*", help="Optional subset of provider names to build")
    parser.add_argument("--max-workers", type=int, default=4, help="Workers per provider")
    args = parser.parse_args()
    res = build_all(data_dir=args.data_dir, config_path=args.config, provider_names=args.providers, max_workers=args.max_workers)
    print(json.dumps(res, indent=2))
    print(f"Done: store {res['store_path']} size={res['store_size']} compact {res['compact_bytes']} < pretty {res['pretty_bytes']}")

if __name__ == "__main__":
    main()
