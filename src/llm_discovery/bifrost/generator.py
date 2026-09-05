from __future__ import annotations

import os
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
        "config_store": {"enabled": False},
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
