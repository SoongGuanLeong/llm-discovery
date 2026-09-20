# Spec: first-look experience

Locked, hand-off-ready decisions for Wayfinder #298. Each section is settled by the
ticket named in its heading; nothing here is speculative. Persisted prose, normal register.

| Ticket | Section | Status |
| --- | --- | --- |
| #301 | Landing page deployment | Settled — see `docs/research/issue-301-github-pages-actions.md` (referenced from #298) |
| #299 | Landing page look and copy | Settled — resolution comment on #299; production page rewrites the prototype |
| #300 | README split, Catalog Path, reference docs | Settled — this file, below |
| #303 | Repo metadata (description, topics, MIT license, homepage) | Not yet settled |
| #302 | Pages deployment for `site/` | Not yet settled (builds on #301) |
| #304 | Assemble and finalise this spec | Open |

---

## README split, Catalog Path, and the reference docs (#300)

### Reader model

The README is written for a reader who gives it **30 seconds**. In that time they must
learn what the tool is, whether it is for them, and the first command to run. Everything
that serves a second reading — schemas, flag matrices, payload shapes, secrets plumbing,
automation — moves to `docs/reference/`. The 30-second reader sees exactly two command
blocks: the Catalog Path and the Golden Path.

### New README section order and budgets

| # | Section | Budget | Content |
| --- | --- | --- | --- |
| 1 | Title, badges, tagline | ~5 lines | `# llm-discovery`, the badge row, the existing one-paragraph description |
| 2 | **Catalog Path** (new) | ~18 lines | Keyless first command, exact wording below |
| 3 | **5-minute Golden Path** | ~60 lines | Intro, prerequisites paragraph (pointing at `docs/reference/cli.md` for the `config/providers.yaml` shape), the existing 8-step command block with one-line comments |
| 4 | **Reference** | ~10 lines | Bullet list linking the four reference docs, `docs/omni-infi-guide.md`, the agents note, and the MIT licence |
| 5 | Licence line | ~2 lines | MIT, linked (lands with #303's LICENSE file) |

Target: roughly 95–110 lines, down from 387. No other commands appear in the README.

### Badge row

Placement: directly under the `# llm-discovery` title, above the tagline. Exactly three
badges, in this order:

1. **CI** — `https://github.com/SoongGuanLeong/llm-discovery/actions/workflows/ci.yml/badge.svg`, linking to the Actions page. The workflow file is `ci.yml` (verified on disk).
2. **MIT** — shields licence badge, linking to the repo's `LICENSE` blob (file lands with #303; until then the badge still reads MIT and links to the repo root).
3. **Python 3.12** — shields badge, linking to `pyproject.toml`.

No coverage, no issues-count, no third-party badges.

### Content mapping: where today's 387 lines go

Line numbers refer to today's `README.md`.

| Today's lines | Section | Destination |
| --- | --- | --- |
| 1–3 | Title and tagline | Stays (tagline unchanged) |
| 5–49 | Golden Path intro, prerequisites, 8-step block | Stays, compressed to the budget above |
| 51–66 | "What each step guarantees" table | `docs/reference/cli.md` |
| 67–72 | "`set` means present, not valid" | `docs/reference/cli.md` |
| 73–101 | "For agents" (`--json` envelope, exit-code table) | `docs/reference/cli.md`, section "For agents and scripts" |
| 102–114 | "Where to look next" | Rewritten as the README **Reference** list: guide link stays; the AA-key note moves to `docs/reference/catalog.md`; the catalog-command bullets move to the Catalog Path section and `docs/reference/catalog.md` |
| 115–157 | Prerequisites (`config/providers.yaml` shape, secrets, Infisical) | `docs/reference/cli.md`, section "Configuration and secrets" |
| 159–186 | Installation | **Deleted from the README** — duplicates the Golden Path env setup; the Infisical detail is preserved once, inside `docs/reference/cli.md` "Configuration and secrets" |
| 188–204 | "Catalog data sources" | `docs/reference/catalog.md` |
| 206–236 | "How to run" + "What happens per run" | `docs/reference/cli.md`, under `discover` |
| 238–290 | Output: location, schema, downstream handoff | `docs/reference/output.md` |
| 291–316 | OmniRoute export | `docs/reference/export.md` |
| 317–350 | Catalog refresh + systemd timer (#140) | `docs/reference/catalog.md` |
| 352–359 | "Query catalogs" | `docs/reference/catalog.md` |
| 361–387 | "Interface decisions (why no UI, no server)" | **Deleted outright.** ADR 0010 holds it normatively; the README must not carry a second, drift-prone copy |

The four reference docs carry no Golden Path walkthrough and no marketing copy; they are
reference material, cross-linked from the README Reference list. Whether `docs/reference/`
also gets an index page, and whether the reference docs link the landing page, is still
open in #298's "Not yet specified" and is not decided here.

### The offline claim: corrected wording

Today's lines 104–107 claim catalog queries are "offline ... No network, no key". That is
false on a fresh clone: `data/**` is gitignored (`.gitignore:21`), so every `catalog`
query exits 3 with `catalog not found: …` and the hint `run llm-discovery refresh`.
The replacement, verbatim, opens the Catalog Path section:

> Catalog queries read local snapshots under `data/`. A fresh clone ships none — `data/`
> is gitignored — so the Catalog Path needs one network fetch and no API key:

Followed by the command block below. The README must not claim any catalog query is
offline or keyless before the snapshot exists.

### The Catalog Path: exact commands

```bash
llm-discovery refresh --only models_dev
llm-discovery catalog models show <model-id>
llm-discovery catalog providers show <provider-id>
llm-discovery catalog providers models <provider-id>
```

- `refresh --only models_dev` fetches `https://models.dev/catalog.json` — public, no key.
- The `catalog models …` / `catalog providers …` queries read that snapshot offline.
- `catalog aa search|filter` is **not** part of the Catalog Path: the Artificial Analysis
  snapshot needs `AA_API_KEY`. One sentence in the README says so and defers to
  `docs/reference/catalog.md`.

Placement in the README: immediately after the tagline, **before** the Golden Path — it is
the reader's first command, and it works with zero configuration.

### Glossary entry (CONTEXT.md)

Landed in this change, in the *Interfaces* group directly after **Golden Path** so the two
read as neighbours, never synonyms:

> **Catalog Path**:
> The keyless way to see real data before configuring anything: fetch the models.dev
> snapshot once with `llm-discovery refresh --only models_dev`, then query it with
> `catalog models …` and `catalog providers …`. Needs network for that one fetch but no
> management key, no provider keys, and no gateway; unlike the Golden Path it never
> writes results or touches the gateway. The `catalog aa` queries are not part of it —
> the Artificial Analysis snapshot needs `AA_API_KEY`.
> _Avoid_: keyless path, offline mode, demo mode

The distinction from Golden Path is mechanical: Golden Path = key + gateway + write path,
Catalog Path = no key, no gateway, read-only.

### "For agents" decision

The section is reference material, not first-look material. It moves wholesale to
`docs/reference/cli.md` under "For agents and scripts" (`--json` envelope, stdout/stderr
split, the exit-code table). The README keeps exactly one line in the Reference list
pointing at it, so an agent is one hop away while the 30-second reader never sees the
taxonomy.

### Test changes (acceptance surface)

`tests/test_readme_golden_path.py` is the regression harness for this spec. Required
changes when the split lands:

1. `test_interface_decisions_recorded` — rewritten: the README no longer mentions
   ADR 0010 (it links `docs/reference/cli.md` instead). New assertions: the ADR file
   exists at `docs/adr/0010-cli-replaces-ui-parity-contract.md`, and the README carries
   no resurrected-UI instructions (the existing `test_no_stale_surfaces_in_readme`
   already guards spellings).
2. New: badge-row test — exactly three badge lines directly under the title.
3. New: Catalog Path test — `refresh --only models_dev` plus the three `catalog …`
   commands appear before the Golden Path heading and parse against `_build_parser()`.
4. Existing tests that must keep passing unchanged: `test_golden_path_section_lists_steps_in_order`,
   `test_golden_commands_parse_against_cli`, `test_guide_promoted_and_linked`,
   `test_agents_points_at_cli`, `test_no_stale_surfaces_in_readme`.

### Acceptance criteria (implementation ticket)

1. README sections in the order and budgets above; total ≤ ~120 lines.
2. Badge row: exactly three badges (CI, MIT, Python 3.12) under the title.
3. The offline claim is replaced by the exact wording above. No README text claims a
   `catalog` query works offline or keyless on a fresh clone. (The tagline's "resolves
   each model against offline catalogs" describes the snapshot-backed resolution step
   inside a discovery run and stays.)
4. `docs/reference/{cli,output,catalog,export}.md` exist and hold the mapped content;
   every link from the README resolves to a real file.
5. The Interface decisions section is gone; ADR 0010 stays untouched as the normative record.
6. CONTEXT.md carries the Catalog Path entry as quoted above.
7. The test changes above are in; full suite green.
