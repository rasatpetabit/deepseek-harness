#!/usr/bin/env bash
set -euo pipefail

DEV_ROOT="/srv/dev/ai/deepseek-harness"
PROD_ROOT="/srv/deepseek-harness"
RELEASES_DIR="$PROD_ROOT/releases"
KEEP_RELEASES=3

# Acquire exclusive deployment lock
mkdir -p "$PROD_ROOT"
exec 9>"$PROD_ROOT/.deploy.lock"
flock -n 9 || { echo "ERROR: Deployment lock busy; another deploy is running." >&2; exit 1; }

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

# Guarantee cleanup of unpromoted staging or target directory on exit
cleanup() {
  local exit_code=$?
  if [ -d "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
  if [ -d "$TARGET_DIR" ]; then
    local current_real
    current_real=$(realpath "$PROD_ROOT/current" 2>/dev/null || true)
    if [ "$current_real" != "$TARGET_DIR" ]; then
      rm -rf "$TARGET_DIR"
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Rebuilding clean workspace artifacts for $GIT_SHA..."
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

# 2. Overlay verified build outputs and node_modules from workspace
rsync -a \
  --include='*/' \
  --include='dist/***' \
  --include='lib/***' \
  --include='node_modules/***' \
  --exclude='*' \
  "$DEV_ROOT/" "$STAGING_DIR/"

# Move staging into permanent release directory before validation
if [ -e "$TARGET_DIR" ]; then
  echo "ERROR: Target release directory $TARGET_DIR already exists." >&2
  exit 1
fi
mv -T "$STAGING_DIR" "$TARGET_DIR"

# 3. Validate production boundary on final release tree:
# Fail closed: every symlink must resolve strictly INSIDE $TARGET_DIR.
# Any link outside the release directory (or failing resolution) aborts immediately.
python3 - << PYEOF
import os, sys
from pathlib import Path

target = Path("$TARGET_DIR")
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
                if not resolved.is_relative_to(target):
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
            candidates.append((entry.stat().st_mtime, entry.path))

    # Sort newest-first by mtime
    candidates.sort(key=lambda x: x[0], reverse=True)
    # Exclude active release from pruning candidates, keeping newest (keep - 1) rollback releases
    non_active = [(mtime, p) for mtime, p in candidates if os.path.realpath(p) != active_real]
    for mtime, path in non_active[max(0, keep - 1):]:
        print(f"Pruning older release: {path}")
        shutil.rmtree(path)
except Exception as e:
    print(f"ERROR during release retention: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

if systemctl is-enabled --quiet dsh.service 2>/dev/null || systemctl is-active --quiet dsh.service 2>/dev/null; then
  echo "Restarting dsh.service..."
  sudo systemctl restart dsh.service
fi

echo "Deploy complete: $RELEASE_ID"
