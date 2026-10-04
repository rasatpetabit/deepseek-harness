#!/usr/bin/env bash
set -euo pipefail

DEV_ROOT="/srv/dev/ai/deepseek-harness"
PROD_ROOT="/srv/deepseek-harness"
RELEASES_DIR="$PROD_ROOT/releases"
KEEP_RELEASES=3

# Source selection is mandatory; the script never falls back to the main checkout.
SOURCE_ROOT=""
GIT_SHA=""
while (( $# )); do
  case "$1" in
    --source)
      if (( $# < 2 )) || [[ -n "$SOURCE_ROOT" ]]; then
        echo "ERROR: --source requires one canonical worktree path." >&2
        exit 1
      fi
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --expected-sha)
      if (( $# < 2 )) || [[ -n "$GIT_SHA" ]]; then
        echo "ERROR: --expected-sha requires one full commit SHA." >&2
        exit 1
      fi
      GIT_SHA="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown deployment argument: $1" >&2
      exit 1
      ;;
  esac
done
if [[ -z "$SOURCE_ROOT" || ! "$GIT_SHA" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "ERROR: Usage: $0 --source <absolute-canonical-worktree> --expected-sha <full-commit-SHA>" >&2
  exit 1
fi
if [[ "$SOURCE_ROOT" != /* || "$SOURCE_ROOT" != "$(realpath -e -- "$SOURCE_ROOT")" ]]; then
  echo "ERROR: Source must be an existing absolute canonical worktree path: $SOURCE_ROOT" >&2
  exit 1
fi
OWNER_COMMON=$(git -C "$DEV_ROOT" rev-parse --path-format=absolute --git-common-dir)
OWNER_COMMON=$(realpath -e -- "$OWNER_COMMON")

# Recheck HEAD, index, tracked and non-ignored untracked source at each phase.
# Ignored build outputs are copied separately and must pass release confinement.
verify_source() {
  local phase="$1" common top registered=0 entry head status
  top=$(git -C "$SOURCE_ROOT" rev-parse --show-toplevel) || return 1
  common=$(git -C "$SOURCE_ROOT" rev-parse --path-format=absolute --git-common-dir) || return 1
  if [[ "$top" != "$SOURCE_ROOT" || "$(realpath -e -- "$common")" != "$OWNER_COMMON" ]]; then
    echo "ERROR: Source is not a worktree of the owning repository ($phase)." >&2
    return 1
  fi
  while IFS= read -r -d '' entry; do
    if [[ "$entry" == "worktree $SOURCE_ROOT" ]]; then
      registered=1
    fi
  done < <(git -C "$DEV_ROOT" worktree list --porcelain -z)
  if (( ! registered )); then
    echo "ERROR: Source is not a registered worktree ($phase)." >&2
    return 1
  fi
  head=$(git -C "$SOURCE_ROOT" rev-parse --verify HEAD)
  if [[ "$head" != "$GIT_SHA" ]]; then
    echo "ERROR: Source HEAD differs from expected SHA ($phase): $head != $GIT_SHA" >&2
    return 1
  fi
  status=$(git -C "$SOURCE_ROOT" status --porcelain --untracked-files=all --ignore-submodules=none)
  if [[ -n "$status" ]]; then
    echo "ERROR: Source worktree is dirty ($phase). Commit changes before deploying." >&2
    printf '%s\n' "$status" >&2
    return 1
  fi
}
verify_source "before deployment"
cd "$SOURCE_ROOT"

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
verify_source "before install"
pnpm install --frozen-lockfile
verify_source "after install"
pnpm run build
verify_source "after build"

echo "Staging DeepSeek Harness release $RELEASE_ID from committed snapshot..."
mkdir -p "$STAGING_DIR"

# 1. Populate source from the expected immutable commit, not a moving ref.
git -C "$SOURCE_ROOT" archive "$GIT_SHA" | tar -x -C "$STAGING_DIR"

# 2. Overlay verified build outputs, native addons, and node_modules from workspace.
#    Exclude .git files and directories before directory/output includes.
rsync -a \
  --exclude='.git' \
  --include='*/' \
  --include='dist/***' \
  --include='lib/***' \
  --include='bin/***' \
  --include='native/***' \
  --include='node_modules/***' \
  --exclude='*' \
  "$SOURCE_ROOT/" "$STAGING_DIR/"
verify_source "after staging"

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

verify_source "before promotion"

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
