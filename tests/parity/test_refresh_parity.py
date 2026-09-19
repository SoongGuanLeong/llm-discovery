"""Tier B — argument-spy equivalence for refresh (ADR 0010 #7)."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from llm_discovery import refresh as refresh_mod
from llm_discovery import cli as new_cli
from tests.parity.allowed_changes import ALLOWED

NORMALIZERS: tuple = ()


def _install_spy(monkeypatch, calls: dict):
    def _fake(**kw):
        calls.update(kw)
        return {"aa": None, "models_dev": None, "benchmarks": None}
    monkeypatch.setattr(refresh_mod, "refresh_all", _fake)


def _run_old(monkeypatch, argv: list[str]) -> tuple[dict, str]:
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["refresh", *argv])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        refresh_mod.main()
    return dict(calls), out.getvalue()


def _run_new(monkeypatch, argv: list[str]) -> tuple[int, dict, str]:
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main(argv)
    return code, dict(calls), out.getvalue()


def test_refresh_calls_identical(monkeypatch, tmp_path):
    old, old_out = _run_old(monkeypatch, ["--data-dir", str(tmp_path), "--dry-run", "--only", "aa", "models_dev"])
    code, new, new_out = _run_new(monkeypatch, ["refresh", "--data-dir", str(tmp_path), "--dry-run", "--only", "aa", "models_dev"])
    assert code == 0
    assert old_out.strip() and new_out.strip()
    assert Path(old["data_dir"]) == Path(new["data_dir"]) == tmp_path
    assert old["dry_run"] is True and new["dry_run"] is True
    assert old["only"] == new["only"] == ["aa", "models_dev"]
    assert old["backup"] is True and new["backup"] is True
    assert new["aa_api_key"] is None


def test_aa_api_key_delta_documented(monkeypatch, tmp_path):
    """Old accepts --aa-api-key; new rejects it, env only (Tier C)."""
    old, _ = _run_old(monkeypatch, ["--data-dir", str(tmp_path), "--aa-api-key", "secret-value"])
    assert old["aa_api_key"] == "secret-value"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main(["refresh", "--data-dir", str(tmp_path), "--aa-api-key", "x", "--json"])
    assert code == 2
    assert any(e.command == "refresh" and "--aa-api-key" in e.old for e in ALLOWED)
