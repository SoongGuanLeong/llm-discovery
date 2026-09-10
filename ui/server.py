"""FastAPI server for local llm-discovery UI — scaffold (issue #185) + key persist + live providers (issue #186)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import set_key, unset_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="llm-discovery Control")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

REPO_ROOT = Path(__file__).parents[1]
ENV_PATH = REPO_ROOT / ".env"
PROVIDERS_YAML = REPO_ROOT / "config" / "providers.yaml"


def _hint_for_key(key: str) -> str:
    """Return masked hint '***' + last 3-4 chars. Never returns raw key."""
    if not key:
        return ""
    if len(key) <= 3:
        return "***"
    # Use last 3 for short keys, last 4 for longer keys to fit spec "last 3-4"
    if len(key) >= 8:
        return "***" + key[-4:]
    return "***" + key[-3:]


def _config_status() -> dict[str, Any]:
    raw = os.environ.get("OMNIROUTE_API_KEY", "")
    has_key = bool(raw)
    hint = _hint_for_key(raw) if has_key else ""
    return {"hasKey": has_key, "hint": hint}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/providers")
def list_providers():
    try:
        text = PROVIDERS_YAML.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="providers.yaml not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"providers.yaml error: {e}")
    providers = data.get("providers") or []
    names: list[str] = []
    for p in providers:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            names.append(p["name"])
        elif isinstance(p, str):
            names.append(p)
    names = sorted(names)
    return {"providers": names}


@app.get("/api/config/status")
def config_status():
    return _config_status()


class OmniKeyBody(BaseModel):
    key: str = ""


@app.post("/api/config/omniroute-key")
def set_omniroute_key(body: OmniKeyBody):
    raw_in: str = body.key if isinstance(body.key, str) else ""
    # Empty string exactly -> delete/unset key, keep other vars
    if raw_in == "":
        try:
            if ENV_PATH.exists():
                try:
                    unset_key(str(ENV_PATH), "OMNIROUTE_API_KEY")
                except Exception:
                    pass
        except Exception:
            pass
        os.environ.pop("OMNIROUTE_API_KEY", None)
        return {"hasKey": False, "hint": ""}
    trimmed = raw_in.strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail="key required")
    existed_before = ENV_PATH.exists()
    try:
        set_key(str(ENV_PATH), "OMNIROUTE_API_KEY", trimmed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    os.environ["OMNIROUTE_API_KEY"] = trimmed
    try:
        if not existed_before and ENV_PATH.exists():
            ENV_PATH.chmod(0o600)
    except Exception:
        pass
    hint = _hint_for_key(trimmed)
    return {"hasKey": True, "hint": hint}


@app.get("/", include_in_schema=False)
def index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx), media_type="text/html")
    return FileResponse(str(idx), media_type="text/html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
