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

## 3. Obtaining the Authentication / Login URL

DSH requires a launch token for Web GUI access. Unauthenticated requests to `/` receive HTTP 401. To retrieve the active authentication URL with token:

```bash
journalctl -u dsh.service -n 30 --no-pager | grep "dsh web: http" | tail -n 1
```

This prints both the loopback URL (`http://127.0.0.1:3080/?token=...`) and LAN URL (`http://<lan-ip>:3080/?token=...`).

> **The LAN URL gives you chat, not Settings.** See section 4 before administering
> models or OAuth from another machine.

## 4. Remote Access: Chat Works Over LAN, Settings Does Not

Two independent loopback gates apply. Both are deliberate security boundaries.
Neither is configurable, and neither is bypassed by a reverse proxy.

### 4.1 The whole Settings UI is gated on the browser's address bar

The DSH **client** decides whether Settings can persist at all, from the hostname
in the address bar:

| Step | Location |
|---|---|
| `isLoopback` derives from the page hostname | `packages/client/connection/src/client/index.ts` |
| true only for `localhost`, `[::1]`, `127.0.0.0/8` | `packages/client/connection/src/loopback-hostname.ts` |
| `persistence = isLoopback ? 'host' : 'memory'` | `packages/client/ui-settings/src/client/index.ts` |
| `memory` sets `status: 'unavailable'`, terminally | `packages/client/ui-settings/src/client/settings-mirror.ts` |
| `view === undefined` raises the error below | `packages/client/ui-settings-models/src/client/store.ts` |

Opening the LAN URL therefore shows:

> Loading the provider directory failed: settings are unavailable in this browser

This is **not** an authentication failure, a server fault, or a problem with
`settings.yaml`. Chat works normally over LAN; only Settings is gated. Because
the check is on the address-bar hostname, a reverse proxy does not help — the
proxy's own hostname is still not loopback. Do not "fix" this by forcing
`isLoopback` or adding wildcard trusted hosts; both remove a real boundary.

### 4.2 The OAuth plugin additionally gates on the TCP peer

`dsh-coding-subscription-oauth` rejects a non-loopback `req.socket.remoteAddress`
on its credential and capability endpoints before any Host or Origin check, so
its capability switches return `403 Forbidden` from a LAN browser even once
Settings loads. Do not patch the plugin's checks in `node_modules`.

### 4.3 The one supported remote path: SSH port forwarding

```bash
ssh -L 3080:localhost:3080 ras@<host-ip>
```

Then open `http://localhost:3080/?token=<token>` in your local browser. The
address bar reads `localhost`, so `isLoopback` is true and the full Settings UI
works — **Settings → Models** for providers, and **Settings → Coding OAuth** to
log in to Grok, Codex, Kimi, or Claude Code.

The launch token changes on every service restart; re-read it per section 3.

Alternatively, manage OAuth accounts directly from the command line:

```bash
DSH_HOME=/srv/deepseek-harness/data /usr/bin/node /srv/deepseek-harness/current/apps/cli/lib/bin.js plugin --profile web exec dsh-coding-oauth login codex
```

Note the CLI covers OAuth only. It has **no** capability-management or
model-settings command, so it is not an alternative to the tunnel for Settings.

## 5. Model Catalog Management (LiteLLM)

The local LiteLLM gateway is registered as a `llm-pi-ai` provider route in
`/srv/deepseek-harness/data/settings.yaml`. Refresh it with the deployed
importer — run it from the release tree, never from `/srv/dev`:

```bash
# Discovery only; writes nothing. Reports admitted/rejected counts with reasons.
python3 /srv/deepseek-harness/current/scripts/litellm_catalog.py

# Merge into settings.yaml once the discovery report looks right.
python3 /srv/deepseek-harness/current/scripts/litellm_catalog.py --apply
sudo systemctl restart dsh.service
```

Classification comes from LiteLLM's authoritative `/model/info` capability
metadata, not from model-name substrings. The importer excludes wildcard route
placeholders and any non-chat mode, and emits `contextWindow`/`maxTokens` only
when LiteLLM actually published them — an absent value inherits DSH's route
default visibly rather than recording a fabricated number as a measurement.

The admitted set is the importer's **selection**, not a list of individually
verified chat models. Where LiteLLM publishes no `mode` the importer falls back
to a name heuristic and says so in its report; an unrecognized non-empty `mode`
is admitted. Treat the discovery report as the claim to check, not as proof.

The importer **never opens the credential store**. It writes an `apiKeyEnv`
*reference* (`LITELLM_API_KEY`); supplying that secret is a separate operator
act through DSH's own credential UI or the environment.

Tests: `python3 -m pytest scripts/tests/test_litellm_catalog.py`

> **Never rotate `client-connection/browser-session` in `.credentials.yaml` as a
> side effect of model refresh.** That record holds the HMAC secret signing
> `dsh-auth-*` cookies; deleting it logs out every live browser session.

## 6. Updates and Upgrades

The development checkout in `/srv/dev/ai/deepseek-harness` tracks upstream `origin/master`. To update and deploy:

```bash
cd /srv/dev/ai/deepseek-harness

# Fetch and rebase local compatibility commits
git fetch origin master
git rebase origin/master

# Build and deploy release snapshot (rebuilds, validates boundary, flips symlink, restarts service, prunes to keep 3)
./scripts/deploy-dsh.sh
```

## 7. Rollback

In the event an upgrade needs to be rolled back:

```bash
# 1. Inspect existing releases
ls -la /srv/deepseek-harness/releases/

# 2. Point current to the previous release
sudo ln -sfn "releases/<previous-release-id>" /srv/deepseek-harness/current

# 3. Restart the service
sudo systemctl restart dsh.service
```

## 8. Known Operational Limits

1. **Settings Is Loopback-Only:** the entire Settings UI, including the Models
   provider directory, is unavailable to any browser whose address bar is not
   loopback. See section 4.1. Use the SSH tunnel; do not patch the client gate.
2. **OAuth Flow Verification:** The plugin `dsh-coding-subscription-oauth@0.6.5` is installed and mounts successfully; live OAuth token exchanges require individual user credentials and were not executed.
3. **Version Alignment:** Plugin 0.6.5 declares peer dependencies against DSH `0.1.1-rc.2`, whereas the monorepo is on `0.1.5-rc.2`. Startup and configuration verification succeed, but individual plugin features may encounter upstream drift.
4. **Filesystem Sandboxing:** The systemd service runs with `ProtectSystem=strict` and `ReadWritePaths=/srv/deepseek-harness/data`. The service can read files across the system (unless protected by `ProtectHome=read-only`), but writes are restricted to its data directory.
5. **Undetected Model Capacity:** LiteLLM publishes an output cap for only some
   models. Those without one inherit the `llm-pi-ai` route defaults
   (`DEFAULT_CONTEXT_WINDOW` / `DEFAULT_MAX_TOKENS` in
   `packages/llm/llm-pi-ai/src/config.ts`), which are fallbacks, not measured
   limits. The importer's discovery report states both counts.
6. **No Backup Trash In The Data Directory:** `/srv/AGENTS.md` forbids leaving
   `.bak`/`.backup` files in a production root. The importer writes one bounded
   `settings.yaml.bak`; delete it once the result is reviewed, or pass
   `--no-backup`. `settings.yaml.lock` is a legitimate persistent `flock` inode
   and is expected to remain.
7. **Deployed Versus Committed:** the running release is built from a specific
   commit — check it with `basename "$(readlink -f /srv/deepseek-harness/current)"`
   and compare against `git log`. Commits landed after the last deploy are
   **not** in production. Do not assume a source fix is live, and do not redeploy
   merely to synchronize documentation.

## 9. Bounded Follow-Up Work

Known defects in `scripts/litellm_catalog.py`, recorded rather than fixed so
that they are not rediscovered. None is a reason for an unreviewed production
change.

1. **`.bak` is created before its mode is set.** `write_atomic` opens the backup
   with a plain `open(..., "wb")` and chmods afterwards, leaving a brief
   permissions window and following a pre-existing symlink at that path. Use
   `os.open` with `O_CREAT | O_EXCL | O_NOFOLLOW` and mode `0600`. Severity is
   low here — the data directory is private and single-owner — but the shape is
   wrong.
2. **The lock does not coordinate with DSH.** It serializes concurrent importer
   runs only. A Settings UI save made while the importer runs can be silently
   overwritten, because DSH holds no part of this lock. Stop the service, or do
   not edit Settings in the browser, during an `--apply`.
3. **Invalid entries are discarded silently**, and duplicate model metadata is
   first-wins. A malformed `/model/info` response narrows the catalog without
   failing the run; only the totals in the report hint at it.
4. **`--allow-other-protocols` is unverified.** It admits `responses`-mode models
   into a route declared `api: openai-completions`. LiteLLM may well translate
   between the two, but that has not been tested here. Do not pass the flag
   without first confirming DSH can serve the resulting route.
