from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

# Tier tokens as defined in CONTEXT.md / categorize.py
TIER_FLASH = "flash"
TIER_MAX = "max"
TIER_CONTRIBUTOR_FREE = "contributor_free"
ALL_TIERS = [TIER_FLASH, TIER_MAX, TIER_CONTRIBUTOR_FREE]

CLOUDFLARE_PROVIDER = "cloudflare"
CLOUDFLARE_API_KEY_VAR = "CLOUDFLARE_API_KEY"
CLOUDFLARE_ACCOUNT_ID_VAR = "CLOUDFLARE_ACCOUNT_ID"


def _is_contributor_model(model_id: str) -> bool:
    return "contributor" in model_id.lower()


def _normalize_available_env(available_env: set[str] | dict[str, str] | None) -> set[str]:
    if available_env is None:
        # fallback to real environment keys
        return set(os.environ.keys())
    if isinstance(available_env, dict):
        # dict mapping var -> value; treat present if value truthy or key exists
        # If dict values are bool/str, consider key present if value not None and not False and not ""
        present = set()
        for k, v in available_env.items():
            if v is None:
                continue
            if isinstance(v, bool):
                if v:
                    present.add(k)
            elif isinstance(v, str):
                if v != "":
                    present.add(k)
            else:
                present.add(k)
        # also consider keys with truthy values; if caller passed {var: True} style
        # above already handles
        return present
    # set
    return set(available_env)


def _provider_catalog_to_dict(catalog: Any) -> dict[str, dict[str, str]]:
    """Normalize provider catalog input to {name: {base_url, secret}}."""
    result: dict[str, dict[str, str]] = {}
    if catalog is None:
        return result
    # list of ProviderConfig or dicts
    if isinstance(catalog, dict):
        # {name: {base_url, secret}} or {name: ProviderConfig}
        for name, info in catalog.items():
            if hasattr(info, "base_url") and hasattr(info, "secret"):
                result[name] = {"base_url": getattr(info, "base_url") or "", "secret": getattr(info, "secret") or ""}
            elif isinstance(info, dict):
                result[name] = {"base_url": info.get("base_url", ""), "secret": info.get("secret", "")}
            else:
                result[name] = {"base_url": "", "secret": str(info)}
        return result
    # list
    for item in catalog:
        if hasattr(item, "name") and hasattr(item, "secret"):
            name = getattr(item, "name")
            base_url = getattr(item, "base_url", "") or ""
            secret = getattr(item, "secret", "") or ""
            result[name] = {"base_url": base_url, "secret": secret}
        elif isinstance(item, dict):
            name = item.get("name")
            if not name:
                continue
            result[name] = {"base_url": item.get("base_url", ""), "secret": item.get("secret", "")}
    return result


def group_keeps_by_tier(
    keeps: list[dict[str, Any]],
    *,
    strict_contributor_free: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Group keeps by their pre-categorized tier without recomputing.

    Keep-all: no dedup by normalized key, all provider variants retained.
    Strict contributor_free: only keeps where model_id contains "contributor"
    (case-insensitive) are kept in contributor_free tier; other tiers untouched.
    """
    grouped: dict[str, list[dict[str, Any]]] = {t: [] for t in ALL_TIERS}
    for rec in keeps:
        tier = rec.get("tier")
        # Normalize contributor_special legacy
        if tier == "contributor_special":
            tier = TIER_CONTRIBUTOR_FREE
        if tier not in ALL_TIERS:
            # Ignore non-tier keeps (drop, uncertain, error) – not part of pools
            continue
        if tier == TIER_CONTRIBUTOR_FREE and strict_contributor_free:
            mid = str(rec.get("model_id", ""))
            if not _is_contributor_model(mid):
                # strict filter: drop zero-price broadening etc.
                continue
        grouped[tier].append(rec)
    return grouped


def generate_bifrost_config(
    keeps: list[dict[str, Any]],
    provider_catalog: Any,
    available_env: set[str] | dict[str, str] | None = None,
    *,
    strict_contributor_free: bool = True,
) -> dict[str, Any]:
    """Pure transform: YAML keeps + provider catalog -> Bifrost config + shim map.

    Args:
        keeps: list of keep records, each with provider, model_id, tier.
               Tier is respected as-is (no recomputation), keep-all preserved.
        provider_catalog: list[ProviderConfig] or dict name->{base_url, secret}
        available_env: set of env var names present, or dict var->value,
                       or None to use os.environ. Missing secret -> provider skipped.
        strict_contributor_free: if True, contributor_free tier filtered to only
                                 substring-matched model_ids.

    Returns:
        dict with keys:
          - config: Bifrost config.json dict (version 2, file-only)
          - shim_map: {tier: [model_id, ...]} preserving all variants per tier
          - skipped: list of skipped provider names (missing env / cloudflare gate)
          - tier_counts: {tier: count}
          - empty_tiers: [tier] where count==0 (503 signal, no fallback)
    """
    catalog = _provider_catalog_to_dict(provider_catalog)
    env_present = _normalize_available_env(available_env)

    grouped = group_keeps_by_tier(keeps, strict_contributor_free=strict_contributor_free)

    # Build provider -> model_ids (explicit allowlist) preserving duplicates? Within a provider
    # model_ids are unique, but we keep list order and allow duplicates if they exist.
    provider_to_models: dict[str, list[str]] = {}
    # Also shim_map tier->list[model_id] preserving all variants (including duplicate strings across providers)
    shim_map: dict[str, list[str]] = {t: [] for t in ALL_TIERS}

    for tier in ALL_TIERS:
        for rec in grouped[tier]:
            prov = str(rec.get("provider", ""))
            mid = str(rec.get("model_id", ""))
            if not prov or not mid:
                continue
            shim_map[tier].append(mid)
            provider_to_models.setdefault(prov, []).append(mid)

    # Determine skipped providers
    skipped: list[str] = []
    # Build config providers block only for catalog providers that have env and (optional) keeps
    providers_block: dict[str, Any] = {}

    for prov_name, info in catalog.items():
        secret = info.get("secret", "")
        base_url = info.get("base_url", "")
        # Cloudflare dual-var gate
        if prov_name == CLOUDFLARE_PROVIDER:
            need = {CLOUDFLARE_API_KEY_VAR, CLOUDFLARE_ACCOUNT_ID_VAR}
            # secret should be CLOUDFLARE_API_KEY, but check both vars
            if not need.issubset(env_present):
                skipped.append(prov_name)
                continue
        else:
            if secret and secret not in env_present:
                skipped.append(prov_name)
                continue
            # If secret empty, treat as missing -> skip? but some providers may have empty secret in catalog snapshot;
            # we skip only if secret truthy and not present. If secret empty, we cannot create env.VAR ref, so skip.
            if not secret:
                skipped.append(prov_name)
                continue

        models = provider_to_models.get(prov_name, [])
        # If provider has no keeps, skip emitting (no allowlist). Still not "skipped" due to missing key, but omitted.
        # We emit only if provider has at least one keep; otherwise omit to keep config clean.
        # However, if provider has keeps but they were filtered (e.g., contributor_free strict), models may be non-empty.
        if not models:
            # No keeps for this provider -> do not emit provider entry (implicit skip, not logged as missing-key)
            # But if caller wants empty-tier detection, provider with no keeps should simply be absent.
            continue

        # Explicit allowlist, deduplicate within provider while preserving order (keep-all is across providers, not within)
        # If same model_id appears twice for same provider (unlikely), keep first occurrence.
        seen = set()
        deduped: list[str] = []
        for m in models:
            if m not in seen:
                deduped.append(m)
                seen.add(m)

        providers_block[prov_name] = {
            "keys": [
                {
                    "name": f"{prov_name}-key-1",
                    "value": f"env.{secret}",
                    "models": deduped,
                    "weight": 1.0,
                }
            ],
            "network_config": {
                "base_url": base_url,
                "max_retries": 3,
                "retry_backoff_initial": 500,
                "retry_backoff_max": 5000,
            },
            "custom_provider_config": {
                "base_provider_type": "openai"
            },
        }

    # Handle keeps for providers not in catalog (unknown provider) -> cannot emit config; treat as skipped
    for prov in list(provider_to_models.keys()):
        if prov not in catalog:
            if prov not in skipped:
                skipped.append(prov)

    # Filter shim_map to only include keeps from emitted providers (so empty-tier reflects routable capacity)
    emitted = set(providers_block.keys())
    shim_filtered: dict[str, list[str]] = {t: [] for t in ALL_TIERS}
    for tier in ALL_TIERS:
        for rec in grouped[tier]:
            prov = str(rec.get("provider", ""))
            mid = str(rec.get("model_id", ""))
            if not prov or not mid:
                continue
            if prov in emitted:
                shim_filtered[tier].append(mid)

    tier_counts = {t: len(shim_filtered[t]) for t in ALL_TIERS}
    empty_tiers = [t for t, c in tier_counts.items() if c == 0]

    config = {
        "version": 2,
        "config_store": {"enabled": True, "type": "sqlite", "config": {"path": "/app/data/config.db"}},
        "providers": providers_block,
    }

    return {
        "config": config,
        "shim_map": shim_filtered,
        "skipped": sorted(skipped),
        "tier_counts": tier_counts,
        "empty_tiers": empty_tiers,
    }


def load_keeps_from_results_dir(
    results_dir: Path | str = Path("data/results"),
    *,
    strict_contributor_free: bool = True,
) -> list[dict[str, Any]]:
    """Read data/results/*.yaml and return list of keep records (provider, model_id, tier)."""
    results_dir = Path(results_dir)
    keeps: list[dict[str, Any]] = []
    if not results_dir.exists():
        return keeps
    for yf in sorted(results_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        provider = data.get("provider") or yf.stem
        for rec in data.get("keep", []) or []:
            if not isinstance(rec, dict):
                continue
            tier = rec.get("tier")
            if tier == "contributor_special":
                tier = TIER_CONTRIBUTOR_FREE
            # Respect pre-categorized tier, no recomputation
            keeps.append(
                {
                    "provider": provider,
                    "model_id": rec.get("model_id", ""),
                    "tier": tier,
                    "pricing": rec.get("pricing"),
                }
            )
    # Apply strict contributor_free filtering at load time as well? No, keep raw and let group handle.
    return keeps


# ---------------------------------------------------------------------------
# Drift detection: file (config.json + shim_map.json) vs live DB / API + mtimes
# ---------------------------------------------------------------------------

def _file_bifrost_state(output_dir: Path) -> dict[str, Any] | None:
    """Read existing file artifacts and return provider/model counts + mtimes."""
    output_dir = Path(output_dir)
    config_path = output_dir / "config.json"
    shim_path = output_dir / "shim_map.json"
    if not config_path.exists():
        return None
    try:
        cfg = json.loads(config_path.read_text())
    except Exception:
        return None
    providers = cfg.get("providers", {}) or {}
    file_providers = len(providers)
    file_models = 0
    for pdata in providers.values():
        for k in pdata.get("keys", []) or []:
            models = k.get("models", []) or []
            file_models += len(models)
    # Shim tier_counts as cross-check
    tier_counts: dict[str, int] | None = None
    tier_total: int | None = None
    if shim_path.exists():
        try:
            shim = json.loads(shim_path.read_text())
            tier_counts = {t: len(shim.get(t, []) or []) for t in ALL_TIERS if t in shim}
            tier_total = sum(tier_counts.values()) if tier_counts else None
        except Exception:
            tier_counts = None
    # mtime: max of config + shim (freshest file = truth)
    mtimes = []
    for p in (config_path, shim_path):
        if p.exists():
            try:
                mtimes.append(p.stat().st_mtime)
            except Exception:
                pass
    file_mtime = max(mtimes) if mtimes else None
    return {
        "providers": file_providers,
        "models": file_models,
        "tier_counts": tier_counts,
        "tier_total": tier_total,
        "mtime": file_mtime,
        "config_path": config_path,
        "shim_path": shim_path,
    }


def _db_bifrost_state(db_path: Path) -> dict[str, Any] | None:
    """Read live config.db counts + mtime, or None if DB absent/unreadable."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    try:
        mtime = db_path.stat().st_mtime
        # Include WAL mtime if newer (sqlite WAL holds freshest data)
        wal = db_path.with_suffix(db_path.suffix + "-wal")
        # config.db-wal variant
        wal2 = db_path.parent / (db_path.name + "-wal")
        for cand in (wal, wal2):
            if cand.exists():
                try:
                    wt = cand.stat().st_mtime
                    if wt > mtime:
                        mtime = wt
                except Exception:
                    pass
        # alt naming: config.db-wal already covered; also check -wal directly
        # Query provider / model counts read-only
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            cur = conn.cursor()
            # config_providers is the canonical table (Bifrost)
            try:
                cur.execute("SELECT COUNT(*) FROM config_providers")
                prov_count = cur.fetchone()[0]
            except Exception:
                prov_count = 0
            try:
                cur.execute("SELECT models_json FROM config_keys")
                model_total = 0
                for (mj,) in cur.fetchall():
                    if mj is None:
                        continue
                    try:
                        arr = json.loads(mj)
                        if isinstance(arr, list):
                            model_total += len(arr)
                    except Exception:
                        continue
            except Exception:
                model_total = 0
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {
            "providers": prov_count,
            "models": model_total,
            "mtime": mtime,
            "path": db_path,
        }
    except Exception:
        return None


def _api_bifrost_state(api_base: str = "http://127.0.0.1:8080", timeout: float = 1.5) -> dict[str, Any]:
    """Try management API counts. Returns dict with reachable flag."""
    base = api_base.rstrip("/")
    result: dict[str, Any] = {"reachable": False, "providers": None, "models": None, "error": None}
    # /api/providers
    try:
        url = f"{base}/api/providers"
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            # Normalize: could be {providers: [...]}, or list
            if isinstance(data, dict):
                # Try common keys
                if "providers" in data and isinstance(data["providers"], list):
                    result["providers"] = len(data["providers"])
                elif "data" in data and isinstance(data["data"], list):
                    result["providers"] = len(data["data"])
                elif "count" in data and isinstance(data["count"], int):
                    result["providers"] = data["count"]
                else:
                    # Fallback: count keys if dict-of-providers
                    result["providers"] = len(data)
            elif isinstance(data, list):
                result["providers"] = len(data)
            result["reachable"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    # /api/models?limit=1000
    try:
        url = f"{base}/api/models?limit=1000"
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                if "total" in data and isinstance(data["total"], int):
                    result["models"] = int(data["total"])
                elif "models" in data and isinstance(data["models"], list):
                    result["models"] = len(data["models"])
                elif "data" in data and isinstance(data["data"], list):
                    result["models"] = len(data["data"])
                elif "count" in data and isinstance(data["count"], int):
                    result["models"] = int(data["count"])
                else:
                    result["models"] = None
                result["reachable"] = True
            elif isinstance(data, list):
                result["models"] = len(data)
                result["reachable"] = True
    except Exception as exc:  # noqa: BLE001
        # Keep providers reachable flag if already true
        if result.get("error") is None:
            result["error"] = str(exc)
    return result


def check_bifrost_drift(
    output_dir: Path | str = Path("data/bifrost"),
    *,
    db_path: Path | str | None = None,
    api_base: str = "http://127.0.0.1:8080",
    api_timeout: float = 1.5,
    check_api: bool = True,
) -> dict[str, Any]:
    """Compare file vs live DB/API + mtimes; report drift.

    Drift if: provider or model counts differ between file and DB (or API when
    reachable), or file mtime newer than DB mtime (stale DB).
    Missing file or missing DB => no drift (fresh install) but reported.

    Returns dict with keys: drift (bool), reasons (list[str]), file/db/api subdicts.
    """
    output_dir = Path(output_dir)
    if db_path is None:
        db_path = output_dir / "config.db"
    else:
        db_path = Path(db_path)

    file_state = _file_bifrost_state(output_dir)
    db_state = _db_bifrost_state(db_path)
    api_state: dict[str, Any] | None = None
    if check_api:
        api_state = _api_bifrost_state(api_base, timeout=api_timeout)

    reasons: list[str] = []
    drift = False

    if file_state is None and db_state is None:
        # No artifacts yet – not drift, just fresh
        return {
            "drift": False,
            "reasons": ["no file artifacts yet (fresh install)"],
            "file": None,
            "db": None,
            "api": api_state,
        }
    if file_state is None:
        reasons.append("config.json missing (needs regen)")
        drift = True
        return {"drift": drift, "reasons": reasons, "file": file_state, "db": db_state, "api": api_state}
    if db_state is None:
        # File exists but DB missing – likely not yet started; warn but not fail drift?
        # Treat as not drift for --check (container not yet ingested), but note.
        reasons.append("config.db missing (not yet ingested)")
        # Not drift: file is truth, DB will be created on restart
        return {"drift": False, "reasons": reasons, "file": file_state, "db": db_state, "api": api_state}

    # Both present: compare counts
    if file_state["providers"] != db_state["providers"]:
        reasons.append(f"providers: file={file_state['providers']} vs db={db_state['providers']}")
        drift = True
    if file_state["models"] != db_state["models"]:
        reasons.append(f"models: file={file_state['models']} vs db={db_state['models']}")
        drift = True
    # API comparison when reachable and counts available
    if api_state is not None and api_state.get("reachable"):
        if api_state.get("providers") is not None and file_state["providers"] != api_state["providers"]:
            reasons.append(f"providers: file={file_state['providers']} vs api={api_state['providers']}")
            drift = True
        if api_state.get("models") is not None and file_state["models"] != api_state["models"]:
            reasons.append(f"models: file={file_state['models']} vs api={api_state['models']}")
            drift = True
        # Cross-check DB vs API as extra signal (not file vs file)
        if (
            api_state.get("providers") is not None
            and db_state["providers"] != api_state["providers"]
        ):
            reasons.append(f"providers: db={db_state['providers']} vs api={api_state['providers']}")
            drift = True
        if api_state.get("models") is not None and db_state["models"] != api_state["models"]:
            reasons.append(f"models: db={db_state['models']} vs api={api_state['models']}")
            drift = True
    # mtime: stale DB if file newer (with 2s grace for atomic replace)
    if file_state.get("mtime") is not None and db_state.get("mtime") is not None:
        if file_state["mtime"] > db_state["mtime"] + 2.0:
            reasons.append(f"mtime: file newer than db (stale db, restart needed)")
            drift = True

    return {"drift": drift, "reasons": reasons, "file": file_state, "db": db_state, "api": api_state}
