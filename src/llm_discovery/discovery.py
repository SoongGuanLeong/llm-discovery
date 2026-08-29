from typing import Any

import httpx


def discover_models(
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/models"

    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    return data["data"]
