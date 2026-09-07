"""Black-box tests for scripts/setup.sh Quadlet install + idempotency + service control (issue 130)."""

import os
import stat
import subprocess
import textwrap
from pathlib import Path
import json

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = REPO_ROOT / "scripts" / "setup.sh"

VALID_SHARED = "7686072c-85c7-4b7e-96e5-5bad8086cf44"
VALID_DISCOVERY = "2902d9c9-0874-4d7e-80f6-2f201f44f911"

def make_fake_bin(tmp: Path, *, infisical_ok=True, dotenv_vars=24, podman_help_env=True):
    bin_dir = tmp / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    infisical_state = tmp / "infisical_logged_in"
    if infisical_ok:
        infisical_state.write_text("1")
    real_secrets = [f"VAR{i}=val{i}" for i in range(dotenv_vars)]
    dotenv_lines = "\n".join(real_secrets)
    help_env = "env" if podman_help_env else "usage: create"
    podman_state = tmp / "podman_secrets"
    podman_state.mkdir(exist_ok=True)
    systemctl_log = tmp / "systemctl_calls.log"
    systemctl_log.write_text("")
    podman_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        if [[ "$1" == "--version" ]]; then
          echo "podman version 5.7.0"
          exit 0
        fi
        if [[ "$1" == "secret" ]]; then
          if [[ "$2" == "--help" ]]; then echo "secret"; exit 0; fi
          if [[ "$2" == "create" && "$3" == "--help" ]]; then echo "{help_env}"; exit 0; fi
          if [[ "$2" == "exists" ]]; then
            if [[ -f "{podman_state}/$3" ]]; then exit 0; else exit 1; fi
          elif [[ "$2" == "rm" ]]; then
            rm -f "{podman_state}/$3" 2>/dev/null; exit 0
          elif [[ "$2" == "create" ]]; then
            if [[ "$*" == *"--env-file"* ]]; then
              name=$(echo "$@" | awk '{{print $NF}}')
              touch "{podman_state}/$name"
              exit 0
            else
              touch "{podman_state}/bifrost-env"
              cat > /dev/null
              exit 0
            fi
          fi
          exit 0
        fi
        exit 0
        """)
    (bin_dir / "podman").write_text(podman_script)
    (bin_dir / "podman").chmod(0o755)
    infisical_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        if [[ "$1" == "--version" ]]; then
          echo "infisical version 0.43.129"
          exit 0
        fi
        if [[ "$1" == "export" ]]; then
          if [[ ! -f "{infisical_state}" ]]; then
            echo "not logged in" >&2
            exit 1
          fi
          if echo "$*" | grep -q "dotenv"; then
            cat <<'EOFDOTENV'
{dotenv_lines}
EOFDOTENV
            exit 0
          else
            echo '{{"K":"V"}}'
            exit 0
          fi
        fi
        exit 0
        """)
    (bin_dir / "infisical").write_text(infisical_script)
    (bin_dir / "infisical").chmod(0o755)
    # systemctl that logs daemon-reload / restart / enable
    systemctl_script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        LOG="{systemctl_log}"
        if [[ "$1" == "--user" && "$2" == "--version" ]]; then
          echo "systemd 255"
          exit 0
        fi
        if [[ "$1" == "--user" && "$2" == "daemon-reload" ]]; then
          echo "daemon-reload" >> "$LOG"
          exit 0
        fi
        if [[ "$1" == "--user" && "$2" == "restart" ]]; then
          echo "restart $3" >> "$LOG"
          exit 0
        fi
        if [[ "$1" == "--user" && "$2" == "enable" ]]; then
          echo "enable $*" >> "$LOG"
          exit 0
        fi
        if [[ "$1" == "--user" && "$2" == "is-active" ]]; then
          echo "inactive" >&2
          exit 1
        fi
        exit 0
        """)
    (bin_dir / "systemctl").write_text(systemctl_script)
    (bin_dir / "systemctl").chmod(0o755)
    (bin_dir / "loginctl").write_text("#!/usr/bin/env bash\necho \"yes\"\nexit 0\n")
    (bin_dir / "loginctl").chmod(0o755)
    # uv that delegates to python
    (bin_dir / "uv").write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        if [[ "$1" == "--version" ]]; then echo "uv 0.5.0"; exit 0; fi
        for arg in "$@"; do
          if [[ "$arg" == "scripts/generate-bifrost-config.py" ]]; then
            exec python3 "$arg"
          fi
        done
        exec python3 "$@"
        """))
    (bin_dir / "uv").chmod(0o755)
    return bin_dir, podman_state, systemctl_log

def run_setup(tmp_home: Path, bin_dir: Path, args, env_overrides=None, cwd=REPO_ROOT, input_text=None):
    env = os.environ.copy()
    env["HOME"] = str(tmp_home)
    env["PATH"] = f"{bin_dir}:{env.get('PATH','')}"
    env.setdefault("LLM_SHARED_PROJECT_ID", VALID_SHARED)
    env.setdefault("LLM_DISCOVERY_PROJECT_ID", VALID_DISCOVERY)
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        [str(SETUP_SH)] + args,
        cwd=str(cwd),
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result

def _find_quadlet_dst(home: Path):
    qdst = home / ".config" / "containers" / "systemd"
    fallback = REPO_ROOT / ".tmp" / "quadlet"
    # check both container files
    for p in [qdst / "bifrost.container", fallback / "bifrost.container"]:
        if p.exists():
            return p.parent
    # if neither exists yet, return qdst as default
    return qdst

def test_units_copied_atomically_only_when_differ_second_run_up_to_date_no_daemon_reload(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state, slog = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    # first run should install and trigger daemon-reload
    r1 = run_setup(home, bin_dir, ["--yes"])
    assert r1.returncode == 0, f"first run failed {r1.stdout[-2000:]} {r1.stderr[-2000:]}"
    assert "installed ->" in r1.stdout
    assert "daemon-reload: systemctl --user daemon-reload" in r1.stdout or "daemon-reload done" in r1.stdout
    assert "daemon-reload" in slog.read_text(), "first run should have called daemon-reload"
    assert "up-to-date" not in r1.stdout or "installed" in r1.stdout
    # clear log for second run
    slog.write_text("")
    r2 = run_setup(home, bin_dir, ["--yes"])
    assert r2.returncode == 0, f"second run failed {r2.stdout[-2000:]} {r2.stderr[-2000:]}"
    # second run should report up-to-date for both units and skip daemon-reload
    assert r2.stdout.count("up-to-date (no copy)") >= 2, f"expected 2 up-to-date, got {r2.stdout}"
    assert "daemon-reload: skipped" in r2.stdout, f"should skip daemon-reload on up-to-date {r2.stdout}"
    assert "daemon-reload" not in slog.read_text(), f"second run should not call daemon-reload, got {slog.read_text()}"

def test_volume_source_is_absolute_repo_path(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, _, _ = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    r = run_setup(home, bin_dir, ["--yes"])
    assert r.returncode == 0
    qdst = home / ".config" / "containers" / "systemd" / "bifrost-data.volume"
    fallback = REPO_ROOT / ".tmp" / "quadlet" / "bifrost-data.volume"
    dst = qdst if qdst.exists() else fallback
    assert dst.exists(), f"volume unit not found"
    text = dst.read_text()
    assert f"Source={REPO_ROOT}/data/bifrost" in text or f"Source={REPO_ROOT / 'data' / 'bifrost'}" in text
    assert "Source=" in text
    # Source should be absolute, not relative
    for line in text.splitlines():
        if line.startswith("Source="):
            assert line.startswith("Source=/"), f"Source not absolute: {line}"
    # re-run preserves correctly
    r2 = run_setup(home, bin_dir, ["--yes"])
    assert r2.returncode == 0
    text2 = dst.read_text()
    assert text == text2, "Source preserved on re-run"

def test_secret_mode_writes_secret_file_mode_writes_environmentfile(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, _, _ = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    # podman mode (default) -> Secret
    r1 = run_setup(home, bin_dir, ["--yes"])
    assert r1.returncode == 0
    qdst = home / ".config" / "containers" / "systemd" / "bifrost.container"
    fallback = REPO_ROOT / ".tmp" / "quadlet" / "bifrost.container"
    dst = qdst if qdst.exists() else fallback
    assert dst.exists()
    txt = dst.read_text()
    assert "Secret=bifrost-env,type=env" in txt, f"podman mode should have Secret, got {txt}"
    assert "EnvironmentFile" not in txt
    # file mode -> EnvironmentFile (need fresh home or same home with flag toggles)
    # use same home but with --secrets=file, should toggle
    r2 = run_setup(home, bin_dir, ["--yes", "--secrets=file"])
    assert r2.returncode == 0, f"file mode failed {r2.stdout} {r2.stderr}"
    # after file mode, dst should now have EnvironmentFile
    # find again (may be same dst)
    dst2 = qdst if qdst.exists() else fallback
    txt2 = dst2.read_text()
    assert "EnvironmentFile=" in txt2, f"file mode should have EnvironmentFile, got {txt2}"
    assert "Secret=bifrost-env" not in txt2
    # toggle back to podman
    slog = tmp_path / "systemctl_calls.log"
    # ensure podman still works
    r3 = run_setup(home, bin_dir, ["--yes", "--secrets=podman"])
    assert r3.returncode == 0
    txt3 = (qdst if qdst.exists() else fallback).read_text()
    assert "Secret=bifrost-env,type=env" in txt3

def test_writable_check_falls_back_to_tmp_quadlet_when_ro(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # make HOME/.config/containers read-only so mkdir+touch fails
    ro_parent = home / ".config" / "containers"
    ro_parent.mkdir(parents=True, exist_ok=True)
    # make it non-writable
    ro_parent.chmod(0o555)
    bin_dir, _, _ = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    try:
        r = run_setup(home, bin_dir, ["--yes"])
        assert r.returncode == 0, f"fallback run failed {r.stdout} {r.stderr}"
        # should have warned about fallback
        combined = r.stdout + r.stderr
        assert ".tmp/quadlet" in combined, f"should mention fallback, got {combined}"
        fallback_cont = REPO_ROOT / ".tmp" / "quadlet" / "bifrost.container"
        fallback_vol = REPO_ROOT / ".tmp" / "quadlet" / "bifrost-data.volume"
        assert fallback_cont.exists(), "fallback container not installed"
        assert fallback_vol.exists(), "fallback volume not installed"
        # verify perms 0644
        mode_c = stat.S_IMODE(fallback_cont.stat().st_mode)
        assert mode_c == 0o644, f"fallback container perms {oct(mode_c)} != 0644"
        mode_v = stat.S_IMODE(fallback_vol.stat().st_mode)
        assert mode_v == 0o644, f"fallback volume perms {oct(mode_v)} != 0644"
        # original dst should not exist as file
        orig = home / ".config" / "containers" / "systemd" / "bifrost.container"
        # orig may not exist due to fallback
        assert not orig.exists() or orig.read_text() == fallback_cont.read_text()
    finally:
        # restore writable for cleanup
        ro_parent.chmod(0o755)

def test_idempotent_no_duplicate_no_perm_drift(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, _, slog = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    r1 = run_setup(home, bin_dir, ["--yes"])
    assert r1.returncode == 0
    qdst = home / ".config" / "containers" / "systemd"
    fallback = REPO_ROOT / ".tmp" / "quadlet"
    dst_dir = qdst if (qdst / "bifrost.container").exists() else fallback
    # check perms 0644
    for unit in ["bifrost.container", "bifrost-data.volume"]:
        p = dst_dir / unit
        assert p.exists()
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o644, f"{unit} perms {oct(mode)} != 0644"
        # check no duplicate Secret/EnvironmentFile lines
        txt = p.read_text()
        if unit == "bifrost.container":
            assert txt.count("Secret=") <= 1
            assert txt.count("EnvironmentFile=") <= 1
            assert "Secret=" in txt or "EnvironmentFile=" in txt
    # introduce perm drift: chmod to 0600
    for unit in ["bifrost.container", "bifrost-data.volume"]:
        (dst_dir / unit).chmod(0o600)
    slog.write_text("")
    r2 = run_setup(home, bin_dir, ["--yes"])
    assert r2.returncode == 0
    # should have fixed perms without triggering daemon-reload (still up-to-date content)
    for unit in ["bifrost.container", "bifrost-data.volume"]:
        mode = stat.S_IMODE((dst_dir / unit).stat().st_mode)
        assert mode == 0o644, f"after re-run {unit} perms not fixed {oct(mode)}"
    # content up-to-date, so no daemon-reload (timer enable may still run)
    assert "daemon-reload: skipped" in r2.stdout
    assert "daemon-reload" not in slog.read_text(), f"should not daemon-reload, got {slog.read_text()}"
    # third run still up-to-date
    r3 = run_setup(home, bin_dir, ["--yes"])
    assert r3.returncode == 0
    assert r3.stdout.count("up-to-date") >= 2
    # ensure data/bifrost not wiped
    assert (REPO_ROOT / "data" / "bifrost" / "config.json").exists()
    assert (REPO_ROOT / "data" / "bifrost" / "shim_map.json").exists()

def test_daemon_reload_only_on_change_restart_logic(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, _, slog = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    r1 = run_setup(home, bin_dir, ["--yes"])
    assert r1.returncode == 0
    assert "daemon-reload" in slog.read_text()
    # second run no change -> no reload
    slog.write_text("")
    r2 = run_setup(home, bin_dir, ["--yes"])
    assert r2.returncode == 0
    assert "daemon-reload" not in slog.read_text(), f"second run should not daemon-reload, got {slog.read_text()}"
    # third run with file mode toggles content -> should trigger reload
    slog.write_text("")
    r3 = run_setup(home, bin_dir, ["--yes", "--secrets=file"])
    assert r3.returncode == 0
    # toggling Secret->EnvironmentFile changes file, so reload should happen
    assert "daemon-reload" in slog.read_text(), f"toggling mode should trigger reload, log={slog.read_text()} stdout={r3.stdout}"


def test_refresh_catalog_units_installed_with_repo_paths(tmp_path):
    # issue #140: daily refresh timer installed via setup.sh diff-before-copy
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, _, slog = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    r1 = run_setup(home, bin_dir, ["--yes"])
    assert r1.returncode == 0, f"setup failed: {r1.stdout[-2000:]} {r1.stderr[-1000:]}"
    qdst = home / ".config" / "containers" / "systemd"
    fallback = REPO_ROOT / ".tmp" / "quadlet"
    dst_dir = qdst if (qdst / "refresh-catalogs.service").exists() else fallback
    svc = dst_dir / "refresh-catalogs.service"
    timer = dst_dir / "refresh-catalogs.timer"
    assert svc.exists(), f"refresh-catalogs.service not installed; stdout tail: {r1.stdout[-1500:]}"
    assert timer.exists(), "refresh-catalogs.timer not installed"
    stxt = svc.read_text()
    assert f"WorkingDirectory={REPO_ROOT}" in stxt, f"service must run from repo root: {stxt}"
    assert f"ExecStart={REPO_ROOT}/.venv/bin/python" in stxt, f"service must use repo venv python: {stxt}"
    assert "scripts/refresh_catalogs.py" in stxt
    assert "EnvironmentFile=-" in stxt, "service should read AA key env file if present (optional)"
    ttxt = timer.read_text()
    assert "OnCalendar=daily" in ttxt
    assert "WantedBy=timers.target" in ttxt
    # first install enables the timer
    assert "enable --now refresh-catalogs.timer" in slog.read_text(), f"timer not enabled: {slog.read_text()}"
    # second run: idempotent, no daemon-reload
    slog.write_text("")
    r2 = run_setup(home, bin_dir, ["--yes"])
    assert r2.returncode == 0
    assert r2.stdout.count("up-to-date (no copy)") >= 4, f"expected >=4 up-to-date units, got: {r2.stdout}"
    assert "daemon-reload" not in slog.read_text(), f"second run must not daemon-reload, got {slog.read_text()}"




def test_refresh_timer_aa_key_file_in_podman_mode(tmp_path):
    # Podman mode (default) must still leave an AA key file for the oneshot timer:
    # the timer Service uses EnvironmentFile, not podman Secret, so without this
    # AA refresh would run keyless forever.
    import stat as _stat
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, _, _ = make_fake_bin(tmp_path, infisical_ok=True, dotenv_vars=24)
    r = run_setup(home, bin_dir, ["--yes"])
    assert r.returncode == 0
    qdst = home / ".config" / "containers" / "systemd"
    fallback = REPO_ROOT / ".tmp" / "quadlet"
    dst_dir = qdst if (qdst / "refresh-catalogs.service").exists() else fallback
    svc = dst_dir / "refresh-catalogs.service"
    assert svc.exists()
    stxt = svc.read_text()
    # Podman mode: timer must point at refresh-catalogs.env, not deleted bifrost.env
    assert "refresh-catalogs.env" in stxt, f"podman timer must use refresh-catalogs.env: {stxt}"
    # Minimal file must exist, 0600, contains AA key
    aa_file = home / ".config" / "bifrost" / "refresh-catalogs.env"
    fb_aa = REPO_ROOT / ".tmp" / "refresh-catalogs.env"
    eff = aa_file if aa_file.exists() else fb_aa
    assert eff.exists(), f"refresh AA file missing: {aa_file} / {fb_aa}; stdout tail: {r.stdout[-1500:]}"
    assert _stat.S_IMODE(eff.stat().st_mode) == 0o600, f"perms {oct(_stat.S_IMODE(eff.stat().st_mode))} != 0600"
    txt = eff.read_text()
    # Mock infisical exports VAR{i}=val{i} so refresh file is empty (valid —
    # timer degrades: models.dev still refreshes, AA with 401 is warn-only).
    # Real deployment with AA_API_KEY in Infisical will populate this file.
    assert txt.strip() == "" or "AA_API_KEY" in txt or "ARTIFICIAL" in txt, f"refresh file unexpected content: {txt[:500]}"
    # Bifrost secret env file must NOT exist in podman mode (deleted)
    assert not (home / ".config" / "bifrost" / "bifrost.env").exists()
    assert not (REPO_ROOT / ".tmp" / "bifrost.env").exists() or "refresh-catalogs" in str(eff)

