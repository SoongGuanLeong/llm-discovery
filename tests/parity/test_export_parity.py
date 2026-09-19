"""Tier A — real execution diff for export (ADR 0010 #7).

Deterministic: all lists sorted, sort_keys=True, no timestamps. Old and new
run against tests/fixtures/omniroute/; payload plus written artifacts are
byte-compared. Changed-by-design rows (flag renames, no-mode exit) are
asserted separately and must exist in allowed_changes.ALLOWED.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from llm_discovery import omniroute_export as old_export
from llm_discovery import cli as new_cli
from tests.parity.allowed_changes import ALLOWED

NORMALIZERS: tuple = ()

FIXTURES = Path("tests/fixtures/omniroute")
PROVIDERS = FIXTURES / "providers.yaml"
RESULTS = FIXTURES / "results"


def _run_old_dry_run(output_dir: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = old_export.main([
            "--dry-run",
            "--providers", str(PROVIDERS),
            "--results-dir", str(RESULTS),
            "--output-dir", str(output_dir),
        ])
    return code or 0, out.getvalue(), err.getvalue()


def _run_new_dry_run(output_dir: Path) -> tuple[int, dict, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = new_cli.main([
            "export", "dry-run",
            "--providers", str(PROVIDERS),
            "--results-dir", str(RESULTS),
            "--output-dir", str(output_dir),
            "--json",
        ])
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 1, "new --json stdout must be exactly one envelope line"
    return code, json.loads(lines[0]), out.getvalue(), err.getvalue()


def test_dry_run_payload_bytes_match(tmp_path):
    old_code, old_stdout, _ = _run_old_dry_run(tmp_path / "old")
    new_code, envelope, _, _ = _run_new_dry_run(tmp_path / "new")
    assert old_code == 0 and new_code == 0
    assert envelope["ok"] is True and envelope["command"] == "export.dry-run"
    new_stdout = json.dumps(envelope["data"], indent=2, sort_keys=True) + "\n"
    assert new_stdout == old_stdout


def test_dry_run_artifacts_bytes_match(tmp_path):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _run_old_dry_run(old_dir)
    _run_new_dry_run(new_dir)
    for name in ("omniroute_import.json", "omniroute_combos.json"):
        old_bytes = (old_dir / name).read_bytes()
        new_bytes = (new_dir / name).read_bytes()
        assert new_bytes == old_bytes, name


def test_check_alias_matches_dry_run(tmp_path):
    """Old --check is an alias for --dry-run; new has no --check (Tier C)."""
    out1, err1 = io.StringIO(), io.StringIO()
    with redirect_stdout(out1), redirect_stderr(err1):
        old_export.main(["--check", "--providers", str(PROVIDERS),
                         "--results-dir", str(RESULTS), "--output-dir", str(tmp_path / "check")])
    out2, err2 = io.StringIO(), io.StringIO()
    with redirect_stdout(out2), redirect_stderr(err2):
        old_export.main(["--dry-run", "--providers", str(PROVIDERS),
                         "--results-dir", str(RESULTS), "--output-dir", str(tmp_path / "check")])
    assert out1.getvalue() == out2.getvalue()
    assert any(e.aspect == "flag" and "--check" in e.old for e in ALLOWED)
    out3, err3 = io.StringIO(), io.StringIO()
    with redirect_stdout(out3), redirect_stderr(err3):
        new_code = new_cli.main(["export", "--check", "--providers", str(PROVIDERS),
                                 "--results-dir", str(RESULTS), "--json"])
    assert new_code == 2


def test_no_mode_exit_change_documented():
    """Old with no mode succeeds silently; new requires dry-run|apply (exit 2)."""
    out = io.StringIO()
    with redirect_stdout(out):
        old_code = old_export.main(["--providers", str(PROVIDERS),
                                    "--results-dir", str(RESULTS)])
    assert (old_code or 0) == 0
    out2, err2 = io.StringIO(), io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
        new_code = new_cli.main(["export", "--providers", str(PROVIDERS),
                                 "--results-dir", str(RESULTS), "--json"])
    assert new_code == 2
    assert any("no mode" in e.old for e in ALLOWED)


def test_apply_flag_shape_parity():
    """Old --omniroute-url becomes --gateway-url; --api-key survives (Tier C)."""
    old_parser = old_export.build_parser()
    old_flags = {a.option_strings[0] for a in old_parser._actions if a.option_strings}
    assert "--omniroute-url" in old_flags and "--api-key" in old_flags
    import io as _io
    from contextlib import redirect_stderr as _re, redirect_stdout as _ro
    out, err = _io.StringIO(), _io.StringIO()
    import os as _os
    old_env = _os.environ.get("OMNIROUTE_API_KEY")
    _os.environ["OMNIROUTE_API_KEY"] = "fake-key-for-parity-test"
    try:
        with _ro(out), _re(err):
            code = new_cli.main(["export", "apply", "--gateway-url", "http://127.0.0.1:9",
                                 "--providers", str(PROVIDERS), "--results-dir", str(RESULTS), "--json"])
    finally:
        if old_env is None:
            _os.environ.pop("OMNIROUTE_API_KEY", None)
        else:
            _os.environ["OMNIROUTE_API_KEY"] = old_env
    assert code == 4
    assert any(e.old == "--omniroute-url" for e in ALLOWED)
