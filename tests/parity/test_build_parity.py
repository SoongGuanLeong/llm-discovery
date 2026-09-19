"""Tier B — argument-spy equivalence for build (ADR 0010 #7)."""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from llm_discovery import build_all as build_mod
from llm_discovery import cli as new_cli
from tests.parity.allowed_changes import ALLOWED

NORMALIZERS: tuple = ()

_FAKE_RES = {
    "providers_discovered": 1,
    "providers": ["groq"],
    "files_written": [],
    "store_path": "data/model_info_store.json",
    "store_size": 1,
    "compact_bytes": 10,
    "pretty_bytes": 20,
    "telemetry": {"per_provider": {}, "totals": {}},
    "discovered": 0,
    "reused": 0,
    "rebuilt": 0,
    "gc": 0,
}


def _install_spy(monkeypatch, calls: dict):
    def _fake(**kw):
        calls.update(kw)
        return dict(_FAKE_RES)
    monkeypatch.setattr(build_mod, "build_all", _fake)


def _run_old(monkeypatch, argv: list[str]) -> tuple[str, str, dict]:
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["build_all", *argv])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        build_mod.main()
    return out.getvalue(), err.getvalue(), dict(calls)


def _run_new(monkeypatch, argv: list[str]) -> tuple[int, dict]:
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main(argv)
    return code, dict(calls)


def _shared_args(tmp_path: Path) -> list[str]:
    return ["--data-dir", str(tmp_path), "--config", "config/providers.yaml"]


def test_subset_calls_identical(monkeypatch, tmp_path):
    shared = _shared_args(tmp_path)
    old_out, _, old = _run_old(monkeypatch, [*shared, "--providers", "groq", "--workers", "2"])
    import io as _io
    from contextlib import redirect_stderr as _re, redirect_stdout as _ro
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    out, err = _io.StringIO(), _io.StringIO()
    with _ro(out), _re(err):
        code = new_cli.main(["build", *shared, "--providers", "groq", "--workers", "2"])
    _, new = code, dict(calls)
    assert code == 0
    assert old_out.strip() and out.getvalue().strip()
    for key in ("provider_names", "max_workers", "catalog_max_age_days", "no_catalog_refresh", "force_judge"):
        assert old[key] == new[key], key
    assert old["provider_names"] == ["groq"]
    assert new["provider_names"] == ["groq"]
    assert new["max_workers"] == 2


def test_positional_delta_documented(monkeypatch, tmp_path):
    """Old absorbs positional provider; new rejects it (Tier C)."""
    shared = _shared_args(tmp_path)
    _, _, old = _run_old(monkeypatch, [*shared, "groq", "--workers", "2"])
    assert old["provider_names"] == ["groq"]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main(["build", *shared, "groq", "--workers", "2", "--json"])
    assert code == 2
    assert any(e.command == "build" and "providers_pos" in e.old for e in ALLOWED)


def test_max_workers_alias_delta_documented(monkeypatch, tmp_path):
    shared = _shared_args(tmp_path)
    _, _, old = _run_old(monkeypatch, [*shared, "--max-workers", "3"])
    assert old["max_workers"] == 3
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main(["build", *shared, "--max-workers", "3", "--json"])
    assert code == 2
    assert any(e.command == "build" and "--max-workers" in e.old for e in ALLOWED)
