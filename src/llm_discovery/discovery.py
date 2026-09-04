from pathlib import Path
from typing import Any

import httpx


def _normalize_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize model objects from different provider APIs to a standard shape.

    Each normalized model has at least an \"id\" and \"name\" field, which the
    rest of the pipeline relies on.  Different providers use different field
    names for the model identifier:

    - OpenAI / Groq / Anthropic: \"id\"
    - Cohere: \"name\"  (no \"id\")
    - Cloudflare: \"name\"  (normalized separately)

    This keeps provider-specific quirks in the discovery layer so the
    evaluation pipeline doesn't need to know which provider it's looking at.
    """
    normalized: list[dict[str, Any]] = []
    for m in models:
        model_id = m.get("id") or m.get("name") or m.get("model") or str(m)
        entry: dict[str, Any] = {
            "id": model_id,
            "name": m.get("name") or m.get("id") or m.get("display_name") or model_id,
            "object": m.get("object", "model"),
        }
        # Preserve premium flag when present (navy_ai: premium==false => free).
        # Omit when absent to distinguish missing vs explicit false (ADR 0004).
        if "premium" in m:
            entry["premium"] = m["premium"]
        normalized.append(entry)
    return normalized


def discover_models(
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Generic model discovery for OpenAI-compatible providers.

    Tries \"data[\"data\"]\" first, then \"data[\"models\"]\".  The result is
    normalized so every model dict has an \"id\" and \"name\" key regardless
    of the provider's response schema.
    """
    url = f"{base_url.rstrip('/')}/models"

    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    if "data" in data:
        models = data["data"]
    elif "models" in data:
        models = data["models"]
    else:
        raise RuntimeError(
            f"Unexpected model discovery response from unknown format: {list(data)}"
        )
    return _normalize_models(models)


def discover_cloudflare_models(
    account_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Discover models from Cloudflare Workers AI.

    Cloudflare's model listing API differs from the OpenAI-compatible shape:
    - Endpoint: \"GET /accounts/{account_id}/ai/models/search\"
    - Response: \"{\"result\": [...]}\"
    - Model objects use \"name\" (not \"id\") as the identifier.

    Results are normalized to the same internal model shape used by the rest
    of the pipeline.
    """
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search"
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    # Cloudflare returns models under 'result', normalize to standard format
    models = data.get("result", [])
    return _normalize_models(models)


# --- NaraRouter true-free filtering (issue #52) ---

NARAROUTER_PLANS_URL = "https://router.bynara.id/api/plans"

# Hard-coded fallback snapshot (2026-09-03) for offline/CI.
NARAROUTER_FREE_SNAPSHOT: set[str] = {
    "agnes-2.0-flash",
    "agnes-2.5-flash",
    "laguna-s-2.1",
    "minimax-m3-free",
    "mistral-large",
    "mistral-medium-3-5",
    "muse-spark-1.2-contributor-free",
    "qwen3.8-27b",
    "stepfun-3.7-flash",
}

# Alias to satisfy spec wording (get_nararouter_free_allowlist fallback param name)
NARAROUTER_FREE_ALLOWLIST_FALLBACK = NARAROUTER_FREE_SNAPSHOT


def get_nararouter_free_allowlist(
    timeout: float = 10,
    fallback: set[str] | None = None,
) -> set[str]:
    """Return the NaraRouter free-plan allowlist.

    Fetches GET https://router.bynara.id/api/plans and extracts
    data.find(p => p.code == "free").models. On any network or
    parse error, returns the hard-coded 2026-09-03 snapshot and
    emits a warning. Never raises for the pipeline.
    """
    _fallback = fallback if fallback is not None else NARAROUTER_FREE_SNAPSHOT
    try:
        response = httpx.get(NARAROUTER_PLANS_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        plans = data.get("data", []) if isinstance(data, dict) else []
        free_entry = next((p for p in plans if p.get("code") == "free"), None)
        if free_entry is None:
            print(f"[nararouter] allowlist: no free plan entry, using fallback snapshot ({len(_fallback)})")
            return set(_fallback)
        models = free_entry.get("models", [])
        allow = set(models) if isinstance(models, list) else set()
        if not allow:
            print(f"[nararouter] allowlist: empty live list, using fallback snapshot ({len(_fallback)})")
            return set(_fallback)
        # Best-effort artifact for audit (optional, never fails run)
        try:
            artifact = Path("data/artifacts/nararouter_plans.json")
            artifact.parent.mkdir(parents=True, exist_ok=True)
            import json as _json

            artifact.write_text(_json.dumps(data, indent=2))
        except Exception:
            pass
        print(f"[nararouter] allowlist source=live api/plans count={len(allow)}")
        return allow
    except Exception as exc:  # noqa: BLE001 — network/parse → fallback, never fail
        print(f"[nararouter] allowlist fetch failed ({exc}), using fallback snapshot count={len(_fallback)}")
        return set(_fallback)


def discover_nararouter_models(
    base_url: str,
    api_key: str,
    allowlist: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Discover NaraRouter models filtered to true-free allowlist.

    Wraps discover_models + allowlist filter. Only ids in the free
    plan's model list proceed to evaluation; paid-gated-free ids
    disappear from the evaluated set. Accepts injected allowlist for
    deterministic offline tests.
    """
    raw = discover_models(base_url, api_key)
    allow = allowlist if allowlist is not None else get_nararouter_free_allowlist()
    filtered = [m for m in raw if m["id"] in allow]
    # Log counts for observability (SRE story)
    source = "injected" if allowlist is not None else "live/fallback"
    print(f"[nararouter] NaraRouter true-free filter: raw {len(raw)} -> true-free {len(filtered)} (allowlist source={source}, allowlist={len(allow)})")
    return filtered
