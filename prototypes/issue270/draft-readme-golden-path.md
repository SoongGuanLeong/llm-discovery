# Draft — README "5-minute Golden Path" section

Draft for #274, written against the #270 prototype so surface and docs are judged
together. The section below is the paste-ready text for `README.md`; the notes
after it are for #274.

Everything in the section matches the surface the prototype validates:
`llm-discovery <command> --help` lists exactly these flags. Steps 4, 5, 7 and
`refresh` are surface-only stubs in the #270 prototype and are implemented in
#271.

---

## 5-minute Golden Path

Fresh clone to a curated keep-list applied to your OmniRoute gateway. Run every
command from the repo root.

**Prerequisites:** Python 3.12, `git`, a reachable OmniRoute gateway, and a
management key for it. `config/providers.yaml` must list at least one provider —
that file is yours to edit by hand; no CLI command writes it.

```bash
git clone <repo> && cd llm-discovery
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e .

# 1. Check the prerequisites. Every failure names its own fix.
llm-discovery doctor

# 2. Store the gateway key. The value is read from stdin, never from argv.
printf %s "$OMNIROUTE_KEY" | llm-discovery config set-key OMNIROUTE_API_KEY

# 3. See which Configured Providers discovery will iterate over.
llm-discovery providers list

# 4. Discover. With no provider argument this covers every Configured Provider.
llm-discovery discover --workers 4

# 5. Build the store: fresh Keepers are reused, Candidates are re-judged.
llm-discovery build

# 6. Inspect the payload that apply would send. Local only, no network, no writes.
llm-discovery export dry-run

# 7. Apply it to the gateway.
llm-discovery export apply
```

Steps 4 and 5 dominate the wall clock — they call provider APIs and the LLM
judge. Everything before them finishes in under a minute.

### What each step guarantees

| # | command | guarantee |
| --- | --- | --- |
| 1 | `doctor` | exits `0` only when every **required** prerequisite passes. Catalogs are **advisory**, so a fresh clone is never blocked by their absence. A failure is actionable: it prints the observed value and a copy-pasteable fix. |
| 2 | `config set-key` | reads the value from stdin (`getpass` on a TTY, raw read when piped), writes `.env` at `chmod 600`, and prints only the last four characters masked. |
| 3 | `providers list` | prints each provider's secret **env-var name**, never a value. |
| 4 | `discover` | progress and per-model lines go to stderr; the result goes to stdout. |
| 5 | `build` | same split. Reuses Keepers inside the 14-day Record TTL, re-judges Candidates. |
| 6 | `export dry-run` | `data` **is** the payload `apply` would send, unwrapped. Nothing is written and no network call is made. |
| 7 | `export apply` | POSTs the payload to the gateway. Gateway unreachable exits `4` (`pipeline`), not `1`. |

### `set` means present, not valid

`config status` reports a key as `set` when it is *present*. It makes no claim
that the key works. Validity is `doctor`'s job: it probes the gateway with the
key and reports what the gateway said.

### For agents

`--json` is accepted before or after the subcommand — root-first is the canonical
form — and produces exactly one envelope on stdout at exit: on success, on a
usage error, on an exception, and on Ctrl-C. Progress, warnings and prompts stay
on stderr, so a pipe is always parseable:

```bash
llm-discovery build --json 2>/dev/null | jq '.data.totals'

# just the fixes for whatever is not ready
llm-discovery doctor --json | jq -r '.data.checks[] | select(.status=="fail") | .fix'

# the human progress view, with the payload discarded
llm-discovery build 2>&1 >/dev/null
```

Exit codes are 1:1 with `error.code`, so a switch on the exit status and a switch
on `error.code` can never disagree:

| exit | `error.code` | meaning |
| --- | --- | --- |
| 0 | — | success |
| 1 | `internal` | unexpected bug |
| 2 | `usage` | bad flag or argument |
| 3 | `prerequisite` | config, secret or gateway not ready |
| 4 | `pipeline` | the command ran and the pipeline failed |
| 130 | `interrupted` | Ctrl-C |

### Where to look next

- `llm-discovery catalog aa search <query>`, `catalog aa filter --min-score N`,
  `catalog models show <id>`, `catalog models providers <id>`,
  `catalog providers show <id>`, `catalog providers models <id>` — offline
  catalog queries. No network, no key.
- `llm-discovery refresh` — refresh the catalog snapshots (`AA_API_KEY` from the
  environment is the only path; there is no key-taking flag).
- `llm-discovery <command> --help` — help exists at every level.

---

## Notes for #274

- **Keep the `--api-key` warning.** `export dry-run|apply --api-key` survives per
  #268, but the README must mark `OMNIROUTE_API_KEY` as preferred: argv leaks
  into `ps` and shell history.
- **Say the `config/providers.yaml` line out loud.** The draft says the file is
  user-only and hand-edited. That matches `AGENTS.md`, and it pre-empts an agent
  trying to "fix" `doctor`'s config failure by writing the file.
- **`doctor` wording is copied from real output**, not invented — see
  `README.md` in this directory for the captured transcripts.
- **Do not promise a byte-identical `dry-run` payload until #271 lands.** #268's
  `data` shape for `export dry-run` is `{"providers", "combos"}`; the payload
  `generate_payload()` actually produces is
  `{"import", "models", "gc", "combos", "meta"}`, and ADR 0010 Tier A requires a
  byte comparison against that. The draft says "the payload `apply` would send",
  which is true under either reading. See finding 1 in this directory's README.
- **Add the four flag deltas to the "where to look next" list once #271 lands**:
  `discover --all-providers` gone, `build --max-workers` gone, `refresh
  --aa-api-key` gone, `export --check` gone.
