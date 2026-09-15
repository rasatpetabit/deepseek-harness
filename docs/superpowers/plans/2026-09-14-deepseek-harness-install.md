# DeepSeek Harness Installation & Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install DeepSeek Harness in `/srv/dev/ai/deepseek-harness`, build from source, deploy to `/srv/deepseek-harness`, configure `0.0.0.0` network binding, install `dsh-coding-subscription-oauth`, and configure an enabled systemd autostart service.

**Architecture:** Monorepo source checkout maintained in `/srv/dev/ai/deepseek-harness` with Node 22 build compatibility fixes. Production release deployed to `/srv/deepseek-harness/releases/<timestamp>-<git-sha>` via git archive staging, post-promotion boundary validation, and atomic symlink promotion with persistent `DSH_HOME` at `/srv/deepseek-harness/data`. Cordis patch sets `webserver` host to `0.0.0.0`. Autostart managed by `/etc/systemd/system/dsh.service`.

**Tech Stack:** Node.js v22.22.1, pnpm v10.34.5, Cordis plugin system, systemd, TypeScript, tsdown, rolldown, Vite.

**Spec:** `docs/superpowers/specs/2026-09-14-deepseek-harness-install-design.md`

## Global Constraints

- Production runtime must never run from or link into `/srv/dev` (`/srv/dev/AGENTS.md`).
- Bounded rollback snapshot retention: keep maximum 3 releases in `/srv/deepseek-harness/releases` (`/srv/AGENTS.md`).
- Webserver must bind to all IP addresses (`0.0.0.0`) without breaking clean `git pull` upgrades.
- Systemd service must run as unprivileged user `ras` with `NoNewPrivileges=yes`.
- All credentials and OAuth profile files in `DSH_HOME` must remain protected (`0600`/`0700`).

---

### Task 1: Workspace Toolchain Compatibility & Build

**Files:**
- Modify: `package.json`
- Modify: `packages/client/tsdown.client.ts:78-83`

**Interfaces:**
- Consumes: Node 22 runtime, pnpm package manager.
- Produces: Built binaries and client dist (`apps/cli/lib/bin.js`, `packages/*/lib/*.js`, `apps/web/dist`).

- [ ] **Step 1: Add unrun to workspace devDependencies**

Run:
```bash
cd /srv/dev/ai/deepseek-harness
pnpm add -w -D unrun
```
Verify `unrun` appears in root `package.json` devDependencies.

- [ ] **Step 2: Patch packages/client/tsdown.client.ts for Node 22 workspace root resolution**

Modify lines 78-83 in `packages/client/tsdown.client.ts`:
Replace:
```typescript
const REPOSITORY_ROOT = fileURLToPath(new URL('../..', import.meta.url))
```
With:
```typescript
const REPOSITORY_ROOT = existsSync(resolvePath(process.cwd(), 'pnpm-workspace.yaml'))
  ? process.cwd()
  : fileURLToPath(new URL('../..', import.meta.url))
```

- [ ] **Step 3: Run full workspace build**

Run:
```bash
cd /srv/dev/ai/deepseek-harness
pnpm run build
```
Expected output: exit status 0 and `apps/cli/lib/bin.js` generated.

- [ ] **Step 4: Verify built dsh CLI application profile boot**

Run:
```bash
EXPECTED_VERSION=$(node -p "require('./package.json').version")
ACTUAL_VERSION=$(node apps/cli/lib/bin.js --version)
[ "$ACTUAL_VERSION" = "$EXPECTED_VERSION" ] && echo "Version match: $ACTUAL_VERSION"
node apps/cli/lib/bin.js --profile web --dump-default-config >/dev/null && echo "Web profile dump successful"
```
Expected output: "Version match: <version>" and "Web profile dump successful".

- [ ] **Step 5: Commit toolchain compatibility changes**

Run:
```bash
cd /srv/dev/ai/deepseek-harness
git add package.json pnpm-lock.yaml packages/client/tsdown.client.ts
git commit -m "fix(build): add unrun and resolve workspace root for Node 22 tsdown"
```

---

### Task 2: Production Root Provisioning & Release Deployment

**Files:**
- Create: `/srv/deepseek-harness/releases/<timestamp>-<git-sha>`
- Create: `/srv/deepseek-harness/data`
- Create: `/srv/deepseek-harness/current` (symlink)
- Create: `/srv/dev/ai/deepseek-harness/scripts/deploy-dsh.sh`

**Interfaces:**
- Consumes: Built workspace in `/srv/dev/ai/deepseek-harness`.
- Produces: Production directory tree in `/srv/deepseek-harness`, atomic `current` symlink.

- [ ] **Step 1: Create production directories**

Run:
```bash
sudo mkdir -p /srv/deepseek-harness/releases /srv/deepseek-harness/data /srv/deepseek-harness/bin
sudo chown -R ras:ras /srv/deepseek-harness
chmod 755 /srv/deepseek-harness
chmod 700 /srv/deepseek-harness/data
```

- [ ] **Step 2: Create automated deploy & upgrade script with fresh build, strict boundary validation, and cleanup traps**

Write `/srv/dev/ai/deepseek-harness/scripts/deploy-dsh.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

DEV_ROOT="/srv/dev/ai/deepseek-harness"
PROD_ROOT="/srv/deepseek-harness"
RELEASES_DIR="$PROD_ROOT/releases"
KEEP_RELEASES=3

cd "$DEV_ROOT"

# Ensure repository worktree is committed and clean
GIT_STATUS=$(git status --porcelain)
if [ -n "$GIT_STATUS" ]; then
  echo "ERROR: Working tree is dirty. Commit or stash all changes before deploying." >&2
  echo "$GIT_STATUS" >&2
  exit 1
fi

GIT_SHA=$(git rev-parse --short HEAD)
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_ID="${TIMESTAMP}-$$-$RANDOM-${GIT_SHA}"
TARGET_DIR="$RELEASES_DIR/$RELEASE_ID"
STAGING_DIR="$RELEASES_DIR/.staging-${RELEASE_ID}"

# Guarantee removal of failed staging directory on exit
cleanup() {
  if [ -d "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
}
trap cleanup EXIT INT TERM

echo "Rebuilding clean workspace artifacts for $GIT_SHA..."
pnpm run build

echo "Staging DeepSeek Harness release $RELEASE_ID from committed snapshot..."
mkdir -p "$STAGING_DIR"

# 1. Populate source strictly from clean git archive of HEAD
git archive HEAD | tar -x -C "$STAGING_DIR"

# 2. Overlay verified build outputs and node_modules from workspace
rsync -a \
  --include='*/' \
  --include='dist/***' \
  --include='lib/***' \
  --include='node_modules/***' \
  --exclude='*' \
  "$DEV_ROOT/" "$STAGING_DIR/"

# Move staging into permanent release directory before validation so all paths are final
if [ -e "$TARGET_DIR" ]; then
  echo "ERROR: Target release directory $TARGET_DIR already exists." >&2
  exit 1
fi
mv -T "$STAGING_DIR" "$TARGET_DIR"

# 3. Validate production boundary on the final release tree: Fail closed with strict resolution
python3 - << PYEOF
import os, sys
from pathlib import Path

target = Path("$TARGET_DIR")
dev_tree = Path("/srv/dev")
violations = []
checked_links = 0

def on_error(err):
    print(f"ERROR: Traversal error: {err}", file=sys.stderr)
    sys.exit(2)

for root, dirs, files in os.walk(target, onerror=on_error):
    for name in dirs + files:
        item = Path(root) / name
        if item.is_symlink():
            checked_links += 1
            try:
                resolved = item.resolve(strict=True)
                if resolved.is_relative_to(dev_tree):
                    violations.append(f"{item} -> {resolved}")
                elif not (resolved.is_relative_to(target) or str(resolved).startswith(("/usr", "/lib", "/bin"))):
                    violations.append(f"External unverified link: {item} -> {resolved}")
            except Exception as exc:
                print(f"ERROR: Cannot strictly resolve symlink {item}: {exc}", file=sys.stderr)
                sys.exit(2)

if violations:
    print(f"ERROR: {len(violations)} production boundary violation(s) found:", file=sys.stderr)
    for v in violations[:10]:
        print(f"  {v}", file=sys.stderr)
    sys.exit(1)

print(f"Production boundary check passed: {checked_links} symlinks strictly verified, 0 leaks into /srv/dev.")
PYEOF

# Atomic symlink replacement
TEMP_LINK="$PROD_ROOT/.current-tmp-$$-$RANDOM"
ln -sfn "releases/$RELEASE_ID" "$TEMP_LINK"
mv -Tf "$TEMP_LINK" "$PROD_ROOT/current"
echo "Active release promoted to $RELEASE_ID."

# Bounded release retention (keep 3 latest, never prune active release)
ACTIVE_TARGET=$(readlink "$PROD_ROOT/current")
ACTIVE_RELEASE=$(basename "$ACTIVE_TARGET")

cd "$RELEASES_DIR"
mapfile -t ALL_RELEASES < <(find . -maxdepth 1 -mindepth 1 -type d ! -name '.*' -printf '%f\n' | sort -r)

if [ "${#ALL_RELEASES[@]}" -gt "$KEEP_RELEASES" ]; then
  for ((i=KEEP_RELEASES; i<${#ALL_RELEASES[@]}; i++)); do
    OLD_RELEASE="${ALL_RELEASES[$i]}"
    if [ "$OLD_RELEASE" = "$ACTIVE_RELEASE" ]; then
      echo "Preserving active release from prune: $OLD_RELEASE"
      continue
    fi
    echo "Pruning older release: $OLD_RELEASE"
    rm -rf "$OLD_RELEASE"
  done
fi

if systemctl is-active --quiet dsh.service 2>/dev/null; then
  echo "Restarting dsh.service..."
  sudo systemctl restart dsh.service
fi

echo "Deploy complete: $RELEASE_ID"
```
Make executable: `chmod +x /srv/dev/ai/deepseek-harness/scripts/deploy-dsh.sh`.

- [ ] **Step 3: Run deployment script to /srv/deepseek-harness**

Run:
```bash
/srv/dev/ai/deepseek-harness/scripts/deploy-dsh.sh
```
Verify `/srv/deepseek-harness/current` points to the active release directory.

- [ ] **Step 4: Verify production application profile boot**

Run:
```bash
node /srv/deepseek-harness/current/apps/cli/lib/bin.js --profile web --dump-default-config >/dev/null && echo "Production web profile valid"
```
Expected output: "Production web profile valid".

- [ ] **Step 5: Commit deployment script**

Run:
```bash
cd /srv/dev/ai/deepseek-harness
git add scripts/deploy-dsh.sh
git commit -m "feat(deploy): add production release deployment and pruning script"
```

---

### Task 3: Network Binding & Plugin Configuration

**Files:**
- Create: `/srv/deepseek-harness/data/cordis.patch.yml`
- Create: `/srv/deepseek-harness/data/profiles/web/package.json`

**Interfaces:**
- Consumes: Deployed DSH binary at `/srv/deepseek-harness/current`.
- Produces: Configured `DSH_HOME` with `host: 0.0.0.0` and installed `dsh-coding-subscription-oauth`.

- [ ] **Step 1: Write cordis.patch.yml for 0.0.0.0 webserver binding**

Write `/srv/deepseek-harness/data/cordis.patch.yml`:
```yaml
- id: webserver
  name: '@deepseek-ai/dsh-host-webserver'
  inject:
    - webStartup
  config:
    host: '0.0.0.0'
    port: !!js ctx.webStartup.port ?? 3080
    compression: gzip
    compressionLevel: 1
    compressionThresholdBytes: 1024
```

- [ ] **Step 2: Install dsh-coding-subscription-oauth plugin into web profile**

Run:
```bash
export DSH_HOME=/srv/deepseek-harness/data
cd /srv/deepseek-harness/current
node apps/cli/lib/bin.js plugin --profile web add dsh-coding-subscription-oauth@0.6.5
```
Expected output: `dependencies: + dsh-coding-subscription-oauth 0.6.5`.

- [ ] **Step 3: Verify web profile configuration**

Run:
```bash
cat /srv/deepseek-harness/data/profiles/web/package.json
```
Verify `dsh-coding-subscription-oauth` is listed in `dsh.profile.bundles`.

- [ ] **Step 4: Verify composed profile configuration dump**

Run:
```bash
export DSH_HOME=/srv/deepseek-harness/data
cd /srv/deepseek-harness/current
node apps/cli/lib/bin.js --profile web --dump-config | grep -A 8 "id: webserver"
```
Verify output shows `host: 0.0.0.0` and `patched by /srv/deepseek-harness/data/cordis.patch.yml`.

---

### Task 4: Systemd Service Autostart & Verification

**Files:**
- Create: `/etc/systemd/system/dsh.service`

**Interfaces:**
- Consumes: `/srv/deepseek-harness/current`, `/srv/deepseek-harness/data`.
- Produces: Running and enabled systemd service `dsh.service`.

- [ ] **Step 1: Install systemd service unit**

Create `/etc/systemd/system/dsh.service`:
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

- [ ] **Step 2: Reload systemd and enable service**

Run:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dsh.service
```

- [ ] **Step 3: Verify service status and listening port**

Run:
```bash
systemctl is-active dsh.service
systemctl is-enabled dsh.service
ss -tulpn | grep 3080
```
Expected output:
`active`, `enabled`, and listening on `0.0.0.0:3080`.

- [ ] **Step 4: Verify HTTP responses on loopback and LAN**

Run:
```bash
curl -s -o /dev/null -w "Loopback HTTP: %{http_code}\n" http://127.0.0.1:3080/
curl -s -o /dev/null -w "LAN HTTP: %{http_code}\n" http://192.168.100.177:3080/
```
Expected: HTTP status 200 or 303 on both addresses.

- [ ] **Step 5: Verify production boundary audit**

Run:
```bash
python3 /srv/workflows/scripts/audit-production-boundary.py
```
Confirm no violations for `dsh.service` or `/srv/deepseek-harness`.
