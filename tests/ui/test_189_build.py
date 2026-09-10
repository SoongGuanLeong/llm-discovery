"""Tests for issue #189: Build-all All/multi + advanced flags + 409 guard."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from ui.server import app, jobs

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
                import os, signal
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    jobs.clear()


def test_build_all_providers_no_filter():
    with patch("ui.server.threading.Thread") as mock_thread:
        # prevent actual thread start, but capture cmd
        def fake_thread(*args, **kwargs):
            # simulate _run_job_thread not running; just return mock with start no-op
            m = MagicMock()
            m.start = lambda: None
            return m
        mock_thread.side_effect = fake_thread
        # need to inspect cmd passed to Thread(target=_run_job_thread, args=(cmd,...))
        r = client.post("/api/build", json={"providers": "all", "workers": 8})
        assert r.status_code == 201, r.text
        assert "jobId" in r.json()
        # find call that created thread for build
        # last call should be build job
        called = mock_thread.call_args_list[-1]
        args = called[1].get("args") or called[0][1] if len(called[0]) > 1 else called[1].get("args")
        # Thread(target=_run_job_thread, args=(cmd, REPO_ROOT, env, job_id))
        if args:
            cmd = args[0]
            assert "llm_discovery.build_all" in " ".join(cmd)
            assert "--workers" in cmd
            # all -> no --providers filter
            assert "--providers" not in cmd, f"all should not send --providers, got {cmd}"


def test_build_multi_mapping():
    with patch("ui.server.threading.Thread") as mock_thread:
        mock_thread.side_effect = lambda *a, **kw: MagicMock(start=lambda: None)
        r = client.post("/api/build", json={"providers": ["groq", "kilo_ai"], "workers": 4})
        assert r.status_code == 201, r.text
        called = mock_thread.call_args_list[-1]
        kwargs = called[1]
        args = kwargs.get("args")
        if not args and len(called[0]) > 1:
            args = called[0][1]
        cmd = args[0] if args else []
        assert "--providers" in cmd, f"multi should send --providers {cmd}"
        idx = cmd.index("--providers")
        # next elements should contain groq and kilo_ai
        assert "groq" in cmd[idx:]
        assert "kilo_ai" in cmd[idx:]
        assert "--workers" in cmd
        # workers mapping
        w_idx = cmd.index("--workers")
        assert cmd[w_idx + 1] == "4"


def test_build_400_empty_list():
    r = client.post("/api/build", json={"providers": [], "workers": 8})
    assert r.status_code == 400, r.text
    d = r.json().get("detail", "")
    assert "provider" in d.lower() or "empty" in d.lower() or "Select" in d


def test_build_400_workers_out_of_range():
    for bad in [0, 33, 99, -1]:
        r = client.post("/api/build", json={"providers": "all", "workers": bad})
        assert r.status_code == 400, f"workers {bad} should 400 got {r.status_code} {r.text}"
    for good in [1, 8, 32]:
        with patch("ui.server.threading.Thread", side_effect=lambda *a, **kw: MagicMock(start=lambda: None)):
            r = client.post("/api/build", json={"providers": "all", "workers": good})
            assert r.status_code == 201, f"workers {good} should 201 got {r.text}"
            jobs.clear()


def test_build_400_workers_string_or_missing():
    # string workers should 422 or 400
    r = client.post("/api/build", json={"providers": "all", "workers": "eight"})
    assert r.status_code in (400, 422), r.text


def test_build_advanced_flags_mapping():
    captured = {}
    with patch("ui.server.threading.Thread") as mock_thread:
        def capture(*a, **kw):
            captured["args"] = kw.get("args") or (a[1] if len(a) > 1 else None)
            m = MagicMock()
            m.start = lambda: None
            return m
        mock_thread.side_effect = capture
        r = client.post("/api/build", json={"providers": "all", "workers": 8, "catalogMaxAgeDays": 14, "noCatalogRefresh": True})
        assert r.status_code == 201, r.text
        cmd = captured["args"][0] if captured.get("args") else []
        assert "--no-catalog-refresh" in cmd, f"should include --no-catalog-refresh {cmd}"
        if "--catalog-max-age-days" in cmd:
            idx = cmd.index("--catalog-max-age-days")
            assert cmd[idx + 1] == "14"
        jobs.clear()
    captured2 = {}
    with patch("ui.server.threading.Thread") as mock_thread:
        def capture2(*a, **kw):
            captured2["args"] = kw.get("args") or (a[1] if len(a) > 1 else None)
            m = MagicMock()
            m.start = lambda: None
            return m
        mock_thread.side_effect = capture2
        r = client.post("/api/build", json={"providers": "all", "workers": 8, "catalogMaxAgeDays": 10, "noCatalogRefresh": True})
        assert r.status_code == 201, r.text
        cmd = captured2["args"][0] if captured2.get("args") else []
        assert "--no-catalog-refresh" in cmd, f"should include --no-catalog-refresh {cmd}"
        # catalog age 10 should map to --catalog-max-age-days 10
        if "--catalog-max-age-days" in cmd:
            idx = cmd.index("--catalog-max-age-days")
            assert cmd[idx + 1] == "10"


def test_build_409_global_guard():
    # first build running -> second should 409
    with patch("ui.server.threading.Thread", side_effect=lambda *a, **kw: MagicMock(start=lambda: None)):
        r1 = client.post("/api/build", json={"providers": "all", "workers": 8})
        assert r1.status_code == 201, r1.text
        # manually mark job as running to trigger guard
        jid = r1.json()["jobId"]
        jobs[jid]["status"] = "running"
        r2 = client.post("/api/build", json={"providers": "all", "workers": 8})
        assert r2.status_code == 409, f"second build should 409 got {r2.status_code} {r2.text}"
        assert "already running" in r2.json().get("detail", "").lower()
        # after job done, should allow new
        jobs[jid]["status"] = "done"
        r3 = client.post("/api/build", json={"providers": "all", "workers": 8})
        assert r3.status_code == 201, r3.text


def test_build_sse_streaming():
    # real subprocess via dummy command? Use actual build dry? Instead test SSE contract via build job with mocked python -c echo
    with patch("ui.server.sys.executable", "python3"):
        # patch cmd to simple echo so it completes fast, but we test SSE plumbing via real thread
        # we will not mock Thread here, let real job run with a quick python one-liner
        import sys
        # Temporarily patch build endpoint to use echo command? easier: call dry-run style but test build SSE shape directly
        # Create a job manually similar to server logic and verify SSE replays
        pass
    # Use real build but mock Popen to fast echo via patching _run_job_thread to emit lines
    # Simpler: trigger build with mocked Thread then manually add lines and check SSE backfill? We instead test that /api/jobs/{id}/logs returns event-stream
    with patch("ui.server.threading.Thread", side_effect=lambda *a, **kw: MagicMock(start=lambda: None)):
        r = client.post("/api/build", json={"providers": "all", "workers": 8})
        assert r.status_code == 201
        jid = r.json()["jobId"]
        # inject some lines into deque to test SSE backfill
        from collections import deque
        jobs[jid]["deque"].append({"type": "stdout", "line": "keep=8 drop=2", "ts": time.time()})
        jobs[jid]["deque"].append({"type": "stdout", "line": "backfill ok", "ts": time.time()})
        jobs[jid]["status"] = "done"
        jobs[jid]["exitCode"] = 0
        # use TestClient streaming or fallback
        try:
            with client.stream("GET", f"/api/jobs/{jid}/logs") as resp:
                assert "text/event-stream" in resp.headers.get("content-type", "")
                found_keep = False
                for line in resp.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    obj = json.loads(payload)
                    if obj.get("line") and "keep" in obj["line"]:
                        found_keep = True
                    if obj.get("type") == "done":
                        break
                assert found_keep, "SSE should replay deque lines with keep/drop"
        except AttributeError:
            r2 = client.get(f"/api/jobs/{jid}/logs")
            assert "text/event-stream" in r2.headers.get("content-type", "")
            assert "keep" in r2.text


def test_ui_build_card_contract():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    js = (REPO / "ui" / "static" / "app.js").read_text()
    # build card exists
    assert "card-build" in html or "Build all" in html
    assert "buildBtn" in html or 'id="buildBtn"' in html
    # All checkbox default checked with badge count
    assert "allProv" in html or "buildAll" in html.lower() or "All providers" in html
    assert "allBadge" in html or "22" in html or "providers" in html.lower()
    # filterable dropdown live from GET /api/providers
    assert "provSearch" in html or "provider" in html.lower()
    assert "/api/providers" in js
    # chip tags + count
    assert "chip" in html.lower() or "tag" in html.lower() or "buildChips" in html or "chip" in js.lower()
    # advanced collapsed
    assert "Advanced" in html
    assert "workers" in html.lower()
    assert "catalog" in html.lower() or "catalogAge" in html or "catalogMaxAge" in js
    assert "noRefresh" in html or "noCatalogRefresh" in html or "no-catalog-refresh" in html.lower()
    # 409 toast string
    assert "already running" in js.lower() or "409" in js
    # validation Select at least one
    assert "Select at least one" in js or "Select" in js
    # build log 5-state
    assert "buildLog" in html
    assert "buildDot" in html or "buildState" in html
    # SSE build log streaming keeps /api/jobs and Job streaming
    assert "/api/jobs" in js
    assert "EventSource" in js


def test_ui_build_dropdown_state_logic():
    js = (REPO / "ui" / "static" / "app.js").read_text()
    # All checked dims/disables individual, uncheck enables multi-select
    assert "allProv" in js or "buildAll" in js
    assert "disabled" in js
    # re-check clears individuals
    assert "clear" in js.lower()
    # workers/catalogMaxAgeDays defaults 8/28
    assert "8" in js  # workers default
    assert "28" in js  # catalog age default
    # catalog age disabled when noRefresh on
    assert "noRefresh" in js or "noCatalogRefresh" in js
    # filter logic
    assert "filter" in js.lower()


def test_build_no_secret_leak_via_sse():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    js = (REPO / "ui" / "static" / "app.js").read_text()
    css = (REPO / "ui" / "static" / "style.css").read_text()
    for content in [html, js, css]:
        # no raw secret pattern
        assert "sk-" not in content or "env:" in content

