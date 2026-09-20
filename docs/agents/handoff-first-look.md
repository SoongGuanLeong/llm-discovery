# Handoff: llm-discovery — first-look experience (Wayfinder #298)

Written 2026-09-20 from the prior session. A fresh agent picks up here to finish the
wayfinder map and run the implementation phase.

> **Status update (same day, second session):** this note was recreated from the prior
> session's read copy after the untracked original vanished from disk, and committed with
> the #304 assembly. In that session #304 was assembled and map #298 closed. The "What
> remains" list below is the *prior* session's state, kept as written; item 1 is done,
> item 2 (ticket-splitting and `/implement`) is the next phase. Settled decisions: no
> `docs/reference/` index page; no README/reference cross-links to the landing page;
> `docs/specs/` declared the standing spec home (AGENTS.md); the page's meta/social layer
> deferred to the production landing-page ticket.

## Working context

- Repo: `SoongGuanLeong/llm-discovery` (public, default branch `master`).
- Current branch: `prototype/299-landing-page` (the #299 throwaway prototype branch).
- **Three local commits are unpushed**: `5d53ce3` (spec #300), `990d373` (spec #302),
  `937245a` (spec #303 + `LICENSE`). Issue comments and the map body link to
  `blob/prototype/299-landing-page/...` URLs, so push (with maintainer permission) or
  those links 404 until pushed.
- Session style note: the previous session ran in a compressed "caveman" chat register;
  all persisted artifacts (specs, comments, commits) were written in normal prose and
  that convention continues.

## State of the wayfinder map (#298)

All decision tickets are resolved and closed on GitHub: #299 (landing page direction,
prototype on this branch), #301 (Pages research), #300 (README split + Catalog Path +
reference docs), #302 (Pages deployment), #303 (repo metadata). Every decision is
recorded in one of two places — do not re-derive them:

- `docs/specs/first-look.md` (on this branch) — the hand-off spec: #300, #302, #303
  sections carry the locked decisions and per-implementation-ticket acceptance criteria.
- Issue comments on #299, #300, #302, #303, plus the map issue #298 body ("Decisions
  so far" now has a line per closed ticket).
- `docs/research/issue-301-github-pages-actions.md` lives on
  `origin/research/github-pages-actions` (canonical Pages mechanics: action pair,
  versions, permissions, base-path rules, one-time settings).

Landed code/docs changes so far: `CONTEXT.md` gained the **Catalog Path** glossary
entry (after Golden Path, deliberately not a synonym); `LICENSE` (MIT, 2026-2027 Soong
Guan Leong) now exists. The README itself is untouched — that is the implementation
phase's job.

## What remains

1. **#304 (open)** — assemble/finalise `docs/specs/first-look.md`: reconcile the
   status table, sweep for cross-section contradictions, and settle any remaining
   "Not yet specified" fog the maintainer wants settled (reference-docs index page,
   README/landing-page cross-linking, heavier page validation). Close the map when done.
2. **Merge to the main flow** — the map hands off, it does not build: turn the spec's
   four implementation surfaces into tracer-bullet tickets with blocking edges
   (README split + reference docs; production landing page + Pages deploy workflow +
   wizard settings; metadata apply). Then `/implement` each ticket fresh, working
   blockers first.
3. Known environment facts: `gh` CLI is authenticated; run tests via `uv run pytest`
   (no bare `python` on PATH); two failures in `tests/test_issue231_weak_recovery.py`
   are pre-existing on a clean tree and unrelated; Pages is not yet enabled
   (`has_pages: false`) — flipping Settings → Pages → Source = "GitHub Actions" is a
   documented human step inside the #302 acceptance criteria, walked via the wizard skill.
4. No secrets or PII are involved anywhere in this work; nothing to redact. The repo's
   `config/providers.yaml` is user-only — never read-for-edit or modify it (AGENTS.md rule).

## Suggested skills

The next agent should load these via the Skill tool, in roughly this order:

- `wayfinder` — map conventions: how #298's tickets, labels, and the "Decisions so far"
  body are maintained; how a map closes and hands off.
- `grilling` — #304 and any remaining fog items are decision tickets; interview the
  maintainer rather than guessing.
- `domain-modeling` — vocabulary source if any spec wording touches CONTEXT.md terms.
- `to-tickets` (if available in the session's skill set; otherwise follow the map's
  handoff note) — split the spec into blocking-edged implementation tickets.
- `implement` — per ticket; it drives `tdd` internally and closes with `code-review`.
- `tdd` — the README split has an explicit test-change plan in the spec
  (`tests/test_readme_golden_path.py` updates named per test).
- `wizard` — only at implementation time, for the two one-time Pages UI settings.
- `caveman` — if the maintainer re-invokes it; otherwise normal prose.

## First actions

1. `git log --oneline -5` and `gh issue view 304` to confirm state.
2. Decide with the maintainer whether to push the branch before assembling #304.
3. Work #304, then close map #298 and start the ticket-splitting phase.
