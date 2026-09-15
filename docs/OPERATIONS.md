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

## 3. Web Access & Automatic VPN/LAN Authentication

Direct browser access is fully automated for the private VPN/LAN network:

- **Automatic Login on VPN/LAN:** When navigating to `http://192.168.100.177:3080/` from any device on `192.168.100.0/24`, the server recognizes the trusted subnet, automatically mints a signed 30-day session cookie, and logs you into the Web GUI with zero manual token steps.
- **Manual Launch Token (Off-subnet):** If connecting from outside the configured `trustedNetworks`, visiting `/` requires the launch token printed at startup:
  ```bash
  journalctl -u dsh.service -n 30 --no-pager | grep "dsh web: http" | tail -n 1
  ```

## 4. Remote Web Access (LAN & VPN)

Direct browser access over your local network or VPN (`http://<lan-ip>:3080/`) is fully supported for both chat and complete administration (Settings, Models, and Coding Subscription capabilities), without needing SSH port-forwarding tunnels.

Security is enforced at two integrated layers:
1. **Launch-token authentication:** Every browser session must authenticate by visiting the launch URL with `?token=...`, which issues a secure, HMAC-signed session cookie bound to the authority.
2. **Authority & Origin Fences:** Every request must match a trusted authority (`Host`), reject cross-site initiators (`Sec-Fetch-Site: cross-site`), and reject mismatched browser origins (protecting against DNS-rebinding and CSRF attacks).

### 4.1 Authenticated Remote Administration

In our custom web bundle composition:
- `ui-settings` and `ui-settings-general` are configured with `allowRemoteAdministration: true`, enabling host persistence for authenticated browser sessions regardless of whether the address bar reads localhost or a LAN IP.
- The host provides an `ownerRequestPolicy` service to `dsh-coding-subscription-oauth`, authorizing capability toggle switches and OAuth login endpoints for any valid, authenticated DSH session.

### 4.2 Logging in from your Browser

Simply navigate directly to the application in your browser:

```
http://192.168.100.177:3080/
```

- When connecting from your workstation on the VPN/LAN, you are automatically logged in.
- Navigate to **Settings → Models** to configure providers and models.
- Navigate to **Settings → Coding OAuth** to view or toggle subscription capabilities.

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

### 6.1 Plugin Management (profile-scoped)

Third-party plugins live in the DSH profile under
`/srv/deepseek-harness/data/profiles/web` (pnpm workspace) and **survive
deploys** — `deploy-dsh.sh` never touches the data root. Manage them with the
CLI, which forwards to pnpm in the profile directory:

```bash
# Install or pin a plugin version (exact pin via --save-exact)
DSH_HOME=/srv/deepseek-harness/data \
  /usr/bin/node /srv/deepseek-harness/current/apps/cli/lib/bin.js \
  plugin --profile web add --save-exact dsh-coding-subscription-oauth@<version>

sudo systemctl restart dsh.service
```

Peer-dependency warnings during `plugin add` are expected: the harness
runtime supplies the `@deepseek-ai/*` peers at load time
(`autoInstallPeers: false` in the profile lockfile).

**Plugin/harness ABI rule.** DSH is a release-candidate line whose internal
plugin contracts change between rc bumps with no stability promise. A plugin
verified against an older rc can mount cleanly yet fail per-provider at
runtime (missing required profile fields, renamed client modules). Treat any
plugin upgrade or harness upgrade as requiring a runtime re-verification of
the model catalog:

```bash
# mint a session cookie (trusted network), then request the catalog
curl -s -c /tmp/dsh-cookie -H 'Host: 127.0.0.1:3080' http://127.0.0.1:3080/ -o /dev/null
curl -s -b /tmp/dsh-cookie -X POST -H 'Host: 127.0.0.1:3080' \
  -H 'Content-Type: application/json' \
  -d '{"type":"client-request","rpcId":"v","method":"session/modelCatalog","payload":{"args":{}}}' \
  http://127.0.0.1:3080/api/session/modelCatalog
# pass: failures array is [] and every expected provider group lists models
```

Currently installed: `dsh-coding-subscription-oauth@0.8.5` (exact pin).
The plugin's credential files (`/srv/deepseek-harness/data/.*-auth.json`) are
owned by the plugin and format-migrated by it on version changes; they are
not touched by `deploy-dsh.sh`. Back them up before any plugin downgrade.

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

1. **Remote Administration Authorization:** Remote settings and capability administration over LAN/VPN requires a valid, authenticated browser session cookie minted from the launch token. Unauthenticated or cross-site requests are refused.
2. **OAuth Plugin Version:** `dsh-coding-subscription-oauth@0.8.5` is pinned in
   the web profile and verified live on harness `0.1.5-rc.2` — the model
   catalog returns all four OAuth routes (Grok Build, Codex, Kimi Code,
   Claude Code) with `failures: []`. Earlier plugin versions (< 0.7.0) break
   on this harness: `llm-pi-ai` at 0.1.5 requires a `modelErrors` profile
   field they do not set, surfacing per provider as "Cannot read properties
   of undefined (reading 'get')" in the model picker.
3. **Version Alignment:** The plugin's declared BOM targets DSH `0.1.1-rc.2`
   and BOM fields are unenforced decoration (nothing in the harness reads
   them). Compatibility must be verified at runtime (see §6.1), not inferred
   from package metadata. Re-verify after every plugin or harness upgrade.
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
