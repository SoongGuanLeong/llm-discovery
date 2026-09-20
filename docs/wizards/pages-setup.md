# One-time GitHub Pages setup for `site/` (issue #307)

`.github/workflows/pages.yml` deploys `site/` to GitHub Pages, but two settings
must be flipped by a human with dashboard access first — the workflow's
`GITHUB_TOKEN` is not allowed to change either one. Until step 1 is done, the
first run fails at `deploy-pages`; until step 2 is done, any branch could
publish the page.

Run the walkthrough interactively:

```bash
./docs/wizards/pages-setup.sh
```

It opens each URL in your browser and tells you exactly what to click. Do this
**after** `pages.yml` has been merged to `master`. The same steps, in prose,
for anyone without a terminal handy:

## 1. Pages source → GitHub Actions

<https://github.com/SoongGuanLeong/llm-discovery/settings/pages>

Settings → Pages → **Build and deployment** → **Source** → select
**GitHub Actions**. That is the only change; no branch source, no custom
domain (the artifact ships without a `CNAME`).

## 2. Deployment protection rule on the `github-pages` environment

<https://github.com/SoongGuanLeong/llm-discovery/settings/environments>

- If no `github-pages` environment is listed yet, click **New environment**
  and create it with the exact name `github-pages` (this is the environment
  `pages.yml` declares).
- Add the deployment protection rule **Deployment branches and tags**.
- Choose **Selected branches and tags** and add the branch `master`. This
  prevents a prototype branch from publishing via `workflow_dispatch`.

## 3. Trigger and verify the first deploy

- <https://github.com/SoongGuanLeong/llm-discovery/actions/workflows/pages.yml>
  → **Run workflow** → `workflow_dispatch` on `master` (or re-run the failed
  earlier run — the source flip in step 1 is the usual fix).
- Wait for the `deploy` job to go green; its summary shows the page URL.
- Open <https://soongguanleong.github.io/llm-discovery/> and confirm the page
  renders with working assets and the links between `index.html` and the
  variant pages resolve.

If the run fails at **Validate site/**, the log names the offending file and
reference — fix it in `site/` (root-absolute `href="/…"`, `src="/…"`,
`url(/…)` or a dead local link), not by deleting the gate.

Spec context: `docs/specs/first-look.md`, section “Pages deployment for
`site/` (#302)”.
