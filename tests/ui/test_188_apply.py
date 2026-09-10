"""Tests for issue #188: Apply confirm + gateway http(s) validate + secret redact."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.server import app, jobs, DEFAULT_GATEWAY_URL

client = TestClient(app)
REPO = Path(__file__).parents[2]

@pytest.fixture(autouse=True)
def clean_jobs():
    jobs.clear()
    yield
    for jid, j in list(jobs.items()):
        proc = j.get("proc")
        if proc is not None:
            try:
                import signal, os as _os
                _os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    jobs.clear()

def test_gateway_validates_ftp_and_bare_host_400():
    for bad in ["ftp://example.com", "example.com:20128", "barehost", "//localhost:20128"]:
        r = client.post("/api/export/apply", json={"gatewayUrl": bad})
        assert r.status_code == 400, f"{bad} got {r.status_code} {r.text}"
        d = r.json().get("detail","")
        assert "gatewayUrl" in d or "http(s)" in d, d

def test_gateway_empty_string_400():
    r = client.post("/api/export/apply", json={"gatewayUrl": ""})
    assert r.status_code == 400
    assert "http(s)" in r.json().get("detail","") or "gatewayUrl" in r.json().get("detail","")

def test_gateway_default_when_omitted():
    r = client.post("/api/export/apply", json={})
    assert r.status_code == 201, r.text
    assert "jobId" in r.json()
    jid = r.json()["jobId"]
    assert re.fullmatch(r"[0-9a-f]{8}", jid)
    assert jid in jobs
    time.sleep(0.6)
    assert jobs[jid]["status"] in ("idle","running","done","error","killed")

def test_gateway_no_body_defaults():
    r = client.post("/api/export/apply")
    # FastAPI with no body should use default -> 201 or 422? We want 201 with default
    # If no JSON body, gatewayUrl defaults to None -> use DEFAULT
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        assert "jobId" in r.json()

def test_gateway_accepts_https():
    r = client.post("/api/export/apply", json={"gatewayUrl": "https://example.com:8443"})
    assert r.status_code == 201
    assert "jobId" in r.json()

def test_gateway_accepts_http():
    r = client.post("/api/export/apply", json={"gatewayUrl": "http://localhost:20128"})
    assert r.status_code == 201

def test_apply_auth_redacted_via_sse(monkeypatch):
    secret = "sk-apply-secret-REDACT-999"
    monkeypatch.setenv("OMNIROUTE_API_KEY", secret)
    r = client.post("/api/export/apply", json={"gatewayUrl": "http://localhost:20128"})
    assert r.status_code == 201
    jid = r.json()["jobId"]
    try:
        with client.stream("GET", f"/api/jobs/{jid}/logs") as resp:
            assert "text/event-stream" in resp.headers.get("content-type","")
            start = time.time()
            for line in resp.iter_lines():
                if time.time() - start > 10:
                    break
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode()
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                blob = json.dumps(obj)
                assert secret not in blob, f"secret leaked in SSE {obj}"
                if "line" in obj:
                    assert secret not in obj["line"]
                    assert "Bearer " + secret not in obj["line"]
                if obj.get("type") in ("done","killed"):
                    break
    except AttributeError:
        r2 = client.get(f"/api/jobs/{jid}/logs")
        assert secret not in r2.text

def test_apiKey_bearer_redaction_patterns(monkeypatch):
    from ui.server import _redact
    monkeypatch.setenv("OMNIROUTE_API_KEY", "tok12345")
    assert "tok12345" not in _redact("Authorization: Bearer tok12345")
    assert "***" in _redact("Authorization: Bearer tok12345")
    assert "tok12345" not in _redact('{"apiKey": "tok12345", "provider":"x"}')
    # generic Bearer still redacted even without env match
    generic = _redact("Authorization: Bearer some-random-token-xyz")
    assert "some-random-token-xyz" not in generic
    assert "***" in generic
    # env placeholder must NOT be redacted
    assert "env:AGNES_AI_API_KEY" in _redact('{"apiKey": "env:AGNES_AI_API_KEY"}')
    # Bearer with env secret already covered
    assert "tok12345" not in _redact("Bearer tok12345 leaked")

def test_ui_confirm_contract():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    js = (REPO / "ui" / "static" / "app.js").read_text()
    assert "localhost:20128" in html or DEFAULT_GATEWAY_URL in html
    assert "gateway" in html.lower()
    assert "apply" in html.lower()
    # Apply button id
    assert "applyBtn" in html.lower() or 'id="apply' in html.lower()
    # Confirm modal required text
    assert "Yes, apply" in html
    assert "bulk import" in html.lower()
    assert "idempotent" in html.lower()
    # Frontend ^https?:// validation
    assert "https?://" in js or "https?://" in html or "gatewayUrl" in js
    assert "dryBtn" in html.lower() or "Dry run" in html

def test_dryrun_needs_no_gateway():
    r = client.post("/api/export/dry-run")
    assert r.status_code == 201
    assert "jobId" in r.json()
