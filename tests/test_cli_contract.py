"""#271 permanent contract coverage (#268 + ADR 0010 #8 harness lifetime).

Independent of old code: envelope shape, exit taxonomy, stdout/stderr split,
no-secret-leak, doctor exit, and the #271 rulings (byte parity, repo-root
.env, --json --help carve-out, reachability-only probe, discover --all
explicit error). Temporary old-vs-new equivalence lives in tests/parity/
(#272), not here.
"""
from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from llm_discovery import cli
from llm_discovery.cli import (
    ENV_PATH,
    _REPO_ROOT,
    main,
)

CODE_TO_EXIT = {"internal": 1, "usage": 2, "prerequisite": 3, "pipeline": 4, "interrupted": 130}
FIXTURES = Path("tests/fixtures/omniroute")


def run(argv: list[str], env: dict | None = None) -> tuple[int, str, str]:
    old = dict(os.environ)
    if env:
        os.environ.update(env)
    cli._SECRETS_LOADED = False
    cli._DOTENV_KEYS = set()
    cli._SECRETS_ERROR = None
    cli._CURRENT_UNIT = ""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(old)
        cli._SECRETS_LOADED = False
        cli._DOTENV_KEYS = set()
        cli._SECRETS_ERROR = None
        cli._CURRENT_UNIT = ""
    return code, out.getvalue(), err.getvalue()


def run_json(argv: list[str], env: dict | None = None) -> tuple[int, dict, str, str]:
    code, out, err = run(argv, env)
    assert out.strip(), f"empty stdout for {argv}"
    lines = out.strip().splitlines()
    assert len(lines) == 1, f"stdout must be exactly one line in --json mode: {argv}"
    return code, json.loads(lines[0]), out, err


def test_envelope_keys_success():
    code, env_, out, err = run_json(["providers", "list", "--json"])
    assert code == 0
    assert set(env_.keys()) == {"schema", "ok", "command", "data", "error"}
    assert env_["ok"] is True and env_["error"] is None
    assert env_["command"] == "providers.list"


def test_exit_matches_error_code():
    code, env_, _, _ = run_json(["discover", "--all", "--json"])
    assert code == 2
    assert env_["ok"] is False
    assert env_["error"]["code"] == "usage"
    assert CODE_TO_EXIT[env_["error"]["code"]] == code
    assert set(env_["error"].keys()) == {"code", "message", "hint"}


def test_stdout_stderr_split_human(tmp_path):
    code, out, err = run(["export", "dry-run", "--providers", str(FIXTURES / "providers.yaml"),
                          "--results-dir", str(FIXTURES / "results"),
                          "--output-dir", str(tmp_path)])
    assert code == 0
    assert "export dry-run" in out
    assert out.strip()


def test_no_secret_leak_json():
    canary = "CANARY-LEAK-CHECK-9876"
    code, env_, out, err = run_json(["doctor", "--json"], env={"OMNIROUTE_API_KEY": canary})
    assert canary not in out and canary not in err
    assert canary not in json.dumps(env_)


def test_doctor_missing_config_exits_3_with_fix(tmp_path):
    missing = tmp_path / "no-providers.yaml"
    code, env_, out, err = run_json(["doctor", "--json", "--config", str(missing)])
    assert code == 3
    assert env_["error"]["code"] == "prerequisite"
    checks = {c["name"]: c for c in env_["data"]["checks"]}
    assert checks["config"]["status"] == "fail"
    assert "fix" in checks["config"] and checks["config"]["fix"]
    assert "user-only" in checks["config"]["fix"]


def test_discover_all_without_provider_is_usage():
    code, env_, _, _ = run_json(["discover", "--all", "--json"])
    assert code == 2
    assert env_["error"]["code"] == "usage"


def test_discover_unknown_provider_is_usage():
    code, env_, _, _ = run_json(["discover", "no-such-provider-xyz", "--json"])
    assert code == 2
    assert env_["error"]["code"] == "usage"


def test_export_dry_run_byte_parity_payload_and_artifacts(tmp_path):
    from llm_discovery.omniroute_export import generate_payload

    old_payload = generate_payload(FIXTURES / "providers.yaml", FIXTURES / "results")
    old_stdout = json.dumps(old_payload, indent=2, sort_keys=True) + "\n"
    outdir = tmp_path / "derived"
    code, env_, _, _ = run_json(["export", "dry-run", "--providers", str(FIXTURES / "providers.yaml"),
                                 "--results-dir", str(FIXTURES / "results"),
                                 "--output-dir", str(outdir), "--json"])
    assert code == 0
    new_stdout = json.dumps(env_["data"], indent=2, sort_keys=True) + "\n"
    assert new_stdout == old_stdout
    for name in ("omniroute_import.json", "omniroute_combos.json"):
        assert (outdir / name).exists()


def test_export_apply_dead_gateway_is_pipeline():
    code, env_, _, _ = run_json(
        ["export", "apply", "--gateway-url", "http://127.0.0.1:9", "--json"],
        env={"OMNIROUTE_API_KEY": "fake-key-for-parity-test"},
    )
    assert code == 4
    assert env_["error"]["code"] == "pipeline"


def test_json_help_carve_out():
    code, out, err = run(["--json", "--help"])
    assert code == 0
    assert "usage" in out.lower()
    assert out.strip().splitlines()[0].startswith("usage")


def test_env_repo_root_anchored():
    assert ENV_PATH == _REPO_ROOT / ".env"


def test_gateway_probe_reports_reachability_only(monkeypatch):
    class Resp:
        status_code = 200

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    monkeypatch.setenv("OMNIROUTE_API_KEY", "fake-key")
    ok, detail, fix = cli._probe_gateway("http://localhost:20128", "test probe")
    assert ok is True
    assert "reachable" in detail
    assert "not validated" in detail


def test_catalog_data_shapes():
    for argv, key in [
        (["catalog", "aa", "search", "llama", "--json"], "models"),
        (["catalog", "models", "show", "no-such-model-xyz", "--json"], None),
    ]:
        code, env_, _, _ = run_json(argv)
        if key:
            assert code == 0
            assert set(env_["data"].keys()) == {"count", "models"}
        else:
            assert code == 2


def test_catalog_models_providers_shape():
    code, env_, _, _ = run_json(["catalog", "models", "providers", "no-such-model-xyz", "--json"])
    assert code == 2
    assert env_["error"]["code"] == "usage"
