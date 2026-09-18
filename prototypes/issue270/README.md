# Prototype #270 — `llm-discovery` CLI skeleton

Throwaway code answering one question: **does the command surface read well, and
is the failure wording actionable?**

This is the CLI-surface branch of the prototype skill: the artefact is a runnable
command-line program plus its captured output, not a LOGIC HTML file or a set of
UI variants. The question is about a text interface, so the interface is what got
built.

Part of Wayfinder #267. Locked inputs: #268 (envelope, exit taxonomy,
stdout/stderr, `doctor`) and ADR 0010 (flag deltas, secret entry).

## How to run

No install needed. The shim prefers the repo venv:

```bash
export PATH="$PWD/prototypes/issue270:$PATH"
llm-discovery --help
llm-discovery doctor
```

or without touching `PATH`:

```bash
python prototypes/issue270/llm_discovery_cli.py doctor
```

`--help` works even when `llm_discovery` is not importable, so the surface can be
reviewed from a bare checkout.

## Wiring for #271

This prototype deliberately does **not** touch `pyproject.toml`, because
`src/llm_discovery/cli.py` currently holds the live catalog-query CLI that #271
rewrites. `#271` adds the real entry point:

```toml
[project.scripts]
llm-discovery = "llm_discovery.cli:main"
```

`main()` here already returns an `int` exit code and `python -m
llm_discovery.cli` keeps working, so the module is a drop-in for that wiring.

## Real vs stubbed

| area | status |
| --- | --- |
| argparse tree, `--help` at every level, `--json` at root and after the subcommand | real |
| `--json` envelope on every path, exit taxonomy, stdout/stderr split | real |
| `doctor` (6 checks, required/advisory, `fix` text, exit 3) | real |
| `config status` / `set-key` / `clear-key` (stdin-only value, `chmod 600` always) | real |
| `providers list` | real |
| `catalog aa search\|filter`, `catalog models show\|providers`, `catalog providers show\|models` | real |
| `export dry-run` (calls `generate_payload`, writes nothing) | real |
| `discover`, `build`, `refresh`, `export apply` | surface-only stub — says so on stderr, exits 0, emits the documented `data` shape with zero counts |

Stub commands exiting `0` with `ok: true` is a prototype artefact. #271 must
never ship a stub that reports success.

## Captured output

Transcripts below are verbatim, with `$REPO` substituted for the absolute
checkout path and the gateway key a deliberate fake. The healthy run was taken
from the repo root with the local OmniRoute gateway up; the unhealthy run from an
empty directory with `OMNIROUTE_*` unset and the probe pointed at a closed port.

### `doctor` — healthy repo

```console
$ llm-discovery doctor
[ ok ] python — 3.12.13
[ ok ] install — llm_discovery importable from $REPO/src/llm_discovery/__init__.py; llm-discovery at $REPO/prototypes/issue270/llm-discovery
[ ok ] config — config/providers.yaml valid, 28 providers
[ ok ] secret — OMNIROUTE_API_KEY set via env (***a1b2)
[ ok ] catalogs — all 3 catalogs present in data/
[ ok ] gateway — HTTP 200 from http://localhost:20128/api/combos
doctor: ready
$ echo $?
0
```

```console
$ llm-discovery doctor --json
{"command": "doctor", "data": {"checks": [{"detail": "3.12.13", "fix": "", "name": "python", "severity": "required", "status": "pass"}, {"detail": "llm_discovery importable from $REPO/src/llm_discovery/__init__.py; llm-discovery at $REPO/prototypes/issue270/llm-discovery", "fix": "", "name": "install", "severity": "required", "status": "pass"}, {"detail": "config/providers.yaml valid, 28 providers", "fix": "", "name": "config", "severity": "required", "status": "pass"}, {"detail": "OMNIROUTE_API_KEY set via env (***a1b2)", "fix": "", "name": "secret", "severity": "required", "status": "pass"}, {"detail": "all 3 catalogs present in data/", "fix": "", "name": "catalogs", "severity": "advisory", "status": "pass"}, {"detail": "HTTP 200 from http://localhost:20128/api/combos", "fix": "", "name": "gateway", "severity": "required", "status": "pass"}], "ok": true}, "error": null, "ok": true, "schema": 1}
$ echo $?
0
```

### `doctor` — unhealthy repo (stdout and stderr split)

```console
$ llm-discovery doctor --gateway-url http://127.0.0.1:9
--- stdout ---
[ ok ] python — 3.12.13
[ ok ] install — llm_discovery importable from $REPO/src/llm_discovery/__init__.py; llm-discovery at $REPO/prototypes/issue270/llm-discovery
[fail] config — config/providers.yaml not found
  fix: create config/providers.yaml with at least one provider (see README -> Prerequisites); this file is user-only, so ask the user to edit it
[fail] secret — OMNIROUTE_API_KEY is not set
  fix: run `llm-discovery config set-key OMNIROUTE_API_KEY` (reads the value from stdin, never from argv)
[warn] catalogs — missing: models_dev_catalog.json, artificial_analysis_models.json, model_info_store.json
  fix: run `llm-discovery refresh` for the catalogs, then `llm-discovery build` to populate model_info_store.json
[fail] gateway — connection refused at http://127.0.0.1:9
  fix: start the OmniRoute gateway, then re-run `llm-discovery doctor`
doctor: not ready — 3 required check(s) failed: config, secret, gateway
--- stderr ---
error: 3 required check(s) failed: config, secret, gateway
hint: each failed check carries a `fix`; apply it, then re-run `llm-discovery doctor`
$ echo $?
3
```

`doctor --json` carries the same checks **and** the error, which is what lets a
failed `doctor` keep its `data` while still exiting `3`:

```console
$ llm-discovery doctor --gateway-url http://127.0.0.1:9 --json
{"command": "doctor", "data": {"checks": [{"detail": "3.12.13", "fix": "", "name": "python", "severity": "required", "status": "pass"}, {"detail": "llm_discovery importable from $REPO/src/llm_discovery/__init__.py; llm-discovery at $REPO/prototypes/issue270/llm-discovery", "fix": "", "name": "install", "severity": "required", "status": "pass"}, {"detail": "config/providers.yaml not found", "fix": "create config/providers.yaml with at least one provider (see README -> Prerequisites); this file is user-only, so ask the user to edit it", "name": "config", "severity": "required", "status": "fail"}, {"detail": "OMNIROUTE_API_KEY is not set", "fix": "run `llm-discovery config set-key OMNIROUTE_API_KEY` (reads the value from stdin, never from argv)", "name": "secret", "severity": "required", "status": "fail"}, {"detail": "missing: models_dev_catalog.json, artificial_analysis_models.json, model_info_store.json", "fix": "run `llm-discovery refresh` for the catalogs, then `llm-discovery build` to populate model_info_store.json", "name": "catalogs", "severity": "advisory", "status": "warn"}, {"detail": "connection refused at http://127.0.0.1:9", "fix": "start the OmniRoute gateway, then re-run `llm-discovery doctor`", "name": "gateway", "severity": "required", "status": "fail"}], "ok": false}, "error": {"code": "prerequisite", "hint": "each failed check carries a `fix`; apply it, then re-run `llm-discovery doctor`", "message": "3 required check(s) failed: config, secret, gateway"}, "ok": false, "schema": 1}
$ echo $?
3
```

### Ctrl-C mid-probe

SIGINT during the gateway probe. Artifacts are unaffected and the envelope names
the interrupted unit:

```console
$ llm-discovery doctor --gateway-url http://127.0.0.1:20777 --json   # hangs, then ^C
{"command": "doctor", "data": null, "error": {"code": "interrupted", "hint": "nothing was left half-written; re-run when ready", "message": "interrupted during doctor: gateway probe"}, "ok": false, "schema": 1}
$ echo $?
130
```

Interrupting during secret load names that instead:
`"message": "interrupted during secret load: infisical export"`.

### The stdout/stderr invariant

```console
$ llm-discovery build 2>&1 >/dev/null      # human progress view
[prototype] build is not wired to the pipeline; surface only, nothing ran
$ llm-discovery build 2>/dev/null          # payload only
build: not wired in this prototype
$ llm-discovery build --json 2>/dev/null   # one valid envelope
{"command": "build", "data": {"completed": 0, "providers": [], "total": 0, "totals": {"candidates": 0, "keepers": 0, "rebuilt": 0, "reused": 0}}, "error": null, "ok": true, "schema": 1}
$ llm-discovery build --json 2>&1 >/dev/null
[prototype] build is not wired to the pipeline; surface only, nothing ran
```

## Verification

A throwaway sweep (kept out of the repo; #271 writes the permanent
`tests/test_cli_contract.py`) ran 30 invocations and asserted, for each:

- stdout is exactly one line and parses as JSON in `--json` mode
- the envelope has exactly `schema`, `ok`, `command`, `data`, `error`
- `ok == (error is None)`, and the exit status equals `CODE_TO_EXIT[error.code]`
- every `error` carries `code`, `message` and `hint`
- a canary secret value set as `OMNIROUTE_API_KEY` appears in no stdout or stderr

Result: **30 cases, 0 failures.** The cases cover success paths, usage errors
(unknown provider, missing subcommand, unknown model, bad gateway URL), all four
prerequisite paths (missing config, missing catalog, missing key, unreachable
gateway), and `--help` at two levels.

Full-suite baseline on this checkout: `666 passed, 8 skipped, 3 failed`. The
three failures — `test_audit_harness.py::test_fixed_snapshot_fingerprint_uses_fixed_catalogs`
and `test_issue231_weak_recovery.py::{test_real_glm_dot_hyphen_strong_without_llm,test_real_mimo_moderate_goes_to_llm}`
— reproduce with this directory moved aside, so they are pre-existing and
data-dependent, not caused by the prototype.

Also verified by hand: `chmod 600` applied to a pre-existing `chmod 644` `.env`
(the ADR 0010 #4 change), empty/whitespace stdin rejected as `usage`, a secret
value never echoed by `config set-key`, and `BrokenPipeError` on `| head` exits
quietly with no traceback.

## Findings and open questions for #271

1. **`export dry-run`'s `data` shape contradicts Tier A.** #268 documents
   `{"providers", "combos"}`; `generate_payload()` returns
   `{"import", "models", "gc", "combos", "meta"}`, and ADR 0010 Tier A requires a
   byte comparison of the payload against the old `--dry-run`. The prototype
   emits the real payload, so Tier A passes and #268's example is stale — but
   this must be decided explicitly, not by accident.
2. **`--json --help` emits help text, not an envelope.** #268 says JSON mode
   covers every code path including exceptions, and is silent on `--help`. As
   built, `llm-discovery --json --help | jq` fails. Decide: envelope-wrapped help,
   or an explicit carve-out in the contract.
3. **The gateway probe proves reachability, not key validity.** `GET
   /api/combos` returns `200` on the local gateway with no `Authorization`
   header. Either probe an endpoint that actually requires the key, or rename the
   check to "gateway reachable" and stop implying the key was validated.
4. **`.env` anchoring is unspecified and currently inconsistent.** The prototype
   found this the hard way: `load_dotenv()` with no argument resolves `.env`
   relative to the *calling module's file*, not the cwd, while `config set-key`
   writes a cwd-relative `Path(".env")`. Run from anywhere but the repo root, the
   two disagree about which file is authoritative. The old `ui/server.py` anchored
   to `REPO_ROOT`; the CLI must pick one rule (repo-root anchoring is the safer
   choice, since every other path is repo-relative). The prototype now loads
   `dotenv_path=ENV_PATH` explicitly so both agree, and leaves the rule for #271.
5. **`discover --all` with no provider is ambiguous.** ADR 0010 #1 says "no
   provider means all configured providers" and "`<provider> --all` is the
   batch". Whether `discover --all` means all providers × all models is not
   stated. The prototype rejects it as `usage` and requires explicitness.
6. **The `[ ok ]` / `[warn]` tags are invented.** #268 specifies only `[fail]`.
   Lock the tag set before it leaks into the parity harness.
7. **`doctor` runs the Infisical export subprocess (~3.5s here).** `doctor` is
   meant to be the cheap prerequisite gate. Consider a timeout, or making the
   Infisical load lazy so `doctor` stays fast.
8. **`config status` reports four `OMNIROUTE_*` names; #268's example shows
   one.** The shape is compatible, but the set (and which one `export` prefers)
   should be locked — `get_auth_headers()` already honours all four.
9. **No-args behaviour.** `llm-discovery` with no arguments exits `2` with a
   `usage` envelope because the subcommand is `required=True`. Printing help and
   exiting `0` is the more common convention; decide.
10. **`export dry-run` writes no artifacts here.** Tier A compares "the written
    artifacts", so #271 must keep writing `data/derived/omniroute_import.json`
    and `omniroute_combos.json`, or the ADR must change.
11. **`--json` is accepted mid-tree too** (`catalog --json aa search ...`), not
    only at the root and after the leaf subcommand as #268 describes. Harmless,
    but the contract should say which positions are guaranteed.
12. **Non-`--json` flags are positional for the grouped commands.** `--config`,
    `--data-dir`, `--gateway-url` and friends must precede the action
    (`catalog --data-dir data aa search q`), because they are defined on the group
    parser. `--json` is the only flag the contract mandates in both positions, and
    it already is. #271 should make every flag work after the leaf command — the
    fix is a `default=argparse.SUPPRESS` parent parser on both group and leaf —
    and it is deliberately left undone here so the wart is visible rather than
    silently inherited.

## Verdict

**The surface reads well and the failure wording is actionable.** The Golden
Path is walkable on paper: eight commands, `--help` at every level, and every
prerequisite failure names both the observed value and the exact next command.

Two things the prototype bought that a design review would not have:

- The `.env` anchoring trap (finding 4) — a real inconsistency between what
  `config set-key` writes and what `config status` reads, invisible until run.
- The `dry-run` payload-shape conflict (finding 1) — #268's documented `data`
  shape and ADR 0010's byte-comparison requirement cannot both be satisfied as
  written.

The `doctor` failure text is the strongest part: `[fail] config —
config/providers.yaml not found` followed by a fix that names the file, says it
is user-only, and tells the caller to ask the user rather than write it. That is
the wording #271 should implement verbatim.

## Disposition

Throwaway. #271 implements the surface in `src/llm_discovery/cli.py`; #274 lifts
`draft-readme-golden-path.md`. This directory is deleted with the rest of the
prototype work once the surface is settled — the decisions above are what
survive.
