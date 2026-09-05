from __future__ import annotations

import random
from typing import Any

ALIAS_TIERS: set[str] = {"flash", "max", "contributor_free"}


def is_alias(model: str) -> bool:
    return model in ALIAS_TIERS


def pick_model_for_tier(
    tier: str,
    shim_map: dict[str, list[str]],
    rng: random.Random | None = None,
) -> str | None:
    """Uniform weighted pick within strict tier (keep-all pool, no dedup).

    Each entry weight 1.0, duplicates count separately.
    Returns None if tier empty or missing (signals 503).
    """
    pool = shim_map.get(tier)
    if not pool:
        return None
    # filter empty strings just in case
    pool = [m for m in pool if m]
    if not pool:
        return None
    if rng is None:
        rng = random
    # uniform choice — each entry equal weight, keep-all duplicates naturally weighted
    return rng.choice(pool)


def load_shim_map(path: str | Any = "data/bifrost/shim_map.json") -> dict[str, list[str]]:
    """Load shim_map from JSON file, fallback to empty tiers."""
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {t: [] for t in ALIAS_TIERS}
    try:
        data = json.loads(p.read_text())
        # data may be {tier: [ids]} or {"shim_map": {...}}
        if isinstance(data, dict) and "shim_map" in data:
            data = data["shim_map"]
        result: dict[str, list[str]] = {t: [] for t in ALIAS_TIERS}
        for t in ALIAS_TIERS:
            if t in data and isinstance(data[t], list):
                result[t] = [str(x) for x in data[t] if x]
        return result
    except Exception:
        return {t: [] for t in ALIAS_TIERS}
