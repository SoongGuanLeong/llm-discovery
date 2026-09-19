"""Tier B — argument-spy equivalence for discover (ADR 0010 #7).

Both old scripts/discover.py main() and new cli main() are invoked
in-process with monkeypatched pipeline; both must call the pipeline with
identical kwargs. Stdout/stderr/exit asserted separately. Changed-by-design
rows (--all-providers drop, --all-without-provider error) asserted with
registry cover.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import scripts.discover as old_discover
from llm_discovery import catalogs as catalogs_mod
from llm_discovery import pipeline as pipeline_mod
from llm_discovery import results as results_mod
from llm_discovery import cli as new_cli
from tests.parity.allowed_changes import ALLOWED

NORMALIZERS: tuple = ()


class _FakeCatalog:
    def __init__(self, *a, **k):
        pass


def _install_spy(monkeypatch, calls: dict):
    def _single(provider_name, config, aa, models_dev, **kw):
        calls["single"] = {"provider_name": provider_name, "kwargs": kw}
        return {"provider_model_id": "x", "decision": "keep", "provider": provider_name}

    def _provider(provider_name, config, aa, models_dev, **kw):
        calls["provider"] = {"provider_name": provider_name, "kwargs": kw}
        return {"keep": [], "drop": [], "error": []}

    def _all(config, aa, models_dev, **kw):
        calls["all"] = {"kwargs": kw}
        return {"groq": {"keep": [], "drop": [], "error": []}}

    monkeypatch.setattr(pipeline_mod, "discover_single", _single)
    monkeypatch.setattr(pipeline_mod, "discover_provider", _provider)
    monkeypatch.setattr(pipeline_mod, "discover_all_providers", _all)
    monkeypatch.setattr(old_discover, "discover_single", _single)
    monkeypatch.setattr(old_discover, "discover_provider", _provider)
    monkeypatch.setattr(old_discover, "discover_all_providers", _all)
    monkeypatch.setattr(catalogs_mod, "ArtificialAnalysisCatalog", _FakeCatalog)
    monkeypatch.setattr(catalogs_mod, "ModelsDevCatalog", _FakeCatalog)
    monkeypatch.setattr(old_discover, "ArtificialAnalysisCatalog", _FakeCatalog)
    monkeypatch.setattr(old_discover, "ModelsDevCatalog", _FakeCatalog)
    monkeypatch.setattr(results_mod, "save_yaml_result", lambda *a, **k: Path("saved.yaml"))
    monkeypatch.setattr(results_mod, "save_provider_result", lambda *a, **k: Path("saved.yaml"))
    monkeypatch.setattr(old_discover, "save_yaml_result", lambda *a, **k: Path("saved.yaml"))
    monkeypatch.setattr(old_discover, "save_provider_result", lambda *a, **k: Path("saved.yaml"))


def _run_old(monkeypatch, argv: list[str]) -> tuple[str, str]:
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    monkeypatch.setattr(sys, "argv", ["discover.py", *argv])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        old_discover.main()
    return out.getvalue(), err.getvalue(), calls


def _run_new(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_tracer_calls_identical(monkeypatch):
    from llm_discovery.config import load_config
    config = load_config()
    provider = config.providers[0].name
    old_out, _, old_calls = _run_old(monkeypatch, [provider])
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    code, new_out, _ = _run_new(["discover", provider])
    assert code == 0
    assert old_out.strip() and new_out.strip()
    assert "single" in old_calls and "single" in calls
    assert old_calls["single"]["provider_name"] == calls["single"]["provider_name"] == provider
    assert old_calls["single"]["kwargs"] == calls["single"]["kwargs"] == {}


def test_batch_calls_identical(monkeypatch):
    from llm_discovery.config import load_config
    config = load_config()
    provider = config.providers[0].name
    old_out, _, old_calls = _run_old(monkeypatch, [provider, "--all", "--workers", "2"])
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    code, new_out, _ = _run_new(["discover", provider, "--all", "--workers", "2"])
    assert code == 0
    assert old_out.strip() and new_out.strip()
    assert old_calls["provider"]["kwargs"] == calls["provider"]["kwargs"]
    assert calls["provider"]["kwargs"]["max_workers"] == 2


def test_all_providers_calls_identical(monkeypatch):
    old_out, _, old_calls = _run_old(monkeypatch, ["--all-providers", "--workers", "2"])
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    code, new_out, _ = _run_new(["discover", "--workers", "2"])
    assert code == 0
    assert old_out.strip() and new_out.strip()
    assert old_calls["all"]["kwargs"] == calls["all"]["kwargs"]
    assert calls["all"]["kwargs"]["max_workers"] == 2


def test_all_providers_flag_delta_documented():
    assert any(e.command == "discover" and e.old == "--all-providers" for e in ALLOWED)


def test_all_without_provider_is_usage_not_silent(monkeypatch):
    calls: dict = {}
    _install_spy(monkeypatch, calls)
    code, _, _ = _run_new(["discover", "--all", "--json"])
    assert code == 2
    assert any("discover --all with no provider" in e.old for e in ALLOWED)
