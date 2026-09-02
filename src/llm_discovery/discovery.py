from typing import Any

import httpx


def _normalize_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize model objects from different provider APIs to a standard shape.

    Each normalized model has at least an ``id`` and ``name`` field, which the
    rest of the pipeline relies on.  Different providers use different field
    names for the model identifier:

    - OpenAI / Groq / Anthropic: ``id``
    - Cohere: ``name``  (no ``id``)
    - Cloudflare: ``name``  (normalized separately)

    This keeps provider-specific quirks in the discovery layer so the
    evaluation pipeline doesn't need to know which provider it's looking at.
    """
    normalized: list[dict[str, Any]] = []
    for m in models:
        model_id = m.get("id") or m.get("name") or m.get("model") or str(m)
        normalized.append(
            {
                "id": model_id,
                "name": m.get("name") or m.get("id") or m.get("display_name") or model_id,
                "object": m.get("object", "model"),
            }
        )
    return normalized


def discover_models(
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Generic model discovery for OpenAI-compatible providers.

    Tries ``data["data"]`` first, then ``data["models"]``.  The result is
    normalized so every model dict has an ``id`` and ``name`` key regardless
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
    - Endpoint: ``GET /accounts/{account_id}/ai/models/search``
    - Response: ``{"result": [...]}``
    - Model objects use ``name`` (not ``id``) as the identifier.

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