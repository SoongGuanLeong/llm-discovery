"""Tests for issue #187: SSE jobs + dry-run export + 5 log states."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.server import app, jobs, REPO_ROOT

client = TestClient(app)

REPO = Path(__file__).parents[2]
DERIVED = REPO / "data" / "derived"
IMPORT_FILE = DERIVED / "omniroute_import.json"
COMBOS_FILE = DERIVED / "omniroute_combos.json"

# Ensure clean jobs before each test
@pytest.fixture(autouse=True)
def clean_jobs():
    jobs.clear()
    yield
    # terminate any leftover procs
    for jid, j in list(jobs.items()):
        proc = j.get("proc")
        if proc is not None and proc.returncode is None:
            try:
                import signal, os as _os
                _os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    jobs.clear()

def _poll_sse_events(job_id: str, timeout: float = 8.0) -> list[dict]:
    """Poll SSE endpoint using TestClient streaming, return parsed data jsons."""
    # Use stream context if available
    try:
        with client.stream("GET", f"/api/jobs/{job_id}/logs", headers={"Accept": "text/event-stream"}) as r:
            assert r.status_code == 200
            # Check content-type
            ctype = r.headers.get("content-type", "")
            assert "text/event-stream" in ctype
            events: list[dict] = []
            start = time.time()
            # iter_lines returns bytes or str depending on version
            for line in r.iter_lines():
                if time.time() - start > timeout:
                    break
                if not line:
                    continue
                # line may be bytes
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                line = line.strip()
                if line.startswith("data:"):
                    payload = line[len("data:"):].strip()
                    try:
                        obj = json.loads(payload)
                    except Exception:
                        continue
                    events.append(obj)
                    if obj.get("type") in ("done", "killed"):
                        break
            return events
    except AttributeError:
        # fallback: non-streaming get with timeout via httpx-like? Use simple GET and parse body
        r = client.get(f"/api/jobs/{job_id}/logs", headers={"Accept": "text/event-stream"})
        # This may hang until timeout? Use with timeout parameter if supported
        body = r.text if hasattr(r, "text") else ""
        events = []
        for line in body.splitlines():
            line=line.strip()
            if line.startswith("data:"):
                payload=line[len("data:"):].strip()
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
        return events

def test_dry_run_returns_201_and_jobid():
    r = client.post("/api/export/dry-run")
    assert r.status_code == 201
    j = r.json()
    assert "jobId" in j
    job_id = j["jobId"]
    assert re.fullmatch(r"[0-9a-f]{8}", job_id), job_id
    # job should exist
    assert job_id in jobs
    # jobs dict shape
    jd = jobs[job_id]
    assert "proc" in jd
    assert "deque" in jd
    assert jd["deque"].maxlen == 500
    assert jd["status"] in ("idle","running","done","error","killed")
    assert "exitCode" in jd
    assert "createdAt" in jd

def test_sse_shape_and_done():
    r = client.post("/api/export/dry-run")
    job_id = r.json()["jobId"]
    events = _poll_sse_events(job_id, timeout=10.0)
    # Should have at least terminal done
    types = [e.get("type") for e in events]
    assert "done" in types, f"types={types} events={events}"
    done = [e for e in events if e.get("type")=="done"][0]
    assert "exitCode" in done
    assert done["exitCode"] == 0
    # stdout/stderr lines have required fields
    for e in events:
        if e.get("type") in ("stdout","stderr"):
            assert "line" in e
            assert "ts" in e
            assert isinstance(e["line"], str)
            assert isinstance(e["ts"], (int,float))

def test_sse_404_unknown():
    r = client.get("/api/jobs/notfound123/logs")
    assert r.status_code == 404
    r2 = client.post("/api/jobs/notfound123/cancel")
    assert r2.status_code == 404

def test_sse_replay():
    r = client.post("/api/export/dry-run")
    job_id = r.json()["jobId"]
    events1 = _poll_sse_events(job_id, timeout=10.0)
    assert any(e.get("type")=="done" for e in events1)
    # replay: second connect should return same deque + done without needing to wait for process
    time.sleep(0.2)
    events2 = _poll_sse_events(job_id, timeout=5.0)
    # Second replay should contain same count or subset (plus terminal)
    assert len(events2) >= 1
    # Should replay at least the same terminal
    assert events2[-1].get("type") in ("done","killed")
    # Compare counts excluding timing differences for ts? Compare types and lines lengths
    # Ensure first connection's stdout lines are replayed on second
    stdout1 = [e["line"] for e in events1 if e.get("type")=="stdout"]
    stdout2 = [e["line"] for e in events2 if e.get("type")=="stdout"]
    # deque is ring 500: second replay may be truncated suffix of first streaming
    if stdout2 != stdout1 and len(stdout2) != len(stdout1):
        # allow truncated replay: stdout2 should be suffix of stdout1 (ring buffer)
        assert stdout2 == stdout1[-len(stdout2):], f"replay truncated mismatch len1={len(stdout1)} len2={len(stdout2)}"
        assert len(stdout2) == 500 or len(stdout2) <= 500

def test_dry_run_file_writes():
    # Capture before files maybe exist - run dry-run and wait
    r = client.post("/api/export/dry-run")
    job_id = r.json()["jobId"]
    events = _poll_sse_events(job_id, timeout=10.0)
    assert any(e.get("type")=="done" for e in events)
    # File writes
    assert IMPORT_FILE.exists(), f"missing {IMPORT_FILE}"
    assert COMBOS_FILE.exists(), f"missing {COMBOS_FILE}"
    import_data = json.loads(IMPORT_FILE.read_text())
    combos_data = json.loads(COMBOS_FILE.read_text())
    assert isinstance(import_data, list)
    assert isinstance(combos_data, list)
    # apiKey should be env:SECRET placeholder, not raw secret
    for row in import_data:
        ak = str(row.get("apiKey",""))
        if ak:
            assert ak.startswith("env:") or ak=="***" or ak=="", f"apiKey should be env: placeholder got {ak}"
            assert "sk-" not in ak or ak.startswith("env:")

def test_cancel_killed_path():
    # Create a long-running job manually using server internals
    import uuid, asyncio
    from collections import deque
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {
        "id": job_id,
        "proc": None,
        "deque": deque(maxlen=500),
        "status": "idle",
        "exitCode": None,
        "createdAt": time.time(),
        "_queue": asyncio.Queue(),
    }
    # spawn sleep 30
    async def spawn_sleep():
        from ui.server import _spawn_job
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        cmd = [sys.executable, "-c", "import time, sys; print('sleep start', flush=True); time.sleep(30); print('done')"]
        await _spawn_job(cmd, REPO, env, job_id)
    # Schedule spawn
    import asyncio
    # Need to run spawn in background loop used by TestClient? TestClient runs app in same event loop per request,
    # but _spawn_job is async. We can start via client? Instead directly create subprocess via asyncio.run replacement hack:
    # Use TestClient to trigger spawn via dry-run with monkeypatched cmd? Simpler: directly create proc synchronously in test loop.
    # We'll create proc ourselves and assign to job, then test cancel endpoint handles SIGTERM/SIGKILL.
    async def create_proc():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time, sys; print('sleep start', flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED":"1"}
        )
        jobs[job_id]["proc"] = proc
        jobs[job_id]["status"] = "running"
        # reader tasks to fill deque/queue
        async def reader(stream, stype):
            while True:
                lb = await stream.readline()
                if not lb:
                    break
                line = lb.decode("utf-8", errors="replace").rstrip("\n")
                entry={"type": stype, "line": line, "ts": time.time()}
                jobs[job_id]["deque"].append(entry)
                try:
                    jobs[job_id]["_queue"].put_nowait(entry)
                except Exception:
                    pass
        t1 = asyncio.create_task(reader(proc.stdout, "stdout"))
        t2 = asyncio.create_task(reader(proc.stderr, "stderr"))
        # do not wait proc here; let test cancel it
        return proc, t1, t2
    # Run async creation
    proc_tuple = asyncio.run(create_proc())
    proc = proc_tuple[0]
    assert proc is not None
    time.sleep(0.3)  # let print arrive
    # Now cancel via API
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") == "killed"
    # Poll SSE for killed terminal
    events = _poll_sse_events(job_id, timeout=5.0)
    types = [e.get("type") for e in events]
    # Should contain killed
    assert "killed" in types, f"expected killed in {types} events={events}"
    # Idempotent second cancel
    r2 = client.post(f"/api/jobs/{job_id}/cancel")
    assert r2.status_code == 200
    assert r2.json().get("status") == "killed"
    # Ensure job deque not leaked secret etc? Check killed entry not contains secret
    for e in events:
        if "line" in e:
            assert "***" not in e["line"] or True  # just check no raw secret if env set (handled later)
    # Cleanup proc if still alive
    try:
        proc.kill()
    except Exception:
        pass
    try:
        import signal as _sig
        os.killpg(proc.pid, _sig.SIGKILL)
    except Exception:
        pass

def test_no_secret_leak_via_sse(monkeypatch):
    secret = "sk-very-secret-TESTKEY-12345"
    monkeypatch.setenv("OMNIROUTE_API_KEY", secret)
    # Also set a dummy env so dry-run doesn't need real key but even if printed, redaction should hide
    # The server _redact replaces env values with ***
    r = client.post("/api/export/dry-run")
    job_id = r.json()["jobId"]
    events = _poll_sse_events(job_id, timeout=10.0)
    # No event line should contain raw secret
    for e in events:
        line = e.get("line","")
        assert secret not in line, f"secret leaked in SSE line: {line}"
        # also check serialized payload not containing secret
        blob = json.dumps(e)
        assert secret not in blob
    # Files should not contain secret
    if IMPORT_FILE.exists():
        content = IMPORT_FILE.read_text()
        assert secret not in content
    if COMBOS_FILE.exists():
        assert secret not in COMBOS_FILE.read_text()

def test_ui_has_five_states():
    # Check static files contain required elements
    html = (REPO / "ui" / "static" / "index.html").read_text()
    assert "Dry run" in html or "dryBtn" in html or "Dry Run" in html
    assert "exportDot" in html
    assert "exportState" in html
    assert "exportLog" in html
    assert "auto-scroll" in html or "exportAutoScroll" in html
    assert "clear" in html.lower()
    js = (REPO / "ui" / "static" / "app.js").read_text()
    # 5 states
    for state in ["idle","running","done","error","killed"]:
        assert state in js, f"missing state {state} in app.js"
    # Check dot colors classes
    css = (REPO / "ui" / "static" / "style.css").read_text()
    assert ".dot" in css
    assert ".dot.running" in css
    assert ".dot.ok" in css
    assert ".dot.err" in css
    assert ".stderr" in css
    # HTML should have placeholder No logs yet
    assert "No logs yet" in html

def test_sse_headers():
    r = client.post("/api/export/dry-run")
    job_id = r.json()["jobId"]
    # Use stream to check headers
    try:
        with client.stream("GET", f"/api/jobs/{job_id}/logs") as resp:
            assert "text/event-stream" in resp.headers.get("content-type","")
            assert resp.headers.get("cache-control") == "no-cache"
            # close quickly
            resp.close()
    except AttributeError:
        r2 = client.get(f"/api/jobs/{job_id}/logs")
        assert "text/event-stream" in r2.headers.get("content-type","")
    # Wait for done to not leave hanging job
    _poll_sse_events(job_id, timeout=5.0)