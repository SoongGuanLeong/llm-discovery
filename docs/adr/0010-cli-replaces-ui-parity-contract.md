# ADR 0010: CLI replaces ui/ and the redundant scripts — behavioral parity contract

## Status

Accepted — Issue #269 grilling (part of #267 Wayfinder), 2026-09-18. Companion to the locked CLI contract on #268.

## Context

The Wayfinder map #267 retires `ui/` and the redundant `scripts/` entrypoints in favour of a single documented `llm-discovery` CLI. Deletion is gated on proving behavioral parity, so "parity" had to be defined before anything is removed. Without a written contract, "parity" degrades into "we looked at it and it seemed fine", and the deletion becomes irreversible without evidence.

Two facts shaped this decision. First, the effort is scoped to the interface in front of the pipeline: pipeline, gate, store, and judge semantics are out of scope, so what must be proven equivalent is the *interface* — the pipeline call a command makes, its output contract, and its exit status. Second, the repo has no CI, so any gate is a documented command rather than an enforced check.

The parity target is therefore not "the new CLI produces the same bytes as the old scripts". It is: every difference between old and new behaviour is either a preserved equivalence or an explicitly documented allowed change, and an unexplained difference fails the harness.

## Decision

### 1. Flag deltas

Four surface changes, each removing duplication or a leak:

- `discover` — no provider means all configured providers; `<provider>` is the tracer; `<provider> --all` is the batch. `--all-providers` is dropped as redundant. `--force-judge` survives.
- `build` — the hidden positional `providers_pos` is dropped; it existed only to absorb a `kilo_ai --all` invocation. `--all-providers` is dropped as redundant. The `--max-workers` alias is dropped; `--workers` survives.
- `refresh` — `--aa-api-key` is dropped. It is an argv secret, which the #268 contract forbids. `AA_API_KEY` from the environment is the only path.
- `export` — the `--dry-run` / `--check` / `--apply` flag trio becomes the `dry-run|apply` subcommands; the `--check` alias is dropped; `--omniroute-url` is renamed `--gateway-url`; `--api-key` survives per #268.

Rule applied: an alias or flag survives only if it carries a distinct meaning.

### 2. Streaming and cancellation

| old | new |
| --- | --- |
| SSE stream, events `{type: stdout\|stderr, line, ts}` then terminal `{type: done, exitCode}` or `{type: killed}` | progress lines on stderr, one envelope on stdout at exit |
| cancel endpoint: SIGTERM to the process group, 2s poll, SIGKILL; response `{"status":"killed"}`, exit code withheld | SIGINT produces the interrupted envelope and exit 130 |
| per-line `ts` timestamp | no timestamp; stderr is human text and nothing machine-reads it |

The SIGTERM → 2s → SIGKILL escalation is retained **internally**, applied to the CLI's own child processes, so a hung judge or provider HTTP call cannot leave the CLI unkillable. It is not part of the user-facing contract: one SIGINT yields the envelope and exit 130.

### 3. Exit-code remap

The old code used a catch-all `1`. The #268 taxonomy splits it.

| scenario | old | new |
| --- | --- | --- |
| unknown provider name (`discover`) | 1 | 2 `usage` |
| no provider configured | 1 | 3 `prerequisite` |
| `config/providers.yaml` missing or invalid | 1 | 3 `prerequisite` |
| catalog missing for a `catalog` query | 1 | 3 `prerequisite` |
| gateway unreachable on `export apply` | 1 | 4 `pipeline` |
| pipeline exception during `build` | 1 | 4 `pipeline` |
| bad flag or argument | 2 | 2 `usage` |
| `httpx` unavailable | 2 | 3 `prerequisite` |
| `omniroute_export` invoked with no mode | 0 | 2 `usage` |
| UI `400` bad key body | n/a (HTTP) | 2 `usage` |
| UI `409` job already running | n/a (HTTP) | dropped — the CLI is one process |

The `omniroute_export` row is the sharpest: the old command with no mode succeeded silently. The new CLI requires an explicit `dry-run|apply`, so this is an exit-code change, not merely a flag rename.

### 4. Secret entry

- `config set-key <name>` reads the value from stdin only — `getpass` on a TTY, raw read when piped. There is no key-taking argv flag on this command.
- `.env` is written at `chmod 600` **always**. The old UI route chmodded only when the file did not already exist, leaving a pre-existing file with its original mode.
- The hint masks the last four characters. The old UI masked the last three or four.
- `export dry-run|apply --api-key` survives. When both are present the flag takes precedence over `OMNIROUTE_API_KEY`; the docs mark the environment variable as preferred because argv leaks into `ps` and shell history.
- `refresh --aa-api-key` is dropped for the same reason.

### 5. Provider listing shape

`GET /api/providers` returned sorted names only. `providers list` returns rich objects (name, base_url, secret env-var name, discovery, discovery_strategy, custom). The **name set** must match; the shape change is allowed and documented. `secret` is always the environment variable *name*, never a value.

### 6. Entrypoint surface

One surface: `llm-discovery` plus `python -m llm_discovery`. As each command lands in the CLI, that module's `main()` and `__main__` block are deleted. `backfill` gets no CLI surface — it is internal maintenance. `scripts/query.py` is deleted in #273.

### 7. Proof tiers

Chosen by determinism, and by what is actually in scope:

- **Tier A — real execution diff.** `export` only. Deterministic (all lists sorted, `sort_keys=True`, no timestamps). Run the old `python -m llm_discovery.omniroute_export --dry-run` and the new `llm-discovery export dry-run --json` against `tests/fixtures/omniroute/`, and byte-compare the payload and the written artifacts.
- **Tier B — argument-spy equivalence.** `discover`, `build`, `refresh`. In-process: monkeypatch the pipeline function, invoke the old `main()` and the new `main()` with their respective argv, and assert both call the pipeline with identical kwargs. Then assert stdout/stderr/exit separately. Because the pipeline is untouched by design, a differing pipeline *call* is the only way the interface could silently change behaviour, and this catches exactly that.
- **Tier C — changed-by-design.** Proven by a *new* test asserting the new behaviour, plus an assertion that the old behaviour differs in precisely the documented way. Equivalence is never claimed for these.

**Normalization.** No test compares pipeline output, so nothing is normalized. `NORMALIZERS: tuple = ()` is declared explicitly in each parity test module, so that adding a normalizer requires editing a named constant rather than quietly relaxing a comparison. Known non-determinism in the pipeline — `wall_seconds`, `wall_duration_s`, `evaluated_at`, `last_updated`, `fetched_at`, `catalogs.stale`, `store_path`, unordered `per_provider` — is recorded here as a warning against introducing such a comparison later.

### 8. Harness lifetime

Split in two:

- `tests/parity/` — old-versus-new equivalence, **temporary**. Deleted in #273 alongside `ui/` and the shims. This is the deletion gate.
- `tests/test_cli_contract.py` — the #268 contract (envelope shape, exit taxonomy, stdout/stderr split, no-secret-leak, `doctor` exit), **permanent** regression coverage. Independent of the old code, so it must not live under `tests/parity/`.

### 9. Test migration

None of the six UI test files is ported wholesale. Assertions migrate individually:

- `tests/test_ui_scaffold.py`, `tests/ui/test_190_open_guide.py` — die with `ui/`; HTTP and static concerns.
- `tests/ui/test_189_build.py` — the 409 guard and SSE assertions die; the flag-to-command mapping assertion migrates into the new `build` tests.
- `tests/test_186_key_providers.py` — key persistence, trim/clear, whitespace-400 and chmod 600 migrate to `config set-key` tests, with chmod-always recorded as a Tier C change.
- `tests/ui/test_187_sse_dryrun.py` — SSE and cancel assertions die; the dry-run file-write assertions migrate as Tier A.
- `tests/ui/test_188_apply.py` — the redaction assertions migrate into `tests/test_cli_contract.py`'s no-secret-leak test.

Anything not listed is deleted with the old surface.

### 10. Deletion gate

`pytest tests/parity` must be green on `main` immediately before deletion, stated as a required step in #273's acceptance criteria and named in the README's contribution note. Fixtures are reused from `tests/fixtures/omniroute/`; no new fixture directory is added. The pipeline parity tests read the real `config/providers.yaml` read-only, so the suite is sensitive to the local provider set — accepted for a temporary gate.

This is a convention, not a guarantee, because the repo has no CI. Enforcing it is deferred to #275.

### 11. Unexplained differences

The rule the harness enforces: **every difference between old and new behaviour is either a preserved equivalence or an explicitly documented allowed change; an unexplained difference fails the parity harness.**

Mechanically, allowed differences live in `tests/parity/allowed_changes.py` as a tuple of frozen records:

```python
@dataclass(frozen=True)
class AllowedChange:
    command: str   # "discover" | "export.apply" | "*"
    aspect: str    # "stdout" | "stderr" | "exit_code" | "flag" | "artifact" | "env_var"
    old: str
    new: str
    adr: str       # anchor, e.g. "ADR-0010#1-flag-deltas"
```

Anchors are GitHub heading slugs, so the numbered subsections above resolve as `#1-flag-deltas`, `#2-streaming-and-cancellation`, `#3-exit-code-remap`, `#4-secret-entry`, `#5-provider-listing-shape`, `#6-entrypoint-surface`, `#7-proof-tiers`, `#8-harness-lifetime`, `#9-test-migration`, `#10-deletion-gate`, `#11-unexplained-differences`. Renumbering a subsection breaks the registry entries that point at it, which is the intended cost: the anchor check fails loudly rather than silently detaching documentation from code.

The harness asserts `computed_diffs - ALLOWED == ∅`. Every entry's `adr` anchor must resolve to a heading in this file, and the harness checks that too. An allowed change therefore cannot be invented in a test file without also being written here — the documentation requirement is mechanised rather than conventional.

## Considered Options

- **Diff the old and new end-to-end output for every command.** Rejected: the old `scripts/discover.py:main` builds catalogs and calls the pipeline directly with no injection seam, so an offline subprocess diff would hit the network. In-process argument-spy equivalence tests the in-scope property more directly.
- **Keep one harness that is both deletion gate and regression suite.** Rejected: #273 would delete the regression coverage along with the old code.
- **Free-form comments next to each assertion instead of a registry.** Rejected: satisfies "documented" only by convention, and nothing fails when an entry is missing.
- **Add CI in this effort.** Rejected as out of scope for #267; deferred to #275.
- **A fixture `providers.yaml` plus monkeypatched `load_config` for the pipeline parity tests.** Rejected as unnecessary machinery: it would test a configuration the user never runs.

## Consequences

- `ui/` and the redundant `scripts/` entrypoints can be deleted with evidence rather than assertion.
- The allowed-change list is executable: adding a difference without documenting it fails the suite, and documenting it in the ADR is checked by the suite.
- The old code must remain in place and green until #273 runs the gate.
- `tests/parity/` is knowingly temporary; the contract coverage that must survive lives in `tests/test_cli_contract.py`.
- The gate is manual until #275 lands.

## References

- #267 Wayfinder map (destination, Notes, Out of scope)
- #268 CLI contract (envelope, exit taxonomy, stdout/stderr, doctor)
- #269 this decision, #270 prototype, #271 implementation, #272 harness, #273 deletion, #274 README
- #275 CI follow-up
- ADR 0006 (Accurate-Enough Gate and store source of truth), ADR 0009 (per-evidence TTL split)
- `ui/server.py`, `scripts/discover.py`, `src/llm_discovery/build_all.py`, `src/llm_discovery/refresh.py`, `src/llm_discovery/omniroute_export.py`
- `tests/fixtures/omniroute/`, `tests/test_186_key_providers.py`, `tests/ui/`
