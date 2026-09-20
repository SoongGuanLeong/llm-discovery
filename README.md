# llm-discovery

[![CI](https://github.com/SoongGuanLeong/llm-discovery/actions/workflows/ci.yml/badge.svg)](https://github.com/SoongGuanLeong/llm-discovery/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/SoongGuanLeong/llm-discovery/blob/master/LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://github.com/SoongGuanLeong/llm-discovery/blob/master/pyproject.toml)

Discover and evaluate cloud LLMs across multiple providers. Enumerates provider `/models` endpoints, resolves each model against offline catalogs, judges coding relevance via LLM, and writes a curated keep-list per provider.

## Catalog quickstart (no API key)

The `catalog` commands read local snapshots of the model catalogs under `data/`.
A fresh clone ships none of them — `data/` is gitignored — so start with one
download. It is a public endpoint and needs no API key:

```bash
llm-discovery refresh --only models_dev
```

That fetches `https://models.dev/catalog.json`; every query below then works
offline:

```bash
llm-discovery catalog models show <model-id>
```

```bash
llm-discovery catalog providers show <provider-id>
```

```bash
llm-discovery catalog providers models <provider-id>
```

The `catalog aa search|filter` commands are not part of this quickstart: the
Artificial Analysis snapshot needs an `AA_API_KEY` to refresh — see
`docs/reference/catalog.md`.

## 5-minute quickstart

Go from a fresh clone to a curated keep-list applied to your OmniRoute
gateway. Run every command from the repo root.

**Prerequisites:** Python 3.12, `git`, a reachable OmniRoute gateway, and a
management key for it (required: `doctor` and `export apply` fail without one).
`config/providers.yaml` must list at least one provider — that file is yours to
edit by hand; no CLI command writes it. Its shape, secrets, and Infisical setup:
`docs/reference/cli.md`.

Clone the repo and install:

```bash
git clone https://github.com/SoongGuanLeong/llm-discovery.git
cd llm-discovery
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .
```

**1. Check the prerequisites.** Every failure names its own fix.

```bash
llm-discovery doctor
```

**2. Store the gateway key.** The value is read from stdin, never from argv.

```bash
OMNIROUTE_KEY="paste-management-key-here"
printf %s "$OMNIROUTE_KEY" | llm-discovery config set-key OMNIROUTE_API_KEY
```

**3. See which Configured Providers discovery will iterate over.**

```bash
llm-discovery providers list
```

**4. Discover.** With no provider argument this covers every Configured Provider.

```bash
llm-discovery discover --workers 4
```

**5. Build the store.** Fresh Keepers are reused, Candidates are re-judged.

```bash
llm-discovery build
```

**6. Inspect the payload that apply would send.** Local files only, no network.

```bash
llm-discovery export dry-run
```

**7. Apply it to the gateway.**

```bash
llm-discovery export apply
```

**8. Verify.**

```bash
curl -s http://localhost:20128/api/combos | jq
```

```bash
curl -s http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"flash","messages":[{"role":"user","content":"hi"}]}' | jq
```

Steps 4 and 5 dominate the wall clock - they call provider APIs and the LLM
judge. Everything before them finishes in under a minute. What each step
guarantees, and the full flag and exit-code surface: `docs/reference/cli.md`.

## Reference

- `docs/reference/cli.md` - per-step guarantees, configuration and secrets,
  running discovery, and the agents/scripts surface (`--json` envelope,
  stdout/stderr split, exit-code table).
- `docs/reference/catalog.md` - catalog data sources, refresh, scheduled
  refresh, and queries.
- `docs/reference/output.md` - result location, schema, downstream handoff.
- `docs/reference/export.md` - OmniRoute export.
- `docs/omni-infi-guide.md` - OmniRoute install/run, Infisical team setup, env
  table, offline fallback.

## Licence

MIT — see [LICENSE](LICENSE).
