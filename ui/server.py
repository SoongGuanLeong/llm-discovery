"""FastAPI server for local llm-discovery UI — scaffold (185) + key persist + live providers (186) + SSE jobs + dry-run (187)."""
from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import threading
import os
import signal
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import yaml
from dotenv import set_key, unset_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="llm-discovery Control")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

REPO_ROOT = Path(__file__).parents[1]
ENV_PATH = REPO_ROOT / ".env"
PROVIDERS_YAML = REPO_ROOT / "config" / "providers.yaml"

# --- in-memory jobs (187) ---
jobs: dict[str, dict[str, Any]] = {}

_SECRET_ENV_KEYS = ["OMNIROUTE_API_KEY", "OMNIROUTE_MANAGE_KEY", "OMNIROUTE_TOKEN", "OMNIROUTE_AUTH_TOKEN"]

def _redact(text: str) -> str:
    out = text
    for k in _SECRET_ENV_KEYS:
        v = os.environ.get(k)
        if v and len(v) >= 4 and v in out:
            out = out.replace(v, "***")
    return out

def _make_job_id() -> str:
    return uuid.uuid4().hex[:8]

def _hint_for_key(key: str) -> str:
    """Return masked hint '***' + last 3-4 chars. Never returns raw key."""
    if not key:
        return ""
    if len(key) <= 3:
        return "***"
    if len(key) >= 8:
        return "***" + key[-4:]
    return "***" + key[-3:]

def _config_status() -> dict[str, Any]:
    raw = os.environ.get("OMNIROUTE_API_KEY", "")
    has_key = bool(raw)
    hint = _hint_for_key(raw) if has_key else ""
    return {"hasKey": has_key, "hint": hint}

def _stream_reader_thread(pipe, job_id: str, stype: str):
    if pipe is None:
        return
    while True:
        line_bytes = pipe.readline()
        if not line_bytes:
            break
        try:
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
        except Exception:
            try:
                line = str(line_bytes)
            except Exception:
                line = ""
        line = _redact(line)
        if len(line) > 2000:
            line = line[:2000] + "\u2026"
        entry = {"type": stype, "line": line, "ts": time.time()}
        job = jobs.get(job_id)
        if job is None:
            break
        job["deque"].append(entry)
        q = job.get("_queue")
        if q is not None:
            try:
                q.put_nowait(entry)
            except Exception:
                pass

def _run_job_thread(cmd: list[str], cwd: Path, env: dict[str, str], job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return
    job["status"] = "running"
    job["exitCode"] = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=1,
        )
    except Exception as e:
        job["status"] = "error"
        job["exitCode"] = 1
        entry = {"type": "stderr", "line": _redact(str(e)), "ts": time.time()}
        job["deque"].append(entry)
        q = job.get("_queue")
        if q is not None:
            try:
                q.put_nowait(entry)
                q.put_nowait({"type": "done", "exitCode": 1})
            except Exception:
                pass
        return
    job["proc"] = proc
    t1 = threading.Thread(target=_stream_reader_thread, args=(proc.stdout, job_id, "stdout"), daemon=True)
    t2 = threading.Thread(target=_stream_reader_thread, args=(proc.stderr, job_id, "stderr"), daemon=True)
    t1.start()
    t2.start()
    try:
        exit_code = proc.wait()
    except Exception:
        exit_code = 1
    # wait briefly for readers to drain
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)
    # status handling (respect killed)
    if job.get("status") == "killed":
        job["exitCode"] = exit_code
    elif exit_code == 0:
        job["status"] = "done"
        job["exitCode"] = 0
    else:
        if exit_code is not None and exit_code < 0:
            if job.get("status") != "killed":
                job["status"] = "error"
        else:
            job["status"] = "error" if job.get("status") == "running" else job.get("status")
        job["exitCode"] = exit_code
    q = job.get("_queue")
    if q is not None:
        term = {"type": "killed"} if job.get("status") == "killed" else {"type": "done", "exitCode": job.get("exitCode")}
        try:
            q.put_nowait(term)
        except Exception:
            pass
    def _cleanup():
        time.sleep(300)
        jobs.pop(job_id, None)
    threading.Thread(target=_cleanup, daemon=True).start()

# keep async wrappers for tests that import _spawn_job / _read_stream directly
async def _read_stream(stream, job_id: str, stype: str):
    # shim: not used in thread model, but keep for test imports
    return

async def _spawn_job(cmd: list[str], cwd: Path, env: dict[str, str], job_id: str):
    # run thread version via asyncio to keep test import compatibility
    await asyncio.to_thread(_run_job_thread, cmd, cwd, env, job_id)

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

@app.post("/api/export/dry-run", status_code=201)
async def export_dry_run():
    job_id = _make_job_id()
    jobs[job_id] = {
        "id": job_id,
        "proc": None,
        "deque": deque(maxlen=500),
        "status": "idle",
        "exitCode": None,
        "createdAt": time.time(),
        "_queue": queue.Queue(),
    }
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [sys.executable, "-m", "llm_discovery.omniroute_export", "--dry-run"]
    threading.Thread(target=_run_job_thread, args=(cmd, REPO_ROOT, env, job_id), daemon=True).start()
    return JSONResponse(content={"jobId": job_id}, status_code=201)

@app.get("/api/jobs/{job_id}/logs")
async def job_logs(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    async def event_gen():
        dq: deque = job["deque"]
        for entry in list(dq):
            payload = json.dumps(entry, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        status = job["status"]
        if status in ("done", "error", "killed"):
            if status == "killed":
                yield f"data: {json.dumps({'type': 'killed'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'done', 'exitCode': job.get('exitCode')})}\n\n"
            return
        q = job["_queue"]
        is_async_q = isinstance(q, asyncio.Queue)
        while True:
            st = job["status"]
            if st in ("done", "error", "killed"):
                while True:
                    try:
                        item = q.get_nowait()
                        yield f"data: {json.dumps(item)}\n\n"
                    except Exception:
                        break
                if st == "killed":
                    yield f"data: {json.dumps({'type': 'killed'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'done', 'exitCode': job.get('exitCode')})}\n\n"
                return
            try:
                if is_async_q:
                    item = await asyncio.wait_for(q.get(), timeout=1.0)
                else:
                    item = await asyncio.wait_for(asyncio.to_thread(q.get, True, 1.0), timeout=2.0)
            except queue.Empty:
                continue
            except asyncio.TimeoutError:
                continue
            except Exception:
                # for async queue cancellation or others
                try:
                    if is_async_q:
                        continue
                except Exception:
                    pass
                continue
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("type") in ("done", "killed"):
                return
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    status = job.get("status")
    if status in ("done", "error", "killed"):
        return {"status": status, "exitCode": job.get("exitCode")}
    if status == "killed":
        return {"status": "killed"}
    proc = job.get("proc")
    if proc is None:
        job["status"] = "killed"
        job["exitCode"] = None
        q = job.get("_queue")
        if q is not None:
            try:
                q.put_nowait({"type": "killed"})
            except Exception:
                pass
        return {"status": "killed"}
    # handle both subprocess.Popen (poll) and asyncio subprocess (returncode + wait)
    try:
        rc = proc.returncode if hasattr(proc, "returncode") else None
        if rc is None:
            # for Popen, use poll()
            try:
                rc = proc.poll()
            except Exception:
                rc = None
        if rc is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
    except Exception:
        pass
    for _ in range(20):
        await asyncio.sleep(0.1)
        try:
            rc2 = proc.poll() if hasattr(proc, "poll") else proc.returncode
        except Exception:
            rc2 = getattr(proc, "returncode", None)
        if rc2 is not None:
            break
    else:
        try:
            rc3 = proc.poll() if hasattr(proc, "poll") else getattr(proc, "returncode", None)
        except Exception:
            rc3 = None
        if rc3 is None:
            try:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                if hasattr(proc, "wait"):
                    # Popen.wait with timeout, or asyncio wait
                    try:
                        await asyncio.to_thread(proc.wait, 2.0)  # type: ignore[arg-type]
                    except TypeError:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)  # type: ignore[arg-type]
                    except Exception:
                        pass
            except Exception:
                pass
    job["status"] = "killed"
    try:
        # prefer poll() for Popen
        rc_final = proc.poll() if hasattr(proc, "poll") else None
        if rc_final is None:
            rc_final = getattr(proc, "returncode", None)
        job["exitCode"] = rc_final
    except Exception:
        pass
    q = job.get("_queue")
    if q is not None:
        try:
            q.put_nowait({"type": "killed"})
        except Exception:
            pass
    return {"status": "killed"}

@app.get("/", include_in_schema=False)
def index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx), media_type="text/html")
    return FileResponse(str(idx), media_type="text/html")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")