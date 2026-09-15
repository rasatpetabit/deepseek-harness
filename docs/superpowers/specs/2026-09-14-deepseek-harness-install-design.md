# DeepSeek Harness Installation & Deployment Design

- **Date:** 2026-09-14
- **Target:** DeepSeek Harness (`dsh`) Web UI & `dsh-coding-subscription-oauth` plugin
- **Status:** Approved

## 1. Context & Objectives

The goal is to install DeepSeek Harness (`https://github.com/deepseek-ai/deepseek-harness`) in `/srv/dev/ai/deepseek-harness`, configure the web server to bind to all IP addresses (`0.0.0.0`), ensure clean upgradeability, install the `dsh-coding-subscription-oauth` plugin (`https://www.dsh.so/artifact/dsh-coding-subscription-oauth/`), and configure systemd autostart.

Per workspace fleet policy (`/srv/dev/AGENTS.md`):
- "Production never runs from `/srv/dev`."
- Production services must run from a production root (e.g. `/srv/deepseek-harness`), never directly from `/srv/dev`.
- Deployments follow a one-way path: built snapshots copied from dev to production with a bounded keep-3 rollback set (`/srv/AGENTS.md`).

## 2. Architecture & Components

### 2.1 Development Repository (`/srv/dev/ai/deepseek-harness`)
- Checked out from `https://github.com/deepseek-ai/deepseek-harness.git` on branch `master`.
- Monorepo package manager: `pnpm@10.34.5` (compatible with `pnpm@11` spec).
- Node 22 build compatibility:
  - Add `unrun` to devDependencies.
  - Patch `packages/client/tsdown.client.ts` to locate `REPOSITORY_ROOT` by testing for `pnpm-workspace.yaml` from `process.cwd()` instead of relying on `import.meta.url`, which points to `.unrun` cache directory during transpilation.
- Build artifact commands: `pnpm install && pnpm run build`.

### 2.2 Production Runtime (`/srv/deepseek-harness`)
- Production root: `/srv/deepseek-harness/` (owned by `ras:ras`).
- Release structure:
  - `/srv/deepseek-harness/releases/<git-sha>/`: clean copy of the built workspace.
  - `/srv/deepseek-harness/current`: atomic symlink to active release.
  - `/srv/deepseek-harness/data/`: persistent `DSH_HOME` containing profiles, attachments, and configurations.
  - Rollback pruning: keep latest 3 releases, automatically pruning older ones upon new deploy.

### 2.3 Network Exposure (`0.0.0.0`)
- Configured via `$DSH_HOME/cordis.patch.yml`:
  ```yaml
  - id: webserver
    name: '@deepseek-ai/dsh-host-webserver'
    inject: [webStartup]
    config:
      host: '0.0.0.0'
      port: !!js ctx.webStartup.port ?? 3080
      compression: gzip
      compressionLevel: 1
      compressionThresholdBytes: 1024
  ```
- DeepSeek Harness's `WebServer` accepts `0.0.0.0`. The `web-app` bundle executes `resolveLanTrust`, discovers the host's IPv4 LAN addresses (e.g. `192.168.100.177`), and adds them to `trustedHosts`.
- Using `cordis.patch.yml` avoids modifying upstream git source files, keeping git pulls clean and merge-conflict-free.

### 2.4 Plugin Installation (`dsh-coding-subscription-oauth`)
- Target: `dsh-coding-subscription-oauth@0.6.5` installed into `$DSH_HOME/profiles/web/`.
- Command: `dsh plugin --profile web add dsh-coding-subscription-oauth@0.6.5`.
- Updates `profiles/web/package.json` to include the plugin in `dsh.profile.bundles`.

### 2.5 Autostart Service (`/etc/systemd/system/dsh.service`)
- Systemd unit definition:
  ```ini
  [Unit]
  Description=DeepSeek Harness Web GUI
  Documentation=https://deepseek-harness.github.io/deepseek-harness/
  After=network-online.target
  Wants=network-online.target

  [Service]
  Type=simple
  User=ras
  Group=ras
  WorkingDirectory=/srv/deepseek-harness/current
  Environment=DSH_HOME=/srv/deepseek-harness/data
  Environment=NODE_ENV=production
  ExecStart=/usr/bin/node /srv/deepseek-harness/current/apps/cli/lib/bin.js web --no-open
  Restart=on-failure
  RestartSec=10
  Nice=10
  NoNewPrivileges=yes
  ProtectSystem=strict
  ProtectHome=read-only
  ReadWritePaths=/srv/deepseek-harness/data
  PrivateTmp=true

  [Install]
  WantedBy=multi-user.target
  ```
- Enabled and managed via `systemctl enable --now dsh.service`.

### 2.6 Upgrade Tooling (`scripts/deploy-dsh.sh`)
- Automated script under `/srv/dev/ai/deepseek-harness/scripts/deploy-dsh.sh` that:
  1. Pulls latest master: `git pull origin master`.
  2. Runs `pnpm install` and `pnpm run build`.
  3. Prepares release directory `/srv/deepseek-harness/releases/<git-sha>`.
  4. Copies built artifacts and node_modules to release directory.
  5. Updates `/srv/deepseek-harness/current` atomic symlink.
  6. Restarts `dsh.service`.
  7. Prunes old releases in `/srv/deepseek-harness/releases/` to keep 3.

## 3. Verification & Acceptance Criteria
1. Monorepo builds completely without errors in `/srv/dev/ai/deepseek-harness`.
2. Release deployed to `/srv/deepseek-harness/releases/<sha>` and symlinked to `/srv/deepseek-harness/current`.
3. `dsh-coding-subscription-oauth` installed and registered in `$DSH_HOME/profiles/web/package.json`.
4. `systemctl status dsh.service` is active (running) and `is-enabled` reports `enabled`.
5. `ss -tulpn | grep 3080` confirms listening on `0.0.0.0:3080`.
6. HTTP request returns valid HTML on loopback and LAN IP.
7. `python3 /srv/workflows/scripts/audit-production-boundary.py` confirms no new violations.
