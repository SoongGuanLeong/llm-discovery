# Research: Podman Quadlet vs Docker Compose vs npx for local Bifrost (issue #111)

Part of #109 — Wayfinder Map: Bifrost local gateway for 3 tier endpoints (flash/max/contributor_free) from data/results.

## Question

What is the best local install method for Bifrost given user preference for Podman Quadlet? Compare Podman Quadlet (.container/.volume quadlets, auto-update, systemd user service), Docker Compose, npx binary, and plain systemd on: persistence of app-dir/config.json, auto-restart, port 8080 binding, secrets/env handling, file-watcher reload on config change, upgrade path, and dev ergonomics on this host (podman config issue noted). Deliver recommendation with pros/cons and a minimal Quadlet example (or alternative if better), including volume mount for config and env handling.

## Method

Primary sources only — fetched 2026-09-05:

- Bifrost docs: docs.getbifrost.ai/deployment-guides/overview, runtime-contract, quickstart/gateway/setting-up, deployment-guides/config-json and /config-json/source-of-truth
- Podman Quadlet docs: podman-systemd.unit.5, podman-container.unit.5, podman-volume.unit.5, podman-auto-update.1 (docs.podman.io)
- Host capability checks: podman --version/info, systemctl --user, loginctl, ls /run/user/1000, which docker/npx/node, ls ~/.config/containers/systemd
- Bifrost GitHub maximhq/bifrost README

No secondary blogs or AI summaries; all claims trace to those pages or shell output.

## Findings

### 1. Bifrost runtime contract (applies to all methods)

**Image and process** (runtime-contract): Published as docker.io/maximhq/bifrost:<VERSION> (also maximhq/bifrost short), linux/amd64+arm64, runs as UID 1000. Entrypoint /app/main -app-dir APP_DIR -port APP_PORT -host APP_HOST -log-level LOG_LEVEL. HTTP on 8080/TCP (APP_PORT); health at GET /health on same port (200 when ready, 503 if store check fails); metrics at GET /metrics. Defaults: APP_HOST=0.0.0.0, APP_PORT=8080, APP_DIR=/app/data, LOG_LEVEL=info, LOG_STYLE=json. GOMEMLIMIT optional (~90% container memory).

**Storage** (overview + runtime-contract): OSS config_store and logs_store each SQLite or PostgreSQL 16+. Enterprise config must be Postgres 16+. SQLite needs writable persistent volume at /app/data (holds config.json, config.db, logs.db). One process per SQLite file — no shared SQLite across replicas. Both stores = Postgres -> no persistent /app/data volume; container still needs app-dir for startup config.json but DB holds durability. Helm with storage.mode: postgres skips the PVC. For local OSS single instance with declarative tiers from data/results -> config.json, expected pattern is SQLite or file-only with host directory mounted at /app/data. Quickstart canonical: docker run -v $(pwd)/data:/app/data maximhq/bifrost.

**Configuration modes** (quickstart + config-json): No config.json -> default SQLite config.db in app-dir, UI/API edits persisted. config.json + config_store omitted/enabled -> DB-backed reconciliation (source_of_truth: split by default; unchanged file rows preserve DB edits; changed rows overwrite; config_hash auto-managed). config_store.enabled:false -> file-only (in-memory); UI/API config surfaces disabled; file change requires restart. source_of_truth: config.json makes present sections authoritative and prunes DB-only rows (empty array [] prunes; missing section leaves DB alone).

**Secrets** (runtime-contract + config-json): Never inline secrets. Use env.VARIABLE_NAME refs: "encryption_key": "env.BIFROST_ENCRYPTION_KEY", "value": "env.OPENAI_API_KEY". Required: stable BIFROST_ENCRYPTION_KEY across restarts/upgrades (losing it breaks encrypted persisted values), provider keys (OPENAI_API_KEY etc.), BIFROST_SETUP_TOKEN for first-admin bootstrap (also env). Enterprise also supports vault.path refs. In Docker/Quadlet values come from -e / Environment= / EnvironmentFile=; in K8s from Secrets.

**Networking and health** (runtime-contract): Single HTTP listener 8080 must be exposed; ingress must not buffer SSE, must allow long-lived streams and WebSocket upgrades, must preserve Host/X-Forwarded-*. Helm probes: readiness initial 10s / period 10s / timeout 5s; liveness 30s/30s/5s; termination grace 60s (15s preStop) + 30s internal SIGTERM cleanup. Any local unit should approximate via Restart + HealthCmd.

**Upgrade** (runtime-contract): Pin image version/digest for rollback. Migrations run at startup (Postgres uses advisory lock); rolling back the image does not reverse migrations. Keep DB backup + encryption key before upgrade.

### 2. The three (four) local methods

#### A. npx binary (npx -y @maximhq/bifrost)

Source: quickstart#1-choose-your-setup-method, #2-configuration-flags, GitHub README.

- Flags: -port 8080 (APP_PORT), -host 0.0.0.0, -app-dir ./my-bifrost-data, -log-level, -log-style.
- app-dir default: OS config dir — Linux/macOS ~/.config/bifrost, Windows %APPDATA%/bifrost. Contains config.json, config.db, logs.db. With npx -app-dir ./data the local folder is the app-dir (no volume mount).
- Persistence: filesystem directory; survives restarts. File-only mode uses config.json in that dir with config_store.enabled:false.
- Auto-restart: none — process dies with shell. Needs external supervisor (systemd user service, pm2, manual restart). No container health check.
- Port 8080: binds directly on host; no NAT. Conflict if something else holds 8080.
- Secrets/env: inherits shell env + optional env-file loaded by caller; config.json env.VAR indirection (vars must be in the npx process env).
- Reload on config change: no file watcher. File-only requires kill and restart; DB-backed split reconciles on next start (hash). For npx you just kill and rerun or systemctl --user restart.
- Upgrade: npx -y @maximhq/bifrost@<version> or npm update; no image pull. Quickest iteration loop.
- Dev ergonomics: fastest for edit-file -> restart -> test. No build context, no daemon. Requires Node 24 + npx (present on this host: npx 11.17.0, node v24.19.0 via nvm). Downsides: no isolation, no cgroup limits via systemd unless you write a unit anyway, logs to stdout/journal only if wrapped, and you must manage the process yourself.

#### B. Docker Compose (docker compose up -d)

Source: quickstart Docker section + runtime-contract + common compose pattern.

Canonical fragment:

```yaml
services:
  bifrost:
    image: docker.io/maximhq/bifrost:<VERSION>
    ports: ["8080:8080"]
    volumes: ["./data:/app/data"]
    env_file: [.env]
    environment:
      APP_PORT: "8080"
      APP_HOST: "0.0.0.0"
      LOG_LEVEL: info
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
```

- Persistence: volumes: ["./data:/app/data"] bind-mount (dev) or named volume bifrost-data:/app/data (more portable). Same /app/data and config.json semantics as Quadlet.
- Auto-restart: restart: unless-stopped (or always) — Docker daemon restarts on failure/reboot. Healthcheck approximates Helm readiness.
- Port 8080: ports: ["8080:8080"] publishes to host; compose handles NAT and conflict detection.
- Secrets/env: env_file + environment; config.json env. refs resolve inside container. .env lives next to compose file — familiar pattern.
- Reload on config change: no auto-reload. File-only mode needs docker compose restart bifrost. DB-backed split needs container restart to reconcile; compose does not watch the file.
- Upgrade: docker compose pull && docker compose up -d (pin tag/digest in compose file). Straightforward.
- Dev ergonomics: excellent — one file, one command, logs via docker compose logs -f, portable, widely documented. Requires Docker Engine + compose plugin (absent on this host: docker: command not found). Podman can provide podman-compose or docker-compat shim, but that is extra and still needs working Podman storage.

#### C. Podman Quadlet (.container + .volume + systemd user service)

Source: podman-systemd.unit.5, podman-container.unit.5, podman-volume.unit.5, podman-auto-update.1; host checks below.

**What Quadlet is:** Systemd generator (/usr/lib/systemd/system-generators/podman-system-generator) that reads declarative unit files (extensions .container, .volume, .network, .pod, .kube, .image, .build) from system paths (/etc/containers/systemd, /run/containers/systemd) and user paths ($XDG_RUNTIME_DIR/containers/systemd/, ~/.config/containers/systemd/, /etc/containers/systemd/users/${UID}). At systemctl daemon-reload or boot it generates real .service units with ExecStart=podman run .... All standard [Unit]/[Service]/[Install] keys pass through to systemd, so you get native systemd semantics (dependencies, cgroup limits, Restart=, TimeoutStartSec, etc.).

**Key [Container] options for Bifrost (podman-container.unit.5 table):**

| Concern | Quadlet key | podman run equiv | Use for Bifrost |
|---|---|---|---|
| Image | Image=docker.io/maximhq/bifrost:VERSION | image spec | Pin version (or digest) — required for AutoUpdate=registry |
| Volumes | Volume=/host/path:/app/data:Z or Volume=bifrost-data.volume:/app/data | -v | Mount app-dir; :Z for SELinux relabel on Fedora/RHEL |
| Ports | PublishPort=8080:8080 | -p | Expose Bifrost HTTP |
| Env | Environment=APP_PORT=8080, EnvironmentFile=%h/.config/bifrost/env | --env, --env-file | Secrets + APP_* |
| Auto-update | AutoUpdate=registry | --label io.containers.autoupdate=registry | Needs fully-qualified image ref (enforced) |
| Health | HealthCmd=CMD-SHELL curl -f http://localhost:8080/health, HealthInterval=30s | --health-cmd | Optional systemd-level health |
| Extras | PodmanArgs=--pull=always | pass-through | Rarely needed |

**[Volume] unit** (podman-volume.unit.5): generator runs podman volume create. A file bifrost-data.volume creates volume systemd-bifrost-data (or custom VolumeName=). Containers can then use Volume=bifrost-data.volume:/app/data and Quadlet auto-orders the dependency.

**AutoUpdate** (podman-auto-update.1): containers with AutoUpdate=registry (or label) are updated by the systemd timer podman-auto-update.service / podman-auto-update.timer when images change in registry. Requires enabling the timer (systemctl --user enable --now podman-auto-update.timer). Not enabled by default; for local pinned version use, disable auto-update or set consciously.

**Systemd integration:**
- Enable at boot: add [Install] WantedBy=default.target (user) or multi-user.target (system). Generator maps this as if systemctl enable had run; you cannot systemctl enable the transient generated service directly — you enable via quadlet file + daemon-reload.
- Restart: set in [Service] (passes through), e.g. Restart=on-failure + RestartSec=5s. Default generated service is Type=notify for containers.
- Drop-ins: foo.container.d/10-override.conf merges alphabetically; useful for env overrides without editing base file.
- Startup timeout: image pull can exceed systemd 90s default. Add [Service] TimeoutStartSec=900 for first pull on slow link.

#### D. Plain systemd unit (without Quadlet) — for completeness

Hand-written bifrost.service with ExecStart=/usr/bin/podman run ... or ExecStart=npx -y @maximhq/bifrost ... Functionally identical to Quadlet but verbose, no generator. Not preferred when Quadlet exists.

### 3. Head-to-head comparison

| Dimension | Podman Quadlet (preferred) | Docker Compose | npx binary |
|---|---|---|---|
| Persistence: app-dir/config.json volume | bind Volume=./data:/app/data:Z or named Volume=bifrost-data.volume:/app/data. Same /app/data contract. Postgres-only: minimal mount for config.json. | volumes: ["./data:/app/data"] or named volume. Identical. | No volume — host dir is app-dir (~/.config/bifrost or -app-dir ./data). Simplest for file-only. |
| Auto-restart | systemd: [Service] Restart=on-failure + [Install] WantedBy=default.target. Survives crash+reboot (if linger=yes). Health: HealthCmd + systemd restart. | restart: unless-stopped via Docker daemon. healthcheck. | None alone. Needs systemd/pm2 wrapper. |
| Port 8080 binding | PublishPort=8080:8080 (rootless via pasta/slirp4netns). | ports: ["8080:8080"] NAT. | Direct bind; no NAT. |
| Secrets / env handling | Environment= + EnvironmentFile= (%h/.config/bifrost/bifrost.env, chmod 600). config.json env.VAR refs. Drop-in overrides. | env_file: + environment:. Familiar merge. | Inherits shell env; same env.VAR indirection. |
| Reload on config change | No watcher — same as Docker. File-only needs systemctl --user restart. DB-backed split reconciles on next start (hash). Optional .path watcher via systemd. | No watcher. docker compose restart. | No watcher. kill and rerun. Fastest cycle. |
| Upgrade path | podman pull + daemon-reload + restart. Pin tag/digest in Image=. AutoUpdate=registry opt-in via timer. Migrations at start; rollback does not reverse DB. | docker compose pull && up -d. Pin tag/digest. | npx @<version> — fastest, no image layer. |
| Dev ergonomics | Declarative units in git, journalctl --user -u bifrost -f, systemd health. Slight learning curve; first-pull timeout needs TimeoutStartSec. | docker compose logs -f, widely known, portable. | Best for solo dev loop: npx then curl. Zero daemon. But no cgroup/replica parity. |
| Dependencies | Podman + cgroup v2 + systemd user bus + lingering. | Docker Engine + compose plugin. | Node 24 + npx. |

### 4. Host capability — this machine (2026-09-05 checks)

```
$ podman --version
Failed to obtain podman configuration: set sticky bit on: chmod /run/user/1000/libpod: read-only file system

$ systemctl --user status
Failed to connect to user scope bus via local transport: No data available

$ ls /run/user/1000/libpod
# dir exists but chmod fails (read-only FS — sandbox overlay)

$ ls ~/.config/containers/systemd/
config.yaml   # not a quadlet file

$ which docker; docker --version
bash: line 1: docker: command not found

$ which npx; npx --version; node --version
/home/soongguanleong/.nvm/versions/node/v24.19.0/bin/npx; 11.17.0; v24.19.0

$ loginctl show-user soongguanleong
UID=1000 Linger=no State=active RuntimePath=/run/user/1000
```

Interpretation:
- Podman storage broken in this session: /run/user/1000/libpod is read-only (sandboxed). Quadlet generator still parses files, but podman run/pull/volume create will fail until storage writable. Matches ticket note podman config issue noted.
- systemd --user bus unavailable via this shell transport (DSH/WSL flatpak-style sandbox proxies dbus). XDG_RUNTIME_DIR=/run/user/1000 exists and bus socket exists, but systemctl --user cannot talk to host user manager from inside this process. Quadlet units in ~/.config/containers/systemd/ would be picked up on host systemd, not inside sandbox — normal.
- Linger=no: even when fixed, user services will not start at boot until user logs in. Fix: sudo loginctl enable-linger $USER.
- Docker absent. npx present via nvm.

Conclusion: Quadlet is architecturally available in principle (Podman + generator + ~/.config/containers/systemd path per docs), but currently blocked by read-only storage + transient dbus transport in this sandbox. Docker Compose blocked by missing docker. npx is unblocked today.

### 5. Recommendation

**Prefer Podman Quadlet for the local Bifrost gateway; use npx as immediate fallback until host podman storage + lingering are fixed; Docker Compose is second-fallback only if Docker is installed.**

Rationale:
1. Repo prefers containers (all deployment docs describe container image as primary; npx is 30-sec eval not production parity). Quadlet gives container parity with systemd auto-restart, health, and persistent /app/data — closest to hosted env while staying local.
2. Quadlet vs Compose: both satisfy persistence/port/secrets/upgrade equally. Quadlet wins on no daemon (rootless Podman), systemd-native logs/restart, declarative units in git, and stated user preference. Compose wins on community familiarity but needs Docker Engine (absent). No functional gap forces compose over quadlet.
3. npx wins on speed (sub-second restart, no mount/pull) and is the only unblocked method today. Loses on parity: no isolation, no digest pinning, manual supervision. Acceptable for ticket #110 exploration but not the final auto-restart gateway envisioned in map #109.
4. Host issue is fixable, not architectural (chmod overlay and dbus transport are sandbox artifacts). Fix steps restore Quadlet without changing Bifrost config.

Therefore decision for #109: implement the gateway assuming Quadlet (file-only config.json + bind or named volume on /app/data), document the npx fallback for contributors whose podman is broken, and optionally keep a compose.yaml as convenience but not the primary.

### 6. Minimal Quadlet example (primary) + fallback

All paths are user-scoped (no sudo). Replace <VERSION> with pinned tag (e.g. 1.3.9) or digest.

**File layout**

```
# repo-root (gitignored artifact dir for generated config)
./data/bifrost/              # host dir that becomes /app/data
./data/bifrost/config.json
~/.config/bifrost/bifrost.env   # chmod 600 — secrets outside repo
~/.config/containers/systemd/
  bifrost-data.volume
  bifrost.container
```

```bash
mkdir -p ~/.config/containers/systemd
mkdir -p ./data/bifrost
chmod 700 ./data/bifrost
cat > ~/.config/bifrost/bifrost.env <<'EOF'
BIFROST_ENCRYPTION_KEY=replace-with-openssl-rand-base64-32
OPENAI_API_KEY=sk-__REPLACE__
ANTHROPIC_API_KEY=sk-ant-__REPLACE__
# add remaining 20+ provider keys per secrets inventory (#114)
BIFROST_SETUP_TOKEN=local-bootstrap-token
EOF
chmod 600 ~/.config/bifrost/bifrost.env
```

**~/.config/containers/systemd/bifrost-data.volume** (named-volume option — omit if using bind):

```ini
# ~/.config/containers/systemd/bifrost-data.volume
[Volume]
VolumeName=bifrost-data
Label=app=bifrost
```

If you prefer bind so ./data/bifrost/config.json is directly the app-dir, omit this file and use the bind Volume= line below.

**~/.config/containers/systemd/bifrost.container** (primary — bind mount variant):

Bind mount is recommended for this repo because data/results/*.yaml -> config.json writes to a host directory you want to inspect. Named-volume variant is in comments.

```ini
# ~/.config/containers/systemd/bifrost.container
[Unit]
Description=Bifrost AI Gateway (Podman Quadlet)
After=network-online.target
Wants=network-online.target
# If using named volume:
# After=bifrost-data.volume
# Requires=bifrost-data.volume

[Container]
Image=docker.io/maximhq/bifrost:1.3.9
ContainerName=bifrost
# Bind-mount host dir to /app/data (:Z for SELinux relabel; omit on plain Ubuntu if desired)
Volume=/home/soongguanleong/projects/llm-discovery/data/bifrost:/app/data:Z
# Named-volume alternative (uncomment, comment bind line):
# Volume=bifrost-data.volume:/app/data
PublishPort=8080:8080
EnvironmentFile=%h/.config/bifrost/bifrost.env
Environment=APP_PORT=8080
Environment=APP_HOST=0.0.0.0
Environment=LOG_LEVEL=info
Environment=LOG_STYLE=pretty
HealthCmd=CMD-SHELL curl -f http://localhost:8080/health || exit 1
HealthInterval=30s
HealthTimeout=5s
HealthRetries=3
HealthStartPeriod=10s
# AutoUpdate=registry  # only with podman-auto-update.timer enabled

[Service]
Restart=on-failure
RestartSec=5s
TimeoutStartSec=900

[Install]
WantedBy=default.target
```

Notes:
- EnvironmentFile path: Quadlet notes relative paths with % must be prefixed with ./; %h/.config/... is absolute-by-specifier and works; verify via /usr/lib/systemd/system-generators/podman-system-generator --user --dryrun or systemd-analyze --user --generators=true verify bifrost.service.
- Image= must be fully-qualified docker.io/... for AutoUpdate=registry (enforced) or generator refuses auto-update.
- :Z vs :z: :Z private relabel, :z shared; omit on non-SELinux hosts harmless.
- Verify generation: systemd-analyze --user --generators=true verify bifrost.service

**Bifrost config.json skeleton for file-only mode** (at ./data/bifrost/config.json -> /app/data/config.json):

```json
{
  "$schema": "https://www.getbifrost.ai/schema",
  "encryption_key": "env.BIFROST_ENCRYPTION_KEY",
  "client": { "enable_logging": true, "drop_excess_requests": false },
  "providers": {
    "openai": {
      "keys": [{ "name": "primary", "value": "env.OPENAI_API_KEY", "models": ["*"], "weight": 1.0 }]
    }
  },
  "config_store": { "enabled": false }
}
```

Expand providers to the 23 providers / 126 keep records (flash/max/contributor_free) via the generated transform (#110/#113). Keep config_store.enabled:false so file is sole source; changes require restart (systemctl --user restart bifrost).

**Lifecycle commands (user Quadlet)**

```bash
systemctl --user daemon-reload
systemctl --user start bifrost.service
systemctl --user status bifrost.service
journalctl --user -u bifrost -f
curl -f http://localhost:8080/health

# Enable at boot (login-based); for boot without login:
sudo loginctl enable-linger $USER
systemctl --user enable --now podman-auto-update.timer  # only if AutoUpdate=registry

# Reload after editing config.json (file-only mode)
systemctl --user restart bifrost.service

# Optional auto-watch via .path unit
cat > ~/.config/containers/systemd/bifrost-restart.path <<'EOF2'
[Unit]
Description=Restart Bifrost when config.json changes
[Path]
PathChanged=%h/projects/llm-discovery/data/bifrost/config.json
Unit=bifrost.service
[Install]
WantedBy=default.target
EOF2
systemctl --user daemon-reload
systemctl --user enable --now bifrost-restart.path

# Upgrade
podman pull docker.io/maximhq/bifrost:1.4.0
# edit Image= line, then:
systemctl --user daemon-reload
systemctl --user restart bifrost.service
# rollback = revert Image tag + restart; DB migrations not reversed — restore backup if needed

# Debug generator failures
/usr/lib/systemd/system-generators/podman-system-generator --user --dryrun 2>&1 | grep -A5 bifrost
systemd-analyze --user --generators=true verify bifrost.service
```

### 7. Fallback if Quadlet unsuitable

When to fall back: Podman storage remains read-only, systemd user manager unreachable, or team needs zero-systemd onboarding.

**Fallback 1 — npx (immediate on this host)**

```bash
mkdir -p ./data/bifrost
cat > ./data/bifrost/config.json <<'EOF3'
{
  "$schema": "https://www.getbifrost.ai/schema",
  "encryption_key": "env.BIFROST_ENCRYPTION_KEY",
  "config_store": { "enabled": false },
  "providers": { "openai": { "keys": [{"name":"primary","value":"env.OPENAI_API_KEY","models":["*"]}] } }
}
EOF3
export $(cat ~/.config/bifrost/bifrost.env | xargs)
npx -y @maximhq/bifrost -app-dir ./data/bifrost -port 8080 -log-level info -log-style pretty
curl -f http://localhost:8080/health
```

Plain systemd wrapper for npx (not Quadlet):

```ini
# ~/.config/systemd/user/bifrost-npx.service
[Unit]
Description=Bifrost via npx (fallback)
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=%h/projects/llm-discovery
EnvironmentFile=%h/.config/bifrost/bifrost.env
ExecStart=%h/.nvm/versions/node/v24.19.0/bin/npx -y @maximhq/bifrost -app-dir %h/projects/llm-discovery/data/bifrost -port 8080
Restart=on-failure
RestartSec=5s
[Install]
WantedBy=default.target
```

**Fallback 2 — Docker Compose (if Docker installed)**

```yaml
# compose.yaml at repo root
services:
  bifrost:
    image: docker.io/maximhq/bifrost:1.3.9
    container_name: bifrost
    ports: ["8080:8080"]
    volumes: ["./data/bifrost:/app/data"]
    env_file: ["./bifrost.env"]
    environment:
      APP_PORT: "8080"
      APP_HOST: "0.0.0.0"
      LOG_LEVEL: info
      LOG_STYLE: pretty
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
```

```bash
docker compose up -d && docker compose logs -f
curl -f http://localhost:8080/health
docker compose restart bifrost  # after config edit
```

Podman users can run same file via podman-compose once Podman storage is fixed.

**Fixing Quadlet on this host to leave fallback:**
- Outside DSH sandbox: podman system reset or rm -rf $XDG_RUNTIME_DIR/libpod then podman info; verify podman info --format '{{.Host.CgroupsVersion}}' shows v2.
- sudo loginctl enable-linger $USER for boot without login.
- Verify systemctl --user is-system-running returns running from a normal terminal (not proxied DSH bus).
- If still blocked, stay on npx fallback and treat Quadlet as documented target for #109.

### 8. Open questions for next tickets

- #110/#113: file-only (config_store.enabled:false) vs DB-backed split — Recommendation: file-only for local tier gateway (deterministic, GitOps-friendly, explicit restart). Switch to split only if UI editing is desired.
- #114: secrets inventory for 126 keeps — enumerate which provider keys actually needed for flash/max/contributor_free tiers.
- Port 8080 default but may conflict; should BIFROST_PORT override via APP_PORT/PublishPort be documented?
- Auto-reload: ship optional .path watcher or keep manual restart as explicit contract?

## References

- docs.getbifrost.ai/deployment-guides/overview (image, storage matrix, APP_DIR, port 8080, health)
- docs.getbifrost.ai/deployment-guides/runtime-contract (docker.io/maximhq/bifrost, UID 1000, APP_* env, SQLite vs Postgres 16+, probes, upgrade/migrations)
- docs.getbifrost.ai/quickstart/gateway/setting-up (npx vs Docker, -app-dir defaults, config modes file-only vs DB-backed)
- docs.getbifrost.ai/deployment-guides/config-json and /config-json/source-of-truth (env. refs, config_store.enabled:false, source_of_truth split/config.json, hash reconciliation)
- docs.podman.io podman-systemd.unit.5 (generator, search paths, [Install] mapping, drop-ins, TimeoutStartSec)
- docs.podman.io podman-container.unit.5 (Volume/PublishPort/Environment/EnvironmentFile/AutoUpdate/HealthCmd keys)
- docs.podman.io podman-volume.unit.5 ([Volume]/VolumeName)
- docs.podman.io podman-auto-update.1 (io.containers.autoupdate label, timer)
- Host evidence: podman --version/info, systemctl --user, loginctl, which docker/npx/node, ls /run/user/1000 and ~/.config/containers/systemd (collected 2026-09-05 in this workspace)
- github.com/maximhq/bifrost README (npx + docker run entry points)
