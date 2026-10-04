"""Regression test for the workspace overlay in scripts/deploy-dsh.sh.

The overlay rsync keeps every directory (`--include='*/'`) so that the
dist/lib/bin/native/node_modules includes can reach nested paths. Without an
earlier `.git` exclusion, that rule also recreated the dev checkout's .git
directory skeleton inside every release, which left each release looking like a
broken repository (found by the 2026-09-29 backup-litter audit).

Like test_deploy_provisioning.py, this extracts the rsync command from the
installer verbatim and runs it against a scratch workspace. It does not
reimplement the filter: a copy would pass while the deployed script diverged.

Run with:

    python3 -m pytest scripts/tests/test_deploy_overlay.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-dsh.sh"


def _extract_overlay() -> str:
    """Pull the overlay rsync command out of the installer, verbatim."""
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("rsync -a \\\n")
    end_marker = '"$SOURCE_ROOT/" "$STAGING_DIR/"\n'
    end = source.index(end_marker, start) + len(end_marker)
    return source[start:end]


def _run_overlay(dev_root: Path, staging: Path) -> None:
    env = dict(os.environ, SOURCE_ROOT=str(dev_root), STAGING_DIR=str(staging))
    subprocess.run(["bash", "-euo", "pipefail", "-c", _extract_overlay()],
                   check=True, env=env)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    dev = tmp_path / "dev"
    for rel in (".git/HEAD", ".git/objects/ab/cdef", "dist/index.js",
                "packages/core/dist/core.js", "packages/core/.git/HEAD",
                "src/main.ts"):
        path = dev / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel, encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    return dev, staging


def test_overlay_copies_build_outputs(tmp_path):
    dev, staging = _workspace(tmp_path)
    _run_overlay(dev, staging)
    assert (staging / "dist" / "index.js").is_file()
    assert (staging / "packages" / "core" / "dist" / "core.js").is_file()
    assert not (staging / "src" / "main.ts").exists()


def test_overlay_never_creates_a_git_directory(tmp_path):
    dev, staging = _workspace(tmp_path)
    _run_overlay(dev, staging)
    leaked = [p for p in staging.rglob(".git")]
    assert leaked == [], leaked


def test_overlay_excludes_linked_worktree_git_files(tmp_path):
    dev, staging = _workspace(tmp_path)
    # Linked worktrees use a .git file rather than a directory.
    shutil.rmtree(dev / ".git")
    (dev / ".git").write_text("gitdir: /outside/worktrees/source\n", encoding="utf-8")
    (dev / "dist" / ".git").write_text("git metadata", encoding="utf-8")
    _run_overlay(dev, staging)
    assert not list(staging.rglob(".git"))
