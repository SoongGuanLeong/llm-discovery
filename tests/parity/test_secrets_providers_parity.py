"""Tier C — changed-by-design: secrets, listing, exit remap, streaming (ADR 0010 #2-5).

Each test asserts the new behaviour plus the precise old difference, and
references allowed_changes.ALLOWED. Equivalence is never claimed here.
"""
from __future__ import annotations

import io
import json
import os
import stat
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import ui.server as old_server
from llm_discovery import cli as new_cli
from tests.parity.allowed_changes import ALLOWED

NORMALIZERS: tuple = ()

client = TestClient(old_server.app)


def _run_new(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    import sys
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    # StringIO has no isatty(True); add one returning False (piped).
    sys.stdin.isatty = lambda: False  # type: ignore[attr-defined]
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = new_cli.main(argv)
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


def test_provider_name_set_matches(monkeypatch):
    """Name set must match; rich shape is allowed change (ADR 0010 #5)."""
    old_names = sorted(client.get("/api/providers").json()["providers"])
    code, out, _ = _run_new(["providers", "list", "--json"])
    assert code == 0
    envelope = json.loads(out.strip().splitlines()[0])
    new_names = sorted(p["name"] for p in envelope["data"]["providers"])
    assert new_names == old_names
    assert set(envelope["data"]["providers"][0].keys()) >= {"name", "base_url", "secret"}
    assert any(e.command == "providers.list" for e in ALLOWED)


def test_chmod_always_vs_conditional(monkeypatch, tmp_path):
    """Old chmods only new file; new chmods always (ADR 0010 #4)."""
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    env.chmod(0o644)
    monkeypatch.setattr(old_server, "ENV_PATH", env)
    r = client.post("/api/config/omniroute-key", json={"key": "new-secret-value"})
    assert r.status_code == 200
    assert stat.S_IMODE(env.stat().st_mode) == 0o644

    env2 = tmp_path / ".env2"
    env2.write_text("OTHER=1\n", encoding="utf-8")
    env2.chmod(0o644)
    monkeypatch.setattr(new_cli, "ENV_PATH", env2)
    code, out, _ = _run_new(["config", "set-key", "OMNIROUTE_API_KEY", "--json"], stdin="new-secret-value")
    assert code == 0
    assert stat.S_IMODE(env2.stat().st_mode) == 0o600
    assert any(e.command == "config.set-key" and "chmod 600" in e.new for e in ALLOWED)


def test_mask_last4_vs_last3(monkeypatch, tmp_path):
    """6-char key: old masks last 3, new masks last 4 (ADR 0010 #4)."""
    assert old_server._hint_for_key("abcdef") == "***def"
    assert new_cli._mask("abcdef") == "***cdef"
    env = tmp_path / ".env"
    monkeypatch.setattr(new_cli, "ENV_PATH", env)
    code, out, _ = _run_new(["config", "set-key", "OMNIROUTE_API_KEY", "--json"], stdin="abcdef")
    assert code == 0
    assert json.loads(out.strip())["data"]["masked"] == "***cdef"


def test_exit_remap_documented():
    """Spot-check remap rows exist in registry (ADR 0010 #3)."""
    rows = {(e.command, e.aspect, e.old, e.new) for e in ALLOWED if e.aspect == "exit_code"}
    assert ("discover", "exit_code", "unknown provider exits 1", "exits 2 usage") in rows
    assert ("export.apply", "exit_code", "gateway unreachable exits 1", "exits 4 pipeline") in rows
    code, _, _ = _run_new(["discover", "no-such-provider-xyz", "--json"])
    assert code == 2


def test_streaming_and_409_documented():
    """SSE/cancel/409 have no CLI equivalent by design (ADR 0010 #2)."""
    assert any(e.aspect == "stderr" and "SSE" in e.old for e in ALLOWED)
    assert any("409" in e.old for e in ALLOWED)
    assert any("SIGTERM" in e.old for e in ALLOWED)
