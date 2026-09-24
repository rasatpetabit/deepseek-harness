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

- **Automatic Login on VPN/LAN:** When navigating to `http://192.168.100.200:3080/` from any device on `192.168.100.0/24`, the server recognizes the trusted subnet, automatically mints a signed 30-day session cookie, and logs you into the Web GUI with zero manual token steps.
- **Manual Launch Token (Off-subnet):** If connecting from outside the configured `trustedNetworks`, visiting `/` requires the launch token printed at startup:
  ```bash
  journalctl -u dsh.service -n 30 --no-pager | grep "dsh web: http" | tail -n 1
  ```

## 4. Remote Web Access (LAN & VPN)

Direct browser access over your local network or VPN (`http://<lan-ip>:3080/`) is fully supported for both chat and complete administration (Settings, Models, and Subscriptions capabilities), without needing SSH port-forwarding tunnels.

Security is enforced at two integrated layers:
1. **Launch-token authentication:** Every browser session must authenticate by visiting the launch URL with `?token=...`, which issues a secure, HMAC-signed session cookie bound to the authority.
2. **Authority & Origin Fences:** Every request must match a trusted authority (`Host`), reject cross-site initiators (`Sec-Fetch-Site: cross-site`), and reject mismatched browser origins (protecting against DNS-rebinding and CSRF attacks).

### 4.1 Authenticated Remote Administration

In our custom web bundle composition:
- `ui-settings` and `ui-settings-general` are configured with `allowRemoteAdministration: true`, enabling host persistence for authenticated browser sessions regardless of whether the address bar reads localhost or a LAN IP.
- The host provides an `ownerRequestPolicy` service, and the plugin's `/api/subscriptions-auth.*` endpoints are reachable only through the connection layer's single `/api` route, which applies `connection.requestRejection(req)` — session cookie plus authority fence — before bridging. Capability toggles and OAuth login are therefore authorized for any valid, authenticated DSH session. Note that `dsh-plugin-subscriptions@0.9.2` injects `connection`, not `ownerRequestPolicy`: the service is provided but this plugin version does not consume it by name (see §10).

### 4.2 Logging in from your Browser

Simply navigate directly to the application in your browser:

```
http://192.168.100.200:3080/
```

- When connecting from your workstation on the VPN/LAN, you are automatically logged in.
- Navigate to **Settings → Models** to configure providers and models.
- Navigate to **Settings → Subscriptions** to connect a provider and toggle subscription capabilities.

Login is performed in the web UI at **Settings → Subscriptions → provider →
Connect**. `dsh-plugin-subscriptions` documents no CLI login path, and
`dsh plugin` is a thin forwarder that runs `pnpm <args...>` in the profile
directory — package management only (`add`, `remove`, `update`, `why`, …),
none of which can perform an OAuth login. The CLI is therefore not an
alternative to the browser for either login or Settings.

## 5. Model Catalog Management (LiteLLM)

The deployment default agent model is `deepseek-official/deepseek-v4.1-flash`
with `reasoningEffort: high`, and a `DEEPSEEK_API_KEY` reference is stored in
`.credentials.yaml` — without one that route errors `MISSING_CREDENTIAL` on
every turn (any token value works against the local vLLM endpoint; store it via
**Settings → Models**). The `litellm` route serves the same model through the
local gateway with no credential, but do not add a `reasoningEffort` override
to `agent-default-model` while the default provider is `litellm`: the pi-ai
route rejects it (`UNSUPPORTED_REASONING_EFFORT`).

The local LiteLLM gateway is registered as a `llm-pi-ai` provider route in
`/srv/deepseek-harness/data/settings.yaml`. Refresh it with the deployed
importer — run it from the release tree, never from `/srv/dev`:

```bash
# Discovery only; writes nothing. Reports admitted/rejected counts with reasons.
python3 /srv/deepseek-harness/current/scripts/litellm_catalog.py

# Merge into settings.yaml once the discovery report looks right.
python3 /srv/deepseek-harness/current/scripts/litellm_catalog.py --apply

# Or refresh capability while keeping the model set this deployment offers.
python3 /srv/deepseek-harness/current/scripts/litellm_catalog.py --existing-only --apply
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

Discovery admits whatever the proxy publishes concretely, and that set is larger
than what this deployment offers: the proxy also publishes vendor routing
variants — `alibaba-singapore/`, `alibaba-us-east-1/`, `cheaperinference/`,
`kimi-code/`, `ollama-cloud/`, `skynet/`, `tencent-singapore/`, `zai/`, `zenmux/`
— over models that are separately declared bare. `--existing-only` holds the
emitted set to the ids the target document already declares, so refreshing
capability does not also decide what the model picker offers. It reports how many
it held back, and refuses rather than guessing when the route declares nothing to
preserve. Widening the offered set is a separate, deliberate act: run without the
flag.

Reasoning efforts are **transcribed, never guessed**. LiteLLM publishes each
model's selectable levels and their wire spellings in
`model_info.thinking_level_map`, keyed by the same vocabulary `llm-pi-ai` accepts,
and the importer copies that map into `reasoningEfforts`. Levels the proxy leaves
valueless are dropped except `off`, the only level DSH permits valueless; when
nothing beyond `off` survives, the field is omitted so DSH inherits its catalog
default rather than receiving a claim its schema refuses. Until the importer
wrote this field, no model on the route advertised a reasoning level, so the
effort selector never appeared for any of them.

The importer **never opens the credential store**. It writes an `apiKeyEnv`
*reference* (`LITELLM_API_KEY`); supplying that secret is a separate operator
act through DSH's own credential UI or the environment.

Tests: `python3 -m pytest scripts/tests/test_litellm_catalog.py`

> **Never rotate `client-connection/browser-session` in `.credentials.yaml` as a
> side effect of model refresh.** That record holds the HMAC secret signing
> `dsh-auth-*` cookies; deleting it logs out every live browser session.

## 6. Updates and Upgrades

The development checkout in `/srv/dev/ai/deepseek-harness` carries a house delta on top of upstream. Integrate an upgrade on a branch first, verify it, then deploy from the dev root:

```bash
cd /srv/dev/ai/deepseek-harness
git fetch --tags --prune origin

# Record the house HEAD behind a safety ref before integrating: the delta is
# not recoverable from upstream if it is lost.
git tag -a "safety/pre-upgrade/$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)" \
  -m "Safety snapshot: house master before the <tag> integration" HEAD

# Pin the target to a published upstream release tag, not a moving branch tip.
git worktree add -b upgrade/<tag> ".worktrees/upgrade-<tag>" master
cd ".worktrees/upgrade-<tag>"
git merge --no-ff <tag>
```

Verify in the worktree before integrating:

```bash
pnpm install --frozen-lockfile
pnpm run build
pnpm run typecheck
pnpm exec vitest run packages/client/connection packages/client/ui-settings \
  packages/client/ui-settings-general packages/bundle/web-app
python3 -m pytest scripts/tests/ -q
```

**Safety-ref naming.** One scheme, derived rather than invented per occasion:
`safety/<kind>/<UTC instant>-<short sha>`, created as an annotated tag. `<kind>`
names what the snapshot protects — `pre-upgrade`, `pre-deploy`, `pre-import` —
the instant is `date -u +%Y%m%dT%H%M%SZ`, the same sortable form `deploy-dsh.sh`
stamps into its release ids, and the trailing short sha makes the protected
commit legible from the ref name alone. Because the instant is part of the name,
two snapshots cannot collide, so the tag never needs `-f`; forcing a safety ref
would move the very snapshot it exists to preserve. Do not create a parallel
backup branch — the tag *is* the snapshot, and a branch can be made from it when
one is actually wanted. Refs of this form are kept, not pruned. The superseded
ad-hoc refs `house/pre-upgrade/<sha>`, `house/pre-<version>-upgrade/<sha>` and
`house-backup-pre-<version>` are retired; they embedded a target version that
went stale on the next upgrade and did not sort.

Then verify the plugin ABI at runtime against a **scratch** `DSH_HOME` — never the production data root — and on a port that is not 3080. §6.1's catalog check is written against the live production service, so retarget it here rather than following it verbatim:

```bash
export DSH_HOME=$(mktemp -d)
node apps/cli/lib/bin.js plugin --profile web add --save-exact dsh-plugin-subscriptions@<pinned-version>
node apps/cli/lib/bin.js web --no-open --port 3180 &   # prints its own launch URL
# Now run §6.1's two curl commands with the port changed to 3180.
# Pass: failures is [] and routableProviders lists every expected route.
```

The `0.1.5-rc.2` → `0.1.6-alpha.1` bump needed exactly one conflict resolution (an import block in `packages/bundle/web-app/tests/web-app.spec.ts`) and passed all of the above: 43 test files / 620 tests, 117 pytest cases, and a scratch-profile catalog returning `failures: []` with all five subscription routes routable.

`deploy-dsh.sh` hardcodes `DEV_ROOT=/srv/dev/ai/deepseek-harness` and refuses a dirty tree, so it deploys the dev root's `HEAD`, never a worktree. **Integrate the branch onto `master` first.** Skipping this step deploys the pre-upgrade `HEAD` silently: a clean, un-integrated `master` passes the dirty-tree gate, and the release is staged from `git archive HEAD`.

```bash
cd /srv/dev/ai/deepseek-harness
git merge --ff-only upgrade/<tag>
git worktree remove --force ".worktrees/upgrade-<tag>"

# After the fast-forward the dev root's installed dependencies and build
# outputs still match the OLD lockfile. deploy-dsh.sh reinstalls and rebuilds,
# but any command run from the dev root before it executes stale artifacts.
pnpm install --frozen-lockfile && pnpm run build

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
  plugin --profile web add --save-exact dsh-plugin-subscriptions@<version>

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
catalog() {
  curl -s -b /tmp/dsh-cookie -X POST -H 'Host: 127.0.0.1:3080' \
    -H 'Content-Type: application/json' \
    -d '{"type":"client-request","rpcId":"v","method":"session/modelCatalog","payload":{"args":{}}}' \
    http://127.0.0.1:3080/api/session/modelCatalog \
  | python3 -c '
import json,sys
v=json.load(sys.stdin)["result"]["value"]
print("failures:", v["failures"])
print("default:", v["default"])
for g in v["groups"]:
    ms=g.get("models",[])
    eff=[m for m in ms if (m.get("reasoning") or {}).get("efforts")]
    print("%-22s models=%3d effortBearing=%d" % (g["id"], len(ms), len(eff)))
'
}
# Capture the capability surface BEFORE the change, then re-run and diff after it.
catalog | tee /tmp/catalog-before.txt
```

**Registration is not compatibility.** A catalog that lists every route with `failures: []` while a group has silently lost its reasoning efforts passes a route-only check and is still a broken upgrade. A pass requires all three:

1. `failures` is `[]`;
2. `routableProviders` lists every expected route, and no group lost models;
3. no group's `effortBearing` count dropped. A group that advertised reasoning efforts and now advertises none is a capability regression, not a clean upgrade.

Diff the post-change output against `/tmp/catalog-before.txt`; a decrease on any line is a failure.

Currently installed: `dsh-plugin-subscriptions@0.9.2` (exact pin;
upstream `V1ki/dsh-plugin-subscriptions`). It registers five
subscription-OAuth routes — `codex`, `claude`, `grok`, `copilot`,
`antigravity` — through `dsh-llm`'s stable `registerAdapter` contract (it
does **not** use `llm-pi-ai`, so the rc-line profile-field breakages that
hit `llm-pi-ai`-based plugins do not apply). Verify the routes with the
catalog check above: `routableProviders` must list all five. There is **no
Kimi route** — Kimi models are served through the LiteLLM provider
(`kimi-k3`, `k3-256k` via the local proxy).

**Credentials.** The plugin keeps one shared `.credentials.json` in
`DSH_HOME` (`/srv/deepseek-harness/data/.credentials.json`), shared across
profiles. Log in per route via the web UI **Settings → Subscriptions** —
remote settings administration is enabled for the trusted LAN (§5), so this
works from a VPN browser. Back up `.credentials.json` before any plugin
downgrade.

Also installed: `@vectorize-io/hindsight-coding-agents@0.6.1` (exact pin;
the official Hindsight coding-agents plugin). It mounts on the host plane
and gives every session long-term memory against the fleet Hindsight server
— recall injection at step time, knowledge-page tools, and background
gitlog/conversation ingestion into a per-repo bank
(`coding-agent::{gitProject}`).

**Hindsight configuration.** Server URL and token live in the shared
`~/.hindsight/coding-agent.json` (read-only to the service; the same file
every Hindsight coding-agent harness reads). Diagnostic logs are redirected
into the writable data root by the operator-approved drop-in
`/etc/systemd/system/dsh.service.d/hindsight.conf` (`HINDSIGHT_LOG_FILE`,
`HINDSIGHT_DIAG_FILE`, `HINDSIGHT_USAGE_FILE` →
`/srv/deepseek-harness/data/hindsight/`); without it the service sandbox
(`ProtectHome=read-only`) silently swallows the plugin's log writes.
Verify health via `diag.jsonl` `inject_ok`/`reflect_ok` events, or in a
session call the `hindsight_sync_status` tool — `"synced": true` means the
bank is seeded and queryable.

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

That reverses **code only**, and code needs no backup of its own: each release is
a `git archive` of a tagged commit, and the `safety/*` tags keep a pruned
release's source rebuildable.

The data root is the part git does not hold — the browser-session HMAC secret,
the subscription OAuth tokens, provider routes, and session history. The symlink
never touches it, so rolling back a release does not roll back state, and a state
change needs its own restore. The data root is in the host's hourly restic set
(`/srv/deepseek-harness/data` in `PETABIT_RESTIC_PATHS`, in
`/etc/petabit-restic/env`):

```bash
# Confirm the newest snapshot contains the data root.
sudo bash -c 'set -a; . /etc/petabit-restic/env; set +a; \
  restic ls latest --path /srv/deepseek-harness/data | head'

# Restore one file elsewhere, review it, then put it back yourself.
sudo bash -c 'set -a; . /etc/petabit-restic/env; set +a; \
  restic restore latest --target /tmp/dsh-restore \
  --include /srv/deepseek-harness/data/settings.yaml'
```

Both are read-only against the repository. `--path` with `latest` selects the
newest snapshot *including that path*, so `no snapshot found` means the data root
is in no snapshot yet — which is exactly what that command reported before
coverage was added, and is the check to run if the include list is ever changed
again. Note that `--path` on `restore` also filters snapshots rather than files;
`--include` is what narrows the restored set.

One exclusion applies. The host exclude list (`/etc/petabit-restic/excludes`,
whose stated policy is "keep system/config state, skip rebuildable bulk") drops
`**/node_modules`, so a restore returns each profile's manifests but not its
installed plugin tree. That is recoverable rather than lost: `package.json` and
`pnpm-lock.yaml` are both inside the backed-up profile, and the lockfile pins the
plugins exactly, so a `pnpm install` in the restored profile rebuilds the tree.
Everything else under the data root — credentials, subscription OAuth tokens,
settings, sessions, storages — is captured; comparing a snapshot listing against
the live tree found no entry missing outside `node_modules`. Run that comparison
again after any change to the include or exclude list, since both are fleet-wide
files owned elsewhere and neither will fail loudly if this path stops being
covered.

## 8. Known Operational Limits

1. **Remote Administration Authorization:** Remote settings and capability administration over LAN/VPN requires a valid, authenticated browser session cookie minted from the launch token. Unauthenticated or cross-site requests are refused.
2. **Subscription Plugin Version:** `dsh-plugin-subscriptions@0.9.2` is pinned
   in the web profile (exact pin). It has been verified live on harness
   `0.1.5-rc.2` and on `0.1.6-alpha.1`: the model catalog returns all five
   subscription routes (`codex`, `claude`, `grok`, `copilot`, `antigravity`)
   alongside `deepseek-official` and `litellm`, with `failures: []`. It
   registers routes through `dsh-llm`'s `registerAdapter` contract rather than
   `llm-pi-ai`, so the `modelErrors` profile-field breakage that hit
   `llm-pi-ai`-based plugins at 0.1.5 does not apply. Its predecessor
   `dsh-coding-subscription-oauth` is no longer installed — versions of it below
   0.7.0 broke on this harness with "Cannot read properties of undefined
   (reading 'get')" in the model picker.
3. **Version Alignment:** `dsh-plugin-subscriptions` declares harness peer
   ranges of `^0.1.1-rc.2 || ^0.1.2-alpha.1` and was built against
   `0.1.2-alpha.3`, while production has run harness versions well outside them
   (`0.1.5-rc.2`, then `0.1.6-alpha.1`). Those declared ranges are unenforced
   decoration — nothing in the harness reads them — so compatibility must be
   verified at runtime (see §6.1), not inferred from package metadata.
   Re-verify after every plugin or harness upgrade.
4. **Filesystem Sandboxing:** The systemd service runs with `ProtectSystem=strict` and `ReadWritePaths=/srv/deepseek-harness/data`. The service can read files across the system (unless protected by `ProtectHome=read-only`), but writes are restricted to its data directory.
5. **Undetected Model Capacity:** LiteLLM publishes an output cap for only some
   models. Those without one inherit the `llm-pi-ai` route defaults
   (`DEFAULT_CONTEXT_WINDOW` / `DEFAULT_MAX_TOKENS` in
   `packages/llm/llm-pi-ai/src/config.ts`), which are fallbacks, not measured
   limits. The importer's discovery report states both counts.
6. **No Backup Trash In The Data Directory:** `/srv/AGENTS.md` forbids leaving
   `.bak`/`.backup` files in a production root. The importer writes one bounded
   `settings.yaml.bak`; delete it once the result is reviewed, or pass
   `--no-backup`. `settings.yaml.lock` is transient — `FileLock.release()`
   unlinks it — so seeing no lock file after a run is correct, and one left
   behind means a run died before releasing it.
7. **Deployed Versus Committed:** the running release is built from a specific
   commit — check it with `basename "$(readlink -f /srv/deepseek-harness/current)"`
   and compare against `git log`. Commits landed after the last deploy are
   **not** in production. Do not assume a source fix is live, and do not redeploy
   merely to synchronize documentation. A fix to a tool operators run *from the
   release tree* — the importer among them — is not documentation; the runbook
   points at that copy, so a stale one is a trap.
8. **A rejected model disappears quietly.** The running adapter materializes the
   catalog under *deferred* validation, so a model entry DSH rejects is recorded
   in `modelErrors` and skipped while its siblings keep serving; only strict
   validation, which the adapter does not use, would abort the route. The catalog
   response exposes `default`, `routableProviders`, `groups` and `failures` — it
   does **not** expose `modelErrors`, and `failures` stays empty when a single
   model was dropped. A silently skipped model is therefore visible only as a
   lower model count for its group, which is why §6.1 compares per-group counts
   against a pre-change baseline instead of asserting only that routes answer.

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

## 10. Bounded Follow-Up Work (platform)

Defects and dead-weight candidates in the house delta and the deploy pipeline,
found by adversarial review of the `0.1.6-alpha.1` upgrade. Recorded rather than
fixed, so that they are not rediscovered — recording is not remediation, and
every item below is still open. None is a reason for an unreviewed production
change.

1. **`ownerRequestPolicy` may be dead house code.** The web-app provides it
   unconditionally, but `dsh-plugin-subscriptions@0.9.2` injects `connection`
   and never consumes a service by that name; authorization actually happens in
   the connection layer's `/api` route. The provision was added for the retired
   predecessor plugin. Confirm no other consumer exists, then drop it as part of
   the prune-cruft pass.
2. **`accessMode` mislabels the transport.** `ownerRequestPolicy.authorize`
   returns `accessMode: 'trusted-https-proxy'` for every non-loopback
   authenticated request, but the web server is plain `node:http` with no TLS
   terminator anywhere in the composition, and the declared `'ssh-tunnel'` mode
   is never produced. Any consumer keying policy on that label would extend
   TLS-proxy trust to plain LAN HTTP clients.
3. **Socket-less requests are misclassified.** `req.socket?.remoteAddress` is
   undefined for Web Worker tunnel traffic, so same-process worker requests fall
   into the non-loopback arm and receive the same label. The authorization
   outcome is unchanged, so this is a labeling defect, not a bypass.
4. **The client-bundler root resolution can silently mis-root.** The house patch
   prefers `process.cwd()` whenever a workspace marker file exists there, so a
   foreign enclosing workspace or a scratch directory makes the repository root
   wrong; sourcemap paths then fail their `packages/` prefix test and degrade to
   the untransformed value with no error. `deploy-dsh.sh` changes into
   `DEV_ROOT` before building, so the deploy path is safe.
5. **The deploy lock excludes concurrent deploys only.** The running service
   never participates in that `flock`, so it provides no mutual exclusion
   against the service's own writes. Exposure is bounded because the deploy
   never touches the data root, and the lock inode is created before the lock is
   actually held.
6. **`KEEP_RELEASES=3` is a hardcoded operational tunable** with no
   configuration seam, feeding the release-pruning bound.
7. **`unrun@^0.3.1` is imported by nothing.** Kept during the merge because it
   is undetermined whether the bundler resolves it at config-load time rather
   than through an import statement; removal is the dangerous direction until
   that is checked. It carries no comment saying so.
