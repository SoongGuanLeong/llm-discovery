# Parity result (#272, ADR 0010)

Gate command: `pytest tests/parity` (temporary; deleted in #273 with old code).

## Equivalent (proven by harness)

- `export dry-run` payload bytes: old `omniroute_export --dry-run` stdout == new `export dry-run --json` envelope `data` (`test_export_parity.py::test_dry_run_payload_bytes_match`).
- `export dry-run` artifacts: `omniroute_import.json` + `omniroute_combos.json` byte-identical (`test_dry_run_artifacts_bytes_match`).
- `discover` tracer / batch / all-providers pipeline kwargs identical via argument spy (`test_discover_parity.py`).
- `build` subset kwargs identical for shared options (`test_build_parity.py::test_subset_calls_identical`).
- `refresh` kwargs identical except `aa_api_key` (`test_refresh_parity.py::test_refresh_calls_identical`).
- `providers list` name set matches `GET /api/providers` (`test_secrets_providers_parity.py::test_provider_name_set_matches`).

## Intentionally changed (registry cover in `allowed_changes.py`)

- Flag deltas (ADR 0010 #1): `--all-providers` dropped, positional `providers_pos` dropped, `--max-workers` alias dropped, `--aa-api-key` dropped, `--check` dropped, `--omniroute-url` renamed `--gateway-url`, no-mode exit 0 becomes 2, `discover --all` without provider becomes usage error.
- Streaming/cancel (ADR 0010 #2): SSE plus `ts` becomes stderr progress plus one envelope; SIGTERM/2s/SIGKILL endpoint becomes SIGINT envelope exit 130; 409 guard dropped.
- Exit remap (ADR 0010 #3): old catch-all 1 split into 2/3/4 per #268 taxonomy.
- Secrets (ADR 0010 #4): `.env` chmod 600 always (was only-new-file); mask last 4 (was last 3-4); stdin-only entry.
- Listing shape (ADR 0010 #5): sorted names become rich objects; name set must match.
- Entrypoint (ADR 0010 #6): many mains become one `llm-discovery` surface.

## Deletable once green (see `deletable.py`)

`tests/test_186_key_providers.py`, four `tests/ui/*` files, `tests/test_ui_scaffold.py`, `ui/`, redundant `scripts/` entrypoints, `tests/parity/` itself.
