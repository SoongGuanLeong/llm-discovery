# Research #301 — Current GitHub Pages static deploy via Actions

Part of #298 Wayfinder: first-look experience — README, repo metadata, and a static landing page.
Blocks #302 "Specify the Pages deployment for `site/`".

## Question

What is the current canonical way to publish a static directory to GitHub Pages from GitHub Actions, and what does a project-page URL imply for asset paths?

Everything below is taken from primary sources — GitHub's own documentation, the action repositories' READMEs and `action.yml`/release notes, and this repository's own settings via the API. Versions verified 2026-09-19.

## Answer (TL;DR)

- **Yes**, `actions/upload-pages-artifact` + `actions/deploy-pages` is still the canonical pair. GitHub's docs, the deploy-pages README, and the official `pages/static.yml` starter template all use exactly this pair.
- **No build step is required.** `upload-pages-artifact` is a *packaging* action: it tars the directory you point it at and uploads it. Point it at `site/` verbatim. The only structural requirement is that `index.html` sits at the **top level of the artifact**.
- **Do not use `.nojekyll`.** Jekyll processing is a *branch publishing source* feature. It is not applied to an artifact deploy, and since `upload-pages-artifact@v4` dotfiles are excluded from the artifact by default anyway.
- **Use relative asset URLs.** The project site is served from `/llm-discovery/`, so root-absolute paths like `/style.css` resolve to `https://soongguanleong.github.io/style.css` and 404.
- **One manual UI step is required once:** Settings → Pages → Build and deployment → Source = **GitHub Actions**. The default `GITHUB_TOKEN` cannot set this; `configure-pages`'s `enablement: true` needs a PAT or GitHub App token.
- The repo is public with default branch `master`; Pages is currently **not enabled** (`has_pages: false`) and no environments exist yet.

## Findings

### 1. Canonical action set, versions, permissions, concurrency

The canonical pair is unchanged. `actions/deploy-pages`'s own README describes deploying "a Pages site previously uploaded as an artifact (e.g. using `actions/upload-pages-artifact`)" ([deploy-pages README](https://github.com/actions/deploy-pages/blob/main/README.md)). GitHub's docs list the same flow: checkout → build if required → `actions/upload-pages-artifact` → `actions/deploy-pages` ([Configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)).

Current released majors (latest release, published):

| Action | Latest release | Published | Major tag |
| --- | --- | --- | --- |
| `actions/upload-pages-artifact` | v5.0.0 | 2026-04-10 | `v5` |
| `actions/deploy-pages` | v5.0.1 | 2026-09-01 | `v5` |
| `actions/configure-pages` | v6.0.0 | 2026-03-25 | `v6` |
| `actions/checkout` | v7.0.1 | 2026-07-20 | `v7` |

Compatibility: `upload-pages-artifact` v3.0.0's release notes state "To deploy a GitHub Pages site which has been uploaded with this version … you must also use `actions/deploy-pages@v4` or newer" ([v3.0.0 release](https://github.com/actions/upload-pages-artifact/releases/tag/v3.0.0)). The v5/v5 pair is therefore fine.

**Caveat — the official starter template lags the latest majors.** `actions/starter-workflows/pages/static.yml` on `main` (fetched 2026-09-19) pins `actions/checkout@v4`, `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3`, `actions/deploy-pages@v5` ([static.yml](https://github.com/actions/starter-workflows/blob/main/pages/static.yml)). The README examples also still show older tags (`upload-pages-artifact@v3`, `deploy-pages@v4`). Both the starter's pins and the current majors are valid; the important thing is to pin a major tag rather than `@main`.

**Required permissions** (deploy job, from deploy-pages README "Security Considerations"): `pages: write` and `id-token: write`. The starter grants them at workflow level together with `contents: read` (needed by `actions/checkout`):

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

`pages: write` lets `GITHUB_TOKEN` create the Pages deployment via the API; `id-token: write` lets the job request the OIDC JWT that proves the workflow's branch/ref is allowed to deploy ([deploy-pages README, OIDC section](https://github.com/actions/deploy-pages/blob/main/README.md)).

**Required concurrency block** (verbatim from the starter template):

```yaml
# Allow only one concurrent deployment, skipping runs queued between the run in-progress and latest queued.
# However, do NOT cancel in-progress runs as we want to allow these production deployments to complete.
concurrency:
  group: "pages"
  cancel-in-progress: false
```

**Required environment** on the deploy job:

```yaml
environment:
  name: github-pages
  url: ${{ steps.deployment.outputs.page_url }}
```

`page_url` is the deploy-pages output ([deploy-pages README](https://github.com/actions/deploy-pages/blob/main/README.md)). The `github-pages` environment is created automatically if missing; GitHub recommends adding a protection rule so only the default branch can deploy ([Configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)).

`actions/configure-pages` is **optional** for a plain static directory. The starter includes it, but nothing in the upload/deploy pair requires it. Its useful outputs if you do include it are `base_path` (`/llm-discovery`), `base_url`, `origin`, `host` ([configure-pages action.yml](https://github.com/actions/configure-pages/blob/main/action.yml)).

### 2. Build step for a plain HTML directory

Not required. `upload-pages-artifact` is described as "A composite action for packaging and uploading an artifact that can be deployed to GitHub Pages" ([README](https://github.com/actions/upload-pages-artifact/blob/main/README.md)). Its `action.yml` simply runs `tar --directory "$INPUT_PATH" … .` and hands the tarball to `actions/upload-artifact` — there is no build, no generator, no Jekyll step ([action.yml](https://github.com/actions/upload-pages-artifact/blob/main/action.yml)).

Inputs ([action.yml](https://github.com/actions/upload-pages-artifact/blob/main/action.yml)):

- `path` (required, default `_site/`) — the directory to package. Use `site`.
- `name` (default `github-pages`) — leave as-is; `deploy-pages` looks for an artifact named `github-pages`.
- `retention-days` (default `1`).
- `include-hidden-files` (default `false`).

The only structural requirement: "If your publishing source is a GitHub Actions workflow, the artifact that you deploy must include the entry file at the top level of the artifact" ([Creating a GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)). So `site/index.html` must exist and `path: site` (not `path: site/subdir`).

Note: the starter's default is `path: '.'` ("Upload entire repository"). For this repo that would publish the whole repository root, so override it with `path: site`.

### 3. Base-path handling for `/llm-discovery/`

A project site is served at `http(s)://<owner>.github.io/<repositoryname>` ([What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)), i.e. `https://soongguanleong.github.io/llm-discovery/`. The site root is `/llm-discovery/`, **not** `/`. GitHub's docs make the mapping concrete: a file at `/about/contact-us.md` is served at `https://<user>.github.io/<repository>/about/contact-us.html` ([Creating a GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)).

**What breaks:** any reference that starts with `/` is resolved against the *domain* root, so it points outside the project path:

| Reference | Resolves to | Result |
| --- | --- | --- |
| `<link href="/style.css">` | `https://soongguanleong.github.io/style.css` | 404 |
| `<img src="/logo.svg">` | `https://soongguanleong.github.io/logo.svg` | 404 |
| `<a href="/docs/">` | `https://soongguanleong.github.io/docs/` | 404 |
| `<style>… url(/bg.png) …</style>` | `https://soongguanleong.github.io/bg.png` | 404 |

**The fix for a single hand-written page with inline CSS:** use relative URLs. Because `index.html` sits at the artifact root, which *is* the site root, a bare `style.css` or `./style.css` resolves to `https://soongguanleong.github.io/llm-discovery/style.css`. The same applies to relative `url(...)` inside an inline `<style>` block: CSS URLs resolve against the document URL, so `url(logo.svg)` in `site/index.html` resolves to `/llm-discovery/logo.svg`. If you later add a sub-page at `site/guide/index.html`, reference the shared asset as `../style.css`.

Other workable options, in order of preference:

1. **Relative paths** (recommended — no configuration, no coupling to the repo name).
2. Full absolute URLs (`https://soongguanleong.github.io/llm-discovery/style.css`) — correct but hardcodes the host and breaks on a custom domain or a rename.
3. `<base href="/llm-discovery/">` — makes root-absolute references resolve correctly, but adds a global coupling to the repo name for no benefit on a single page.

If you want to derive the prefix instead of hardcoding it, `configure-pages` exposes `base_path` (example from its docs: `"/my-repo"`), so the workflow could template it — unnecessary complexity for one page ([configure-pages action.yml](https://github.com/actions/configure-pages/blob/main/action.yml)).

### 4. `.nojekyll` and Jekyll processing

**Jekyll is a branch-publishing-source feature, and it is not applied to an uploaded artifact.**

- "If you publish your site from a source branch, GitHub Pages will use Jekyll to build your site by default. … Otherwise, disable the Jekyll build process by creating an empty file called `.nojekyll` in the root of your publishing source" ([Creating a GitHub Pages site — Static site generators](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)). The conditional is explicit: this is about branch sources.
- For an Actions source, the docs describe the workflow as producing the final output — "If required by your site, build any static site files" — and then uploading it; the artifact "must include the entry file at the top level", i.e. the artifact *is* the site ([Configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site), [Creating a GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)).
- The official `pages/static.yml` starter contains no Jekyll step ([static.yml](https://github.com/actions/starter-workflows/blob/main/pages/static.yml)); Jekyll has its own separate starters (`jekyll.yml`, `jekyll-gh-pages.yml`) and its own `actions/jekyll-build-pages` action.

**Therefore `.nojekyll` is not needed for this deploy, and it should not be added to `site/`.** Additional reason it is pointless there: since `upload-pages-artifact@v4.0.0` (2025-08-14), "hidden files (specifically dotfiles) will not be included in the artifact" — the tar step passes `--exclude=.[^/]*`, and the new `include-hidden-files` input defaults to `false` ([v4.0.0 release notes](https://github.com/actions/upload-pages-artifact/releases/tag/v4.0.0), [action.yml](https://github.com/actions/upload-pages-artifact/blob/main/action.yml), [README](https://github.com/actions/upload-pages-artifact/blob/main/README.md)). A `.nojekyll` file in `site/` would be silently dropped from the artifact unless `include-hidden-files: true`.

(For completeness: `.nojekyll` remains correct and necessary if this repo ever switches to publishing from a branch. It is only the Actions path where it is a no-op.)

### 5. Required repository settings

- **Settings → Pages → Build and deployment → Source must be `GitHub Actions`.** This is a documented configuration step for an Actions-based deploy ([Configuring a publishing source — Publishing with a custom GitHub Actions workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)). It is a one-time, human, UI step.
- The `github-pages` environment is created automatically if the repository does not already have one; GitHub recommends a deployment protection rule restricting it to the default branch ([same doc](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)).
- **The default `GITHUB_TOKEN` cannot flip that setting.** `actions/configure-pages` can attempt it via `enablement: true`, but its `action.yml` says the option "requires a token other than `GITHUB_TOKEN` to be provided" — a PAT with the `repo` scope or Pages write permission, or a GitHub App with `administration:write` and `pages:write` ([configure-pages action.yml](https://github.com/actions/configure-pages/blob/main/action.yml)). So do not expect a first run to enable Pages by itself; a maintainer must select **GitHub Actions** as the source once.
- Pages on GitHub Free requires a public repository ([What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)). This repo is public.
- No `gh-pages` branch, no dedicated publishing branch, and no `CNAME` file are needed. (A `CNAME` file does not configure a custom domain; that is a repository setting or API call — [Configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).)

Current state of this repository, read via the API on 2026-09-19: `private: false`, `default_branch: master`, `has_pages: false`, environments list empty. So the implementer will need to enable Pages (step above) before the first deploy succeeds.

### 6. Limits that matter for one small page

From [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits):

- Published site may be no larger than **1 GB**.
- Source repository recommended limit **1 GB**.
- Deployments **time out after 10 minutes**.
- Soft bandwidth limit **100 GB/month**.
- Soft limit of **10 builds per hour — "This limit does not apply if you build and publish your site with a custom GitHub Actions workflow."** So an Actions deploy is exempt from the build-frequency limit; there is no documented per-hour deploy cap for the Actions path.
- Rate limits may apply; over-limit requests get HTTP `429`.

From the artifact validation rules in the [upload-pages-artifact README](https://github.com/actions/upload-pages-artifact/blob/main/README.md):

- The artifact must be named `github-pages` and be a single gzip archive containing a single tar.
- The tar must be **under 10 GB** (hard, unofficial), with **under 1 GB recommended** because the official Pages limit is 1 GB and large tarballs risk the 10-minute deploy timeout.
- The tar must **not contain symbolic or hard links**, and must contain only files and directories. (Note the related docs caveat: a repository containing symlinks must publish via an Actions workflow, not a branch — [Configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).)
- Artifact `retention-days` defaults to `1`; irrelevant to serving the site, only to how long the run artifact is kept.

**Allowed file types:** there is no per-file allowlist. GitHub Pages serves static files and supports 750+ MIME types derived from the [mime-db](https://github.com/jshttp/mime-db) project, with no per-file or per-repo MIME overrides; server-side languages (PHP, Ruby, Python) are not supported ([Creating a GitHub Pages site — MIME types](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)). None of these limits are remotely relevant to a single hand-written page.

## Recommended workflow

One file, `.github/workflows/pages.yml`, deploying `site/` verbatim:

```yaml
name: Deploy site to GitHub Pages

on:
  push:
    branches: [master]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Upload site directory as artifact
        uses: actions/upload-pages-artifact@v5
        with:
          path: site
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v5
```

Notes for the implementer:

- `path: site` packages `site/` verbatim; `site/index.html` becomes the entry file at the artifact top level.
- `actions/configure-pages` is omitted deliberately — it is optional for a plain static directory. Add it only if you want the `base_path`/`base_url` outputs.
- Version pins: this uses the current majors. If you would rather mirror GitHub's maintained starter template exactly, its pins are `checkout@v4`, `configure-pages@v5`, `upload-pages-artifact@v3`, `deploy-pages@v5` ([static.yml](https://github.com/actions/starter-workflows/blob/main/pages/static.yml)). Either set is valid; pinning a major tag is what matters.
- Do not add `.nojekyll` to `site/`. Do not use root-absolute asset paths.

## Open item for #302

The one thing the workflow cannot do for itself is the first-time repository setting: **Settings → Pages → Build and deployment → Source = GitHub Actions**. This should be captured as a setup prerequisite in #302's acceptance criteria, since the first workflow run will fail (or deploy nothing) until a maintainer flips it.
