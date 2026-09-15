# DeepSeek Harness Operations Runbook

## 1. Service Overview

- **Production Root:** `/srv/deepseek-harness`
- **Active Release:** `/srv/deepseek-harness/current`
- **Persistent Data / DSH_HOME:** `/srv/deepseek-harness/data`
- **Listening Address:** `0.0.0.0:3080` (all IPv4 network interfaces)
- **Service Unit:** `/etc/systemd/system/dsh.service` (runs as user `ras`, autostarts on boot)

## 2. Service Management Commands

```bash
# Check service status
systemctl status dsh.service

# Restart service
sudo systemctl restart dsh.service

# Stop / Start service
sudo systemctl stop dsh.service
sudo systemctl start dsh.service

# View recent service logs
journalctl -u dsh.service -n 50 --no-pager
```

## 3. Web UI Access

You can open the Web GUI directly in any browser across your network:

```
http://<host-ip>:3080/
```

(e.g., `http://192.168.100.177:3080/` or `http://127.0.0.1:3080/`).

Requests to `/` automatically receive an authority-bound session cookie and load the full DeepSeek Harness interface without requiring one-time launch tokens in the URL.

## 4. Accessing the Coding Subscription OAuth Plugin

The `dsh-coding-subscription-oauth` plugin is loaded into the Web profile. By design, the plugin's Settings and credential endpoints enforce loopback-only access. When configuring OAuth accounts from a remote machine, access DSH via an SSH port-forwarding tunnel:

```bash
ssh -L 3080:localhost:3080 ras@<host-ip>
```

Then open `http://localhost:3080/?token=<token>` in your local browser and navigate to **Settings → Coding OAuth** to log in to Grok, Codex, Kimi, or Claude Code.

Alternatively, manage OAuth accounts directly from the command line:

```bash
DSH_HOME=/srv/deepseek-harness/data /usr/bin/node /srv/deepseek-harness/current/apps/cli/lib/bin.js plugin --profile web exec dsh-coding-oauth login codex
```

## 5. Updates and Upgrades

The development checkout in `/srv/dev/ai/deepseek-harness` tracks upstream `origin/master`. To update and deploy:

```bash
cd /srv/dev/ai/deepseek-harness

# Fetch and rebase local compatibility commits
git fetch origin master
git rebase origin/master

# Build and deploy release snapshot (rebuilds, validates boundary, flips symlink, restarts service, prunes to keep 3)
./scripts/deploy-dsh.sh
```

## 6. Rollback

In the event an upgrade needs to be rolled back:

```bash
# 1. Inspect existing releases
ls -la /srv/deepseek-harness/releases/

# 2. Point current to the previous release
sudo ln -sfn "releases/<previous-release-id>" /srv/deepseek-harness/current

# 3. Restart the service
sudo systemctl restart dsh.service
```

## 7. Known Operational Limits

1. **OAuth Flow Verification:** The plugin `dsh-coding-subscription-oauth@0.6.5` is installed and mounts successfully; live OAuth token exchanges require individual user credentials and were not executed.
2. **Version Alignment:** Plugin 0.6.5 declares peer dependencies against DSH `0.1.1-rc.2`, whereas the monorepo is on `0.1.5-rc.2`. Startup and configuration verification succeed, but individual plugin features may encounter upstream drift.
3. **Filesystem Sandboxing:** The systemd service runs with `ProtectSystem=strict` and `ReadWritePaths=/srv/deepseek-harness/data`. The service can read files across the system (unless protected by `ProtectHome=read-only`), but writes are restricted to its data directory.
