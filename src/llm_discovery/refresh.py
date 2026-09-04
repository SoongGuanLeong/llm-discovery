"""Catalog refresh: fetch AA + models.dev + rebuild benchmarks with atomic write + backup."""
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .benchmarks import BenchmarkDataCache
from .catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog

try:
    from .secrets import load_all_secrets
except ImportError:  # allow running without secrets module in minimal env
    load_all_secrets = None  # type: ignore

DATA_DIR = Path("data")
DEFAULT_AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
DEFAULT_MODELS_DEV_URL = "https://models.dev/catalog.json"


def _atomic_write_json(path: Path, data: Any, backup: bool = True):
    """Write JSON atomically: temp file + rename, optionally backup prior file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return backup_path


def _normalize_aa_payload(raw: Any) -> dict[str, Any]:
    """Normalize AA API response to snapshot shape."""
    fetched_at = datetime.now(UTC).isoformat()
    if isinstance(raw, dict) and "models" in raw and isinstance(raw["models"], list):
        out = dict(raw)
        out["fetched_at"] = fetched_at
        out.setdefault("source", "artificial-analysis")
        return out
    if isinstance(raw, list):
        return {
            "source": "artificial-analysis",
            "tier": "free",
            "intelligence_index_version": 4.1,
            "fetched_at": fetched_at,
            "models": raw,
        }
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
        return {
            "source": "artificial-analysis",
            "tier": "free",
            "intelligence_index_version": 4.1,
            "fetched_at": fetched_at,
            "models": raw["data"],
        }
    if isinstance(raw, dict):
        if "models" in raw:
            raw["fetched_at"] = fetched_at
            return raw
        return {
            "source": "artificial-analysis",
            "tier": "free",
            "intelligence_index_version": 4.1,
            "fetched_at": fetched_at,
            "models": [raw],
        }
    raise ValueError(f"Unexpected AA payload shape: {type(raw)}")


def fetch_artificial_analysis(api_key=None, url=DEFAULT_AA_URL, timeout=30):
    if load_all_secrets is not None and api_key is None and not os.getenv("AA_API_KEY"):
        try:
            load_all_secrets()
        except Exception:
            pass
    headers = {"Accept": "application/json"}
    key = api_key or os.getenv("AA_API_KEY") or os.getenv("ARTIFICIAL_ANALYSIS_API_KEY") or os.getenv("ARTIFICIALANALYSIS_API_KEY")
    if key:
        headers["x-api-key"] = key
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    raw = resp.json()
    return _normalize_aa_payload(raw)


def fetch_models_dev(url=DEFAULT_MODELS_DEV_URL, timeout=30):
    headers = {"Accept": "application/json"}
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "models" in data and "providers" in data:
        return data
    if isinstance(data, dict) and data and all(isinstance(v, dict) and "models" in v for v in list(data.values())[:1]):
        models = {}
        providers = {}
        for pid, prov in data.items():
            providers[pid] = {
                "id": prov.get("id", pid),
                "name": prov.get("name", pid),
                "api": prov.get("api"),
                "doc": prov.get("doc"),
                "env": prov.get("env"),
                "npm": prov.get("npm"),
                "models": prov.get("models", {}),
            }
            for mid, m in prov.get("models", {}).items():
                if mid not in models:
                    models[mid] = m
                    models[mid].setdefault("id", mid)
        return {"models": models, "providers": providers}
    return data


def refresh_artificial_analysis(output=DATA_DIR / "artificial_analysis_models.json", api_key=None, url=DEFAULT_AA_URL, backup=True, dry_run=False):
    data = fetch_artificial_analysis(api_key=api_key, url=url)
    if dry_run:
        print(f"[dry-run] AA: would write {len(data.get('models', []))} models to {output}")
        return None
    bp = _atomic_write_json(output, data, backup=backup)
    print(f"AA refreshed: {len(data.get('models', []))} models -> {output}")
    if bp:
        print(f"  backup: {bp}")
    return output


def refresh_models_dev(output=DATA_DIR / "models_dev_catalog.json", url=DEFAULT_MODELS_DEV_URL, backup=True, dry_run=False):
    data = fetch_models_dev(url=url)
    if dry_run:
        print(f"[dry-run] models.dev: would write {len(data.get('models', {}))} models, {len(data.get('providers', {}))} providers to {output}")
        return None
    bp = _atomic_write_json(output, data, backup=backup)
    print(f"models.dev refreshed: {len(data.get('models', {}))} models, {len(data.get('providers', {}))} providers -> {output}")
    if bp:
        print(f"  backup: {bp}")
    return output


def refresh_benchmarks(aa_path=DATA_DIR / "artificial_analysis_models.json", models_dev_path=DATA_DIR / "models_dev_catalog.json", output=DATA_DIR / "benchmarks.json", backup=True, dry_run=False):
    # Cache-optional per #76/#77: succeed without local caches, use only as accelerator.
    # When caches missing, do not create benchmarks.json (per #79).
    aa_exists = Path(aa_path).exists()
    md_exists = Path(models_dev_path).exists()
    if not aa_exists or not md_exists:
        print(f"benchmarks skipped (cache-miss): aa_exists={aa_exists} models_dev_exists={md_exists}")
        return None
    aa = ArtificialAnalysisCatalog(aa_path)
    models_dev = ModelsDevCatalog(models_dev_path)
    cache = BenchmarkDataCache(cache_path=output)
    cache._data = {}
    cache._loaded = True
    cache.collect_from_local(aa, models_dev)
    if dry_run:
        print(f"[dry-run] benchmarks: would write {len(cache._data)} entries to {output}")
        return None
    bp = _atomic_write_json(output, cache._data, backup=backup)
    print(f"benchmarks rebuilt: {len(cache._data)} entries -> {output}")
    if bp:
        print(f"  backup: {bp}")
    return output


def refresh_all(data_dir=DATA_DIR, aa_api_key=None, aa_url=DEFAULT_AA_URL, models_dev_url=DEFAULT_MODELS_DEV_URL, backup=True, dry_run=False, only=None):
    """Refresh all JSON catalogs in order: AA, models.dev, then benchmarks (derived)."""
    if load_all_secrets is not None:
        try:
            load_all_secrets()
        except Exception:
            pass  # explicit env vars still work; 401 hint covers missing key
    data_dir = Path(data_dir)
    only_set = set(only) if only else {"aa", "models_dev", "benchmarks"}
    results = {}
    if "aa" in only_set:
        results["aa"] = refresh_artificial_analysis(output=data_dir / "artificial_analysis_models.json", api_key=aa_api_key, url=aa_url, backup=backup, dry_run=dry_run)
    if "models_dev" in only_set:
        results["models_dev"] = refresh_models_dev(output=data_dir / "models_dev_catalog.json", url=models_dev_url, backup=backup, dry_run=dry_run)
    if "benchmarks" in only_set:
        results["benchmarks"] = refresh_benchmarks(aa_path=data_dir / "artificial_analysis_models.json", models_dev_path=data_dir / "models_dev_catalog.json", output=data_dir / "benchmarks.json", backup=backup, dry_run=dry_run)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Refresh catalog snapshots (AA + models.dev + benchmarks) atomically with backup.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Data directory (default: data)")
    parser.add_argument("--aa-url", default=DEFAULT_AA_URL, help="AA API URL")
    parser.add_argument("--models-dev-url", default=DEFAULT_MODELS_DEV_URL, help="models.dev catalog URL")
    parser.add_argument("--aa-api-key", default=None, help="AA API key (or env AA_API_KEY)")
    parser.add_argument("--no-backup", action="store_true", help="Disable .bak backup")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate but do not write")
    parser.add_argument("--only", nargs="*", choices=["aa", "models_dev", "benchmarks"], help="Only refresh selected catalogs")
    args = parser.parse_args()
    try:
        results = refresh_all(data_dir=args.data_dir, aa_api_key=args.aa_api_key, aa_url=args.aa_url, models_dev_url=args.models_dev_url, backup=not args.no_backup, dry_run=args.dry_run, only=args.only)
        print("Done:", ", ".join(f"{k}={v or 'dry-run'}" for k, v in results.items()))
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        body = e.response.text[:500] if e.response is not None else ""
        print(f"HTTP {status} from {e.request.url if e.request else '?' }\n{body}")
        if status == 401:
            print("AA requires API key: set AA_API_KEY env or --aa-api-key")
        raise SystemExit(1)
    except Exception as e:
        print(f"Refresh failed: {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()