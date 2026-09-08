from __future__ import annotations

import json
import random
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .shim import ALIAS_TIERS, is_alias, pick_model_for_tier

# Headers stripped before proxying to Bifrost (hop-by-hop + length recalculated)
STRIP_REQUEST_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}


def _forward_headers(request_headers: Any) -> dict[str, str]:
    return {k: v for k, v in request_headers.items() if k.lower() not in STRIP_REQUEST_HEADERS}


def _response_headers(upstream_headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in upstream_headers.items():
        lk = k.lower()
        if lk in ("retry-after", "content-type", "x-provider") or lk.startswith("x-"):
            out[k] = v
    # ensure retry-after case-insensitive preserved
    if "retry-after" not in {k.lower() for k in out} and "retry-after" in upstream_headers:
        out["retry-after"] = upstream_headers["retry-after"]
    return out


def _is_streaming_response(headers: Any) -> bool:
    ct = headers.get("content-type", "") if hasattr(headers, "get") else ""
    # httpx headers case-insensitive, check lower
    if isinstance(ct, str) and "text/event-stream" in ct.lower():
        return True
    return False


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

        forward_headers = _forward_headers(request.headers)
        data = json.dumps(body).encode()

        # Detect transport type
        is_async_transport = transport is not None and hasattr(transport, "handle_async_request")

        if is_async_transport or transport is None:
            async with httpx.AsyncClient(transport=transport) if transport else httpx.AsyncClient() as client:  # type: ignore
                try:
                    upstream = await client.post(bifrost_path, content=data, headers={**forward_headers, "content-type": "application/json"})
                except httpx.RequestError as e:
                    return JSONResponse(status_code=502, content={"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
                resp_headers = _response_headers(upstream.headers)
                # Streaming forwarded: if upstream is event-stream, proxy as StreamingResponse
                if _is_streaming_response(upstream.headers):
                    # Preserve content-type, stream bytes
                    media = upstream.headers.get("content-type", "text/event-stream")

                    async def aiter() -> AsyncGenerator[bytes, None]:
                        # upstream.content already buffered for MockTransport; for real streaming we'd use aiter_bytes
                        # Try aiter_bytes if available (httpx streaming), else yield content splitted
                        try:
                            async for chunk in upstream.aiter_bytes():  # type: ignore
                                yield chunk
                        except Exception:
                            # fallback: yield content in chunks
                            content = upstream.content
                            for i in range(0, len(content), 8192):
                                yield content[i : i + 8192]

                    # If aiter_bytes not available (buffered), just yield content
                    if hasattr(upstream, "aiter_bytes"):
                        return StreamingResponse(aiter(), status_code=upstream.status_code, headers=resp_headers, media_type=media)
                    # fallback buffered streaming
                    content = upstream.content

                    async def buffered_iter():
                        for i in range(0, len(content), 8192):
                            yield content[i : i + 8192]

                    return StreamingResponse(buffered_iter(), status_code=upstream.status_code, headers=resp_headers, media_type=media)
                try:
                    content = upstream.json()
                except Exception:
                    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers, media_type=upstream.headers.get("content-type", "application/json"))
                return JSONResponse(status_code=upstream.status_code, content=content, headers=resp_headers)
        else:
            # Sync transport (httpx.MockTransport is sync)
            with httpx.Client(transport=transport) as client:  # type: ignore
                try:
                    upstream = client.post(bifrost_path, content=data, headers={**forward_headers, "content-type": "application/json"})
                except httpx.RequestError as e:
                    return JSONResponse(status_code=502, content={"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
                resp_headers = _response_headers(upstream.headers)
                if _is_streaming_response(upstream.headers):
                    media = upstream.headers.get("content-type", "text/event-stream")
                    content = upstream.content

                    def sync_iter():
                        for i in range(0, len(content), 8192):
                            yield content[i : i + 8192]

                    return StreamingResponse(sync_iter(), status_code=upstream.status_code, headers=resp_headers, media_type=media)
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
