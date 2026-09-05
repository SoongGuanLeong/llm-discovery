from __future__ import annotations

import json
import random
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .shim import ALIAS_TIERS, is_alias, pick_model_for_tier


def create_app(
    shim_map: dict[str, list[str]] | None = None,
    *,
    bifrost_url: str = "http://localhost:8080",
    transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    rng: random.Random | None = None,
) -> FastAPI:
    """Create shim sidecar FastAPI app.

    Args:
        shim_map: tier -> list[model_id] (keep-all, no dedup). If None, loads from file.
        bifrost_url: Bifrost gateway base URL (no trailing slash).
        transport: optional httpx transport for testing (MockTransport).
        rng: optional random.Random for deterministic picks in tests.
    """
    if shim_map is None:
        from .shim import load_shim_map

        shim_map = load_shim_map()

    # Normalize - ensure all tiers present
    normalized: dict[str, list[str]] = {t: list(shim_map.get(t, [])) for t in ALIAS_TIERS}

    app = FastAPI(title="llm-discovery shim sidecar")

    # Use supplied rng or new Random()
    app_rng = rng if rng is not None else random.Random()

    @app.get("/health")
    async def health():
        return {"status": "ok", "tiers": {k: len(v) for k, v in normalized.items()}}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            body_bytes = await request.body()
            body: dict[str, Any] = json.loads(body_bytes.decode()) if body_bytes else {}
        except Exception:
            return JSONResponse(status_code=400, content={"error": {"message": "invalid json", "type": "invalid_request_error"}})

        model = body.get("model", "")
        if not isinstance(model, str):
            model = str(model)

        # Alias handling: weighted pick within strict tier
        if is_alias(model):
            picked = pick_model_for_tier(model, normalized, app_rng)
            if picked is None:
                # Strict 503, no fallback to other tier
                return JSONResponse(
                    status_code=503,
                    content={"error": {"message": f"tier_unavailable: {model} pool empty", "type": "tier_unavailable", "code": "tier_unavailable", "tier": model}},
                    headers={"Retry-After": "60"},
                )
            body["model"] = picked
        # else explicit pin: proxy as-is

        # Proxy to Bifrost
        bifrost_path = f"{bifrost_url.rstrip('/')}/v1/chat/completions"

        # Forward headers except host/content-length, preserve content-type
        forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}

        # Use httpx client with optional mock transport
        # For sync TestClient, we need sync httpx.Client; but endpoint is async so use AsyncClient
        # We handle both transport types: if BaseTransport (sync), wrap via Client; if AsyncBaseTransport, use AsyncClient
        # Simplify: try AsyncClient first, fallback to sync
        headers_to_forward = forward_headers
        data = json.dumps(body).encode()

        # Detect transport type by checking if it has async methods
        is_async_transport = transport is not None and hasattr(transport, "handle_async_request")

        if is_async_transport or transport is None:
            async with httpx.AsyncClient(transport=transport) if transport else httpx.AsyncClient() as client:  # type: ignore
                try:
                    upstream = await client.post(bifrost_path, content=data, headers={**headers_to_forward, "content-type": "application/json"})
                except httpx.RequestError as e:
                    return JSONResponse(status_code=502, content={"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
                # Proxy status, headers, body
                # Preserve important headers including retry-after
                resp_headers = {}
                for k, v in upstream.headers.items():
                    lk = k.lower()
                    if lk in ("retry-after", "x-provider", "content-type") or lk.startswith("x-"):
                        resp_headers[k] = v
                # Also preserve retry-after case-insensitive
                if "retry-after" not in {k.lower() for k in resp_headers} and "retry-after" in upstream.headers:
                    resp_headers["retry-after"] = upstream.headers["retry-after"]
                # Return JSON body if possible else raw
                try:
                    content = upstream.json()
                except Exception:
                    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers, media_type=upstream.headers.get("content-type", "application/json"))
                return JSONResponse(status_code=upstream.status_code, content=content, headers=resp_headers)
        else:
            # Sync transport (httpx.MockTransport is sync)
            with httpx.Client(transport=transport) as client:  # type: ignore
                try:
                    upstream = client.post(bifrost_path, content=data, headers={**headers_to_forward, "content-type": "application/json"})
                except httpx.RequestError as e:
                    return JSONResponse(status_code=502, content={"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
                resp_headers = {}
                for k, v in upstream.headers.items():
                    lk = k.lower()
                    if lk in ("retry-after", "content-type") or lk.startswith("x-"):
                        resp_headers[k] = v
                try:
                    content = upstream.json()
                except Exception:
                    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers, media_type=upstream.headers.get("content-type", "application/json"))
                return JSONResponse(status_code=upstream.status_code, content=content, headers=resp_headers)

    return app


def _load_shim_map_from_env() -> dict[str, list[str]]:
    from pathlib import Path
    import json as _json
    import os as _os

    # Prefer explicit path env, else data/bifrost/shim_map.json relative to cwd or project root
    cand = _os.environ.get("SHIM_MAP_PATH", "data/bifrost/shim_map.json")
    p = Path(cand)
    if not p.is_file():
        # Try project root resolve
        p2 = Path(__file__).resolve().parents[3] / "data" / "bifrost" / "shim_map.json"
        if p2.is_file():
            p = p2
    from .shim import load_shim_map

    return load_shim_map(str(p))


if __name__ == "__main__":
    import os
    import uvicorn

    shim_map_cli = _load_shim_map_from_env()
    bifrost_url_cli = os.environ.get("BIFROST_URL", "http://localhost:8080")
    port_cli = int(os.environ.get("SHIM_PORT", "8081"))
    host_cli = os.environ.get("SHIM_HOST", "0.0.0.0")
    app_cli = create_app(shim_map_cli, bifrost_url=bifrost_url_cli)
    uvicorn.run(app_cli, host=host_cli, port=port_cli)
