#!/usr/bin/env bash
set -euo pipefail

DEV_ROOT="/srv/dev/ai/deepseek-harness"
PROD_ROOT="/srv/deepseek-harness"
RELEASES_DIR="$PROD_ROOT/releases"
KEEP_RELEASES=3

# Validate that production roots do not resolve into /srv/dev
PROD_REAL=$(realpath -m "$PROD_ROOT")
RELEASES_REAL=$(realpath -m "$RELEASES_DIR")
if [[ "$PROD_REAL" == "/srv/dev" || "$PROD_REAL" == /srv/dev/* || "$RELEASES_REAL" == "/srv/dev" || "$RELEASES_REAL" == /srv/dev/* ]]; then
  echo "ERROR: Production root resolves into /srv/dev (prod: $PROD_REAL, releases: $RELEASES_REAL)" >&2
  exit 1
fi

# Acquire exclusive deployment lock
mkdir -p "$PROD_ROOT"
exec 9>"$PROD_ROOT/.deploy.lock"
flock -n 9 || { echo "ERROR: Deployment lock busy; another deploy is running." >&2; exit 1; }

# Fail closed on an unprovisioned host. This script restarts an existing unit;
# it never creates one. Without this guard a host that has never run the install
# step is handed a promoted release with no service, and the run still reports
# "Deploy complete" (the 2026-09-16 ai-vm cutover).
if ! systemctl cat dsh.service >/dev/null 2>&1; then
  echo "ERROR: dsh.service is not installed on this host; refusing to deploy." >&2
  echo "  This script only restarts an existing unit. Install it first:" >&2
  echo "  docs/superpowers/specs/2026-09-14-deepseek-harness-install-design.md section 2.5" >&2
  exit 1
fi

cd "$DEV_ROOT"

# Ensure repository worktree is committed and clean
GIT_SHA=$(git rev-parse --verify HEAD)
if ! git diff-index --quiet "$GIT_SHA" -- || [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: Working tree is dirty or modified. Commit or stash all changes before deploying." >&2
  git status --porcelain >&2
  exit 1
fi

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_ID="${TIMESTAMP}-$$-$RANDOM-${GIT_SHA:0:7}"
TARGET_DIR="$RELEASES_DIR/$RELEASE_ID"
STAGING_DIR="$RELEASES_DIR/.staging-${RELEASE_ID}"
TEMP_LINK="$PROD_ROOT/.current-tmp-$$-$RANDOM"
TARGET_CREATED=0

# Guarantee cleanup of unpromoted staging, target, or temp link on exit
cleanup() {
  local exit_code=$?
  if [ -L "$TEMP_LINK" ] || [ -e "$TEMP_LINK" ]; then
    rm -f "$TEMP_LINK"
  fi
  if [ -d "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
  if [ "$TARGET_CREATED" -eq 1 ]; then
    local current_real target_real
    current_real=$(realpath "$PROD_ROOT/current" 2>/dev/null || true)
    target_real=$(realpath "$TARGET_DIR" 2>/dev/null || true)
    if [ -n "$target_real" ] && [ "$current_real" != "$target_real" ]; then
      rm -rf "$TARGET_DIR"
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Reconciling dependencies and rebuilding clean workspace artifacts for $GIT_SHA..."
pnpm install --frozen-lockfile
pnpm run build

# Verify worktree remained pristine throughout the build
if ! git diff-index --quiet "$GIT_SHA" --; then
  echo "ERROR: Tracked source modified during build. Aborting deploy." >&2
  exit 1
fi

echo "Staging DeepSeek Harness release $RELEASE_ID from committed snapshot..."
mkdir -p "$STAGING_DIR"

# 1. Populate source strictly from clean git archive of HEAD
git archive HEAD | tar -x -C "$STAGING_DIR"

# 2. Overlay verified build outputs, native addons, and node_modules from workspace.
#    '.git/' is excluded before '*/' (rsync takes the first match): otherwise the
#    dev checkout's .git directory skeleton lands in every release.
rsync -a \
  --exclude='.git/' \
  --include='*/' \
  --include='dist/***' \
  --include='lib/***' \
  --include='bin/***' \
  --include='native/***' \
  --include='node_modules/***' \
  --exclude='*' \
  "$DEV_ROOT/" "$STAGING_DIR/"

# Move staging into permanent release directory before validation
if [ -e "$TARGET_DIR" ]; then
  echo "ERROR: Target release directory $TARGET_DIR already exists." >&2
  exit 1
fi
mv -T "$STAGING_DIR" "$TARGET_DIR"
TARGET_CREATED=1

# 3. Validate production boundary on final release tree:
# Fail closed: target root and every descendant symlink must resolve strictly INSIDE $TARGET_DIR.
# Any link outside the release directory (or failing resolution) aborts immediately.
python3 - << PYEOF
import os, sys
from pathlib import Path

target = Path("$TARGET_DIR")
dev_tree = Path("/srv/dev")
violations = []
checked_links = 0

try:
    target_real = target.resolve(strict=True)
    if target_real.is_relative_to(dev_tree):
        print(f"ERROR: Target directory itself resolves into /srv/dev: {target_real}", file=sys.stderr)
        sys.exit(1)
except Exception as exc:
    print(f"ERROR: Cannot resolve target directory {target}: {exc}", file=sys.stderr)
    sys.exit(2)

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
                if not resolved.is_relative_to(target_real):
                    violations.append(f"External link forbidden: {item} -> {resolved}")
            except Exception as exc:
                print(f"ERROR: Cannot strictly resolve symlink {item}: {exc}", file=sys.stderr)
                sys.exit(2)

if violations:
    print(f"ERROR: {len(violations)} boundary violation(s) found:", file=sys.stderr)
    for v in violations[:10]:
        print(f"  {v}", file=sys.stderr)
    sys.exit(1)

print(f"Production boundary check passed: {checked_links} symlinks strictly verified, 100% self-contained in release tree.")
PYEOF

# Atomic symlink replacement
TEMP_LINK="$PROD_ROOT/.current-tmp-$$-$RANDOM"
ln -sfn "releases/$RELEASE_ID" "$TEMP_LINK"
mv -Tf "$TEMP_LINK" "$PROD_ROOT/current"
PROMOTED=1
echo "Active release promoted to $RELEASE_ID."

# Restart service immediately following promotion, then prove it came back. A
# promoted release behind a unit that is not running is a failed deploy, not a
# successful one, and must never report success.
echo "Restarting dsh.service to active release..."
sudo systemctl restart dsh.service
if ! systemctl is-active --quiet dsh.service; then
  echo "ERROR: dsh.service is not active after restart. Release $RELEASE_ID is promoted but not serving." >&2
  echo "  Previous releases are retained in $RELEASES_DIR; roll $PROD_ROOT/current back if needed." >&2
  sudo systemctl status dsh.service --no-pager >&2 || true
  exit 1
fi

# Bounded release retention per /srv/AGENTS.md: mtime newest-first, protected active release
python3 - << PYEOF
import os, shutil, sys

releases_dir = "$RELEASES_DIR"
prod_root = "$PROD_ROOT"
keep = $KEEP_RELEASES

try:
    active_real = os.path.realpath(os.path.join(prod_root, "current"))
    candidates = []
    for entry in os.scandir(releases_dir):
        if entry.is_dir() and not entry.name.startswith("."):
            candidates.append((entry.stat().st_mtime, entry.name, entry.path))

    # Newest-first by mtime, tie-broken by release id. Ids begin with a UTC
    # timestamp, so id order is chronological. Without the tie-break, equal
    # mtimes fall back to os.scandir order - filesystem hash order, which can
    # retain an older release while pruning a newer one.
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    # Exclude active release from pruning candidates, keeping newest (keep - 1) rollback releases
    non_active = [c for c in candidates if os.path.realpath(c[2]) != active_real]
    for _mtime, _name, path in non_active[max(0, keep - 1):]:
        print(f"Pruning older release: {path}")
        shutil.rmtree(path)
except Exception as e:
    print(f"WARNING: Release retention encountered an error (service remains active on $RELEASE_ID): {e}", file=sys.stderr)
PYEOF

echo "Deploy complete: $RELEASE_ID"
