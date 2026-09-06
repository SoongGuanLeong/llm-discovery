"""Black-box tests for scripts/setup.sh podman secret handoff (issue 129)."""

import os
import stat
import subprocess
import textwrap
import time
from pathlib import Path
import json

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = REPO_ROOT / "scripts" / "setup.sh"

VALID_SHARED = "7686072c-85c7-4b7e-96e5-5bad8086cf44"
VALID_DISCOVERY = "2902d9c9-0874-4d7e-80f6-2f201f44f911"

def make_fake_bin_podman(tmp: Path, *, infisical_ok=True, with_uv=True, uv_fails=False, dotenv_vars=24, podman_help_env=True, podman_env_file_fails=False):
    bin_dir = tmp / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    infisical_state = tmp / "infisical_logged_in"
    if infisical_ok:
        infisical_state.write_text("1")
    real_secrets = [f"VAR{i}=val{i}" for i in range(dotenv_vars)]
    dotenv_lines = "\n".join(real_secrets)
    help_env = "env" if podman_help_env else "usage: create"
    # podman that supports secret create --help containing env when podman_help_env
    podman_state = tmp / "podman_secrets"
    podman_state.mkdir(exist_ok=True)
    # build podman script
    podman_script = textwrap.dedent(f"""        #!/usr/bin/env bash
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
            # handle --env-file form vs stdin fallback
            if [[ "$*" == *"--env-file"* ]]; then
              if [[ "{str(podman_env_file_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              name=$(echo "$@" | awk '{{print $NF}}')
              touch "{podman_state}/$name"
              exit 0
            else
              # stdin fallback: podman secret create bifrost-env - < file
              name=$(echo "$@" | awk '{{for(i=1;i<=NF;i++) if($i=="create") print $(i+1)}}')
              # handle dash
              if [[ "$name" == "-" ]]; then name="bifrost-env"; fi
              # also handle create bifrost-env -
              if [[ "$2" == "create" && "$3" != "--env-file" ]]; then
                name="$3"
                if [[ "$name" == "-" ]]; then name="bifrost-env"; fi
              fi
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
    # infisical
    infisical_script = textwrap.dedent(f"""        #!/usr/bin/env bash
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
    (bin_dir / "systemctl").write_text("#!/usr/bin/env bash\nif [[ \"$1\" == \"--user\" && \"$2\" == \"--version\" ]]; then echo \"systemd 255\"; exit 0; fi\nexit 0\n")
    (bin_dir / "systemctl").chmod(0o755)
    (bin_dir / "loginctl").write_text("#!/usr/bin/env bash\necho \"yes\"\nexit 0\n")
    (bin_dir / "loginctl").chmod(0o755)
    if with_uv:
        if uv_fails:
            (bin_dir / "uv").write_text("#!/usr/bin/env bash\necho \"uv 0.5\" >&2\nexit 1\n")
        else:
            (bin_dir / "uv").write_text(textwrap.dedent("""                #!/usr/bin/env bash
                if [[ "$1" == "--version" ]]; then echo "uv 0.5.0"; exit 0; fi
                for arg in "$@"; do
                  if [[ "$arg" == "scripts/generate-bifrost-config.py" ]]; then
                    exec python3 "$arg"
                  fi
                done
                exec python3 "$@"
                """))
        (bin_dir / "uv").chmod(0o755)
    return bin_dir, podman_state

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

def test_default_no_flag_equals_podman_no_file_secret_exists(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state = make_fake_bin_podman(tmp_path, infisical_ok=True, dotenv_vars=24, podman_help_env=True)
    result = run_setup(home, bin_dir, ["--yes"])
    assert result.returncode == 0, f"exit {result.returncode} stdout={result.stdout[-2000:]} stderr={result.stderr[-2000:]}"
    eff = home / ".config" / "bifrost" / "bifrost.env"
    fallback = REPO_ROOT / ".tmp" / "bifrost.env"
    assert not eff.exists(), "podman mode should not write file"
    assert not fallback.exists(), "podman mode should not write fallback"
    assert (podman_state / "bifrost-env").exists(), "podman secret not created"
    # quadlet should use Secret
    qdst = home / ".config" / "containers" / "systemd" / "bifrost.container"
    fallback_q = REPO_ROOT / ".tmp" / "quadlet" / "bifrost.container"
    dst = qdst if qdst.exists() else fallback_q
    assert dst.exists()
    text = dst.read_text()
    assert "Secret=bifrost-env,type=env" in text
    assert "EnvironmentFile" not in text

def test_old_podman_without_type_env_fails_fast_with_hint(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state = make_fake_bin_podman(tmp_path, infisical_ok=True, dotenv_vars=24, podman_help_env=False)
    result = run_setup(home, bin_dir, ["--yes"])
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Upgrade Podman" in combined
    assert "--secrets=file" in combined
    assert not (podman_state / "bifrost-env").exists()
    eff = home / ".config" / "bifrost" / "bifrost.env"
    assert not eff.exists()

def test_secrets_flag_toggles_quadlet_unit(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state = make_fake_bin_podman(tmp_path, infisical_ok=True, dotenv_vars=24, podman_help_env=True)
    # file mode -> EnvironmentFile
    result = run_setup(home, bin_dir, ["--yes", "--secrets=file"])
    assert result.returncode == 0
    qdst = home / ".config" / "containers" / "systemd" / "bifrost.container"
    dst = qdst if qdst.exists() else REPO_ROOT / ".tmp" / "quadlet" / "bifrost.container"
    assert "EnvironmentFile=" in dst.read_text()
    # podman mode -> Secret
    result2 = run_setup(home, bin_dir, ["--yes", "--secrets=podman"])
    assert result2.returncode == 0
    text2 = dst.read_text()
    assert "Secret=bifrost-env,type=env" in text2
    assert "EnvironmentFile" not in text2
    # toggle back to file
    result3 = run_setup(home, bin_dir, ["--yes", "--secrets=file"])
    assert result3.returncode == 0
    assert "EnvironmentFile=" in dst.read_text()

def test_secret_replace_atomic_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state = make_fake_bin_podman(tmp_path, infisical_ok=True, dotenv_vars=24, podman_help_env=True)
    result1 = run_setup(home, bin_dir, ["--yes"])
    assert result1.returncode == 0
    assert (podman_state / "bifrost-env").exists()
    mtime1 = (podman_state / "bifrost-env").stat().st_mtime
    time.sleep(1.1)
    result2 = run_setup(home, bin_dir, ["--yes"])
    assert result2.returncode == 0
    assert (podman_state / "bifrost-env").exists()
    # quadlet up-to-date on second run
    assert "up-to-date" in result2.stdout.lower() or "up-to-date" in result2.stderr.lower()

def test_no_stale_file_left_in_secret_mode(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state = make_fake_bin_podman(tmp_path, infisical_ok=True, dotenv_vars=24, podman_help_env=True)
    eff = home / ".config" / "bifrost" / "bifrost.env"
    eff.parent.mkdir(parents=True, exist_ok=True)
    eff.write_text("OLD=1\n")
    result = run_setup(home, bin_dir, ["--yes"])
    assert result.returncode == 0
    assert not eff.exists(), "stale file not removed in secret mode"
    assert (podman_state / "bifrost-env").exists()

def test_podman_missing_hard_fails(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # bin without podman
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    infisical_state = tmp_path / "infisical_logged_in"
    infisical_state.write_text("1")
    (bin_dir / "infisical").write_text(textwrap.dedent(f"""        #!/usr/bin/env bash
        if [[ "$1" == "--version" ]]; then echo "infisical version 0.43.129"; exit 0; fi
        if [[ "$1" == "export" ]]; then echo "VAR0=val0"; exit 0; fi
        exit 0
        """))
    (bin_dir / "infisical").chmod(0o755)
    (bin_dir / "systemctl").write_text("#!/usr/bin/env bash\nif [[ \"$1\" == \"--user\" && \"$2\" == \"--version\" ]]; then echo \"systemd 255\"; exit 0; fi\nexit 0\n")
    (bin_dir / "systemctl").chmod(0o755)
    (bin_dir / "loginctl").write_text("#!/usr/bin/env bash\necho \"yes\"\nexit 0\n")
    (bin_dir / "loginctl").chmod(0o755)
    (bin_dir / "uv").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bin_dir / "uv").chmod(0o755)
    result = run_setup(home, bin_dir, ["--yes"])
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Podman" in combined or "podman" in combined

def test_fallback_stdin_when_env_file_fails(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, podman_state = make_fake_bin_podman(tmp_path, infisical_ok=True, dotenv_vars=24, podman_help_env=True, podman_env_file_fails=True)
    result = run_setup(home, bin_dir, ["--yes"])
    assert result.returncode == 0, f"fallback failed stdout={result.stdout[-2000:]} stderr={result.stderr[-2000:]}"
    assert (podman_state / "bifrost-env").exists()
