# Spec: first-look experience

Locked, hand-off-ready decisions for Wayfinder #298. Each section is settled by the
ticket named in its heading; nothing here is speculative. Persisted prose, normal register.

| Ticket | Section | Status |
| --- | --- | --- |
| #301 | Landing page deployment | Settled — see `docs/research/issue-301-github-pages-actions.md` on `origin/research/github-pages-actions`, referenced from #298 |
| #299 | Landing page look and copy | Settled — this file, below; prototype on `prototype/299-landing-page`, production page rewrites it |
| #300 | README split, Catalog Path, reference docs | Settled — this file, below |
| #303 | Repo metadata (description, topics, MIT license, homepage) | Settled — this file, below; `LICENSE` file landed with the resolution |
| #302 | Pages deployment for `site/` | Settled — this file, below |
| #304 | Assemble and finalise this spec | Settled — this commit |

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
| 4 | **Reference** | ~10 lines | Bullet list linking the four reference docs, `docs/omni-infi-guide.md`, and the agents note |
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
reference material, cross-linked from the README Reference list. Decided in #304:
`docs/reference/` gets **no index page** — the README Reference list is the sole index —
and neither the README nor the reference docs cross-link the landing page; discovery of
the live page runs through the repository's homepage field (#303).

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

---

## Landing page look and copy (#299)

> **Direction reversed (2026-09-20, PR #312).** After the prototype deployed live, the
> maintainer chose **direction B (paper/serif editorial)** as the production page over the
> originally locked direction A (mono/teal). `site/index.html` now serves editorial; the
> mono and product-card variants are deleted from `site/` and survive only on the
> `prototype/299-landing-page` branch. The visual-direction subsection below documents the
> **superseded** direction A and is kept as design history, not as the current spec — the
> live `site/index.html` is the source of truth for the shipped look. The **Copy** block
> stays authoritative: all three variants shared identical copy and it is unchanged.

Prototype: branch [`prototype/299-landing-page`](https://github.com/SoongGuanLeong/llm-discovery/tree/prototype/299-landing-page),
directory `site/`. Three directions were explored as self-contained single files (inline
CSS, no JavaScript, identical copy): A mono/teal (`index.html`), B paper/serif editorial
(`variant-b.html`), C centred product cards (`variant-c.html`). The ticket originally locked
**A**; the production page that shipped is **B (editorial)** — see the reversal note above.
Nothing merged as-is from the prototype branch: the production ticket promoted the editorial
prototype to `site/index.html` and dropped the A/B/C switcher bar; the rejected variants
left `site/` (#302 AC4).

### Visual direction (superseded — direction A, kept as design history)

- Near-white paper (`#fbfbfa`), ink text (`#14171a`), one desaturated teal accent
  (`#0e7490`) used only for links, step indices and the note rule. The neon-green-on-black
  draft was rejected as too "hacker"; the page is near-monochrome.
- Monospace carries structure — the eyebrow, section labels, step numbers, hints, the
  keep-list, the footer; system sans carries prose.
- Left-aligned single measure, max 760px; thin `1px` rules between sections; no cards, no
  shadows, no fills except the keep-list tint and the single solid CTA button.
- Mobile: single column throughout. Problem rows collapse from label-plus-text to stacked
  at 640px. The five-step strip is 5 columns above 640px, 2 columns down to 420px, 1
  column below. Long inline paths wrap (`overflow-wrap: anywhere`); the JSON block scrolls
  horizontally rather than wrapping.

### Copy (locked)

- **h1**: "Find the free coding models worth routing to."
- **Subhead**: opens with the locked one-liner — *"Finds the best free coding LLMs across
  providers and wires them into your gateway."* — followed by one supporting sentence on
  what it does.
- **CTAs**: "Read the Golden Path" (solid ink button) and "View the source" (quiet link).
  One solid button and one quiet link only; a second primary action would require
  re-deciding the hierarchy.
- **Pipeline diagram**: a five-cell bordered CSS grid with monospace indices and short
  hints, captioned "The pipeline" — not SVG. The hand-written SVG version was built as
  variant B and rejected on maintenance and reflow grounds.
- **Keep-list excerpt**: two of the three combos (`contributor_free`, `flash`) from the
  committed fixture `data/derived/examples/omniroute_combos.example.json`, shown verbatim
  under a line saying what it is, with the path cited in the caption.
- **Gateway dependency** — the "Run it" section opens with, verbatim: *"llm-discovery is a
  CLI, and it is not a gateway. It finds the models and configures the OmniRoute gateway
  you already run — so the last step needs a reachable gateway and a management key."* The
  only call to action is the link to the README's 5-minute Golden Path; **no commands
  appear on the page**.
- **Footer**: names the MIT licence and links the repository.

The page's meta/social layer (Open Graph tags, favicon, preview image) is deferred to the
production ticket — see Out of scope.

---

## Pages deployment for `site/` (#302)

Builds on #301's research (`docs/research/issue-301-github-pages-actions.md`), which
holds the mechanics: `upload-pages-artifact` + `deploy-pages` is the canonical pair, no
build step, no `.nojekyll`, relative asset URLs. This section settles what the research
left open: triggers, pins, the validation gate, and the one-time human settings.

### The workflow: `.github/workflows/pages.yml`

One file, hand-written, no generator:

- **Trigger**: `push` to `master` with `paths: [site/**, .github/workflows/pages.yml]`, plus `workflow_dispatch`. The paths filter keeps code-only pushes from redeploying an unchanged page; `paths` does not apply to `workflow_dispatch`, so manual runs always execute.
- **Permissions** (workflow level, per the deploy-pages README): `contents: read`, `pages: write`, `id-token: write`.
- **Concurrency** (verbatim from the starter template's shape): `group: "pages"`, `cancel-in-progress: false`.
- **Job `deploy`**: `environment: name: github-pages, url: ${{ steps.deployment.outputs.page_url }}`.
- **Steps** (major-tag pins only, never `@main` — current majors verified by #301):
  1. `actions/checkout@v7`
  2. `actions/configure-pages@v6` — **included but with no `enablement: true`**: enabling Pages needs a PAT, not `GITHUB_TOKEN`; the manual flip below covers it. It is included for its `base_path`/`base_url` outputs should they ever matter.
  3. `actions/upload-pages-artifact@v5` with `path: site` — packages `site/` verbatim; `site/index.html` must sit at the artifact top level. No build step.
  4. `actions/deploy-pages@v5` with `id: deployment`.

### The artifact: production page only

The deployed tree is the production page and nothing else: `index.html`, its assets,
and `preview.sh` (kept as an inert dev convenience; a plain text file ships harmlessly).
The rejected prototype variants `variant-b.html` and `variant-c.html` **leave `site/`
when the production page lands** — they stay on the `prototype/299-landing-page` branch
as the primary sources for their directions (per #299). No `.nojekyll` (Jekyll is a
branch-source feature, and `upload-pages-artifact@v5` drops dotfiles from artifacts
anyway). No `CNAME`. Asset references are relative (`style.css`, never `/style.css`) —
enforced by the validation gate below, not by convention.

### The validation gate (landing-page CI)

The map's fog item "whether CI should validate the landing page" is settled **yes**,
lightweight. Three checks, ~15 lines of inline Python (matching `ci.yml`'s existing
inline-heredoc style), living in two places:

1. `ci.yml` — a `site` job running the checks on every push and PR, so a base-path
   regression is caught before merge, not after deploy.
2. `pages.yml` — the same checks inline at the top of the `deploy` job, before upload,
   so a failing page never deploys even if CI elsewhere was ignored.

The duplication is deliberate: each workflow file stays self-contained.

The checks:

- `site/index.html` exists (entry file at the artifact top level).
- No root-absolute asset references: no `href="/`, `src="/`, or `url(/` in any
  `site/*.html` — the exact regression a project page under `/llm-discovery/` invites.
- Every local link (`href="..."`/`src="..."` not starting with a scheme or `#`) resolves
  to a file that exists under `site/`.

Deliberately not in scope: full HTML validity, external link checking, accessibility
linting. If they ever land, they go in `ci.yml`'s `site` job, not the deploy path.

### One-time human settings (prerequisites, not workflow steps)

The workflow cannot perform these for itself; both are recorded in the implementation
ticket's acceptance criteria and walked interactively via the `/wizard` skill at
implementation time:

1. Settings → Pages → Build and deployment → Source = **GitHub Actions**. The default
   `GITHUB_TOKEN` cannot set this; the first workflow run fails until it is flipped.
   Retry via `workflow_dispatch` after flipping.
2. Deployment protection rule on the `github-pages` environment restricting deploys to
   the default branch (`master`). Prevents a prototype branch from publishing.

### Acceptance criteria (implementation ticket)

1. `.github/workflows/pages.yml` exists with the trigger, permissions, concurrency,
   environment, and step pins above.
2. The validation gate exists in both `ci.yml` (new `site` job) and `pages.yml` (inline
   before upload); a deliberately broken check (e.g. a root-absolute `href`) fails both.
3. The two one-time settings are captured in a `/wizard` walkthrough; after flipping,
   a `workflow_dispatch` run deploys the prototype page to
   `https://soongguanleong.github.io/llm-discovery/` and it renders with working assets.
4. The production-page ticket (from #299's direction) removes the variant pages from
   `site/` before or with the deploy, leaving the artifact production-only.

---

## Repo metadata (#303)

Exact values, settled with the maintainer, to be applied with no further decisions. Per
the ticket, this section records values; only the `LICENSE` file has landed with this
resolution (explicit maintainer instruction). Everything else applies in the metadata
ticket.

### The values

1. **Description** (verbatim, 95 chars, within the 350 limit):
   `Discovers and evaluates cloud LLM models, judges coding relevance, emits a curated keep-list.`
2. **Topics** — exactly these 10, no extras (GitHub allows 20):
   `llm`, `ai`, `cli`, `python`, `llm-evaluation`, `benchmarks`, `models-dev`,
   `artificial-analysis`, `free-models`, `omniroute`.
3. **License**: MIT. `LICENSE` landed with this resolution:
   `Copyright (c) 2026-2027 Soong Guan Leong` (holder = the maintainer's git author
   identity, confirmed in interview; year range 2026–2027). The text is the canonical
   MIT wording, so GitHub licence detection picks it up automatically.
4. **Homepage**: `https://soongguanleong.github.io/llm-discovery/` — the exact
   project-page form: owner-pages host, `/llm-discovery/` path, trailing slash,
   lowercase. Same URL #302's deploy publishes to.
5. **Social preview image**: **deferred** to the landing page's meta layer (Open Graph
   tags, favicon, preview image — deliberately unresolved in #298 until the production
   page exists). GitHub falls back to the repo card until then.

### Applying (metadata ticket)

```bash
gh repo edit --description "Discovers and evaluates cloud LLM models, judges coding relevance, emits a curated keep-list."
gh repo edit --homepage "https://soongguanleong.github.io/llm-discovery/"
gh repo edit --add-topic llm --add-topic ai --add-topic cli --add-topic python \
  --add-topic llm-evaluation --add-topic benchmarks --add-topic models-dev \
  --add-topic artificial-analysis --add-topic free-models --add-topic omniroute
gh repo view --json description,homepageUrl,repositoryTopics,licenseInfo   # verify
```

### Acceptance criteria (implementation ticket)

1. `gh repo view --json description` returns the exact string above.
2. `repositoryTopics` is exactly the 10 locked topics, no extras.
3. `licenseInfo` reports MIT; the #300 README badge's MIT link now resolves to a real
   `LICENSE` blob.
4. `homepageUrl` is the exact URL above; it serves the page once #302's deploy has run.
5. Nothing set for the social preview; the landing-page meta-layer ticket owns it.

---

## Out of scope

Carried from map #298, plus the fog items #304 closed:

- **Rebuilding a product UI.** `ui/` was retired in ADR 0010; the landing page is static
  documentation hosting, not an app.
- **Committing catalog snapshots** so a fresh clone works with no network at all. Ruled
  out in favour of the Catalog Path.
- **Contributor onboarding** (`CONTRIBUTING.md`, issue templates). The audience is readers,
  not contributors.
- **Untracking `config/providers.yaml`.** Not a defect: AGENTS.md states the file is
  committed by design so user edits survive resets, and it holds env-var names, never
  secret values.
- **The landing page's meta/social layer** — Open Graph tags, favicon, social preview
  image, page title/description meta. Owned by the production landing-page ticket; GitHub
  falls back to the repo card until then (#303).
- **A `docs/reference/` index page.** Decided no — the README Reference list is the only
  index (see #300).
- **Cross-linking the landing page from the README or the reference docs.** Decided no —
  discovery runs through the repository homepage field (#303).
- **Heavier landing-page validation** — full HTML validity, external link checking,
  accessibility linting. Not in the deploy path; if they land, they go in `ci.yml`'s
  `site` job (#302).
