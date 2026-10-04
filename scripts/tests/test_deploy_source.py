"""Exercise immutable source selection through the real deploy script in scratch trees.

Only deployment roots are substituted. Git, archive, rsync, confinement, promotion,
and retention run unchanged; service and build commands are controlled stubs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-dsh.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def deployment(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / ".gitignore").write_text("dist/\nnode_modules/\n", encoding="utf-8")
    (repo / "source.txt").write_text("committed source\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    sha = _git(repo, "rev-parse", "HEAD")
    source = tmp_path / "source"
    _git(repo, "worktree", "add", "--detach", str(source), sha)
    for tree, value in ((repo, "wrong main output"), (source, "selected output")):
        (tree / "dist").mkdir()
        (tree / "dist" / "index.js").write_text(value, encoding="utf-8")
    prod = tmp_path / "prod"
    previous = prod / "releases" / "previous"
    previous.mkdir(parents=True)
    (prod / "current").symlink_to("releases/previous")
    data = prod / "data"
    data.mkdir()
    (data / ".credentials.json").write_text("protected fixture", encoding="utf-8")
    (data / "signing-secret").write_text("protected signing fixture", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    _stub(bindir, "systemctl", 'echo "systemctl $*" >> "$CALLS"\n')
    _stub(bindir, "sudo", 'exec "$@"\n')
    real_rsync = shutil.which("rsync")
    assert real_rsync is not None, "deployment overlay requires rsync"
    _stub(bindir, "rsync", f'''"{real_rsync}" "$@"
if [[ "${{MUTATE_PHASE:-run}}" == "staging" ]]; then
  case "${{MUTATION:-none}}" in
    head) git -c user.name=Fixture -c user.email=fixture@example.invalid commit --allow-empty -qm moved ;;
    tracked) echo modified >> source.txt ;;
    index) echo modified >> source.txt; git add source.txt ;;
    untracked) echo unexpected > extra.txt ;;
  esac
fi
''')
    _stub(bindir, "pnpm", '''echo "pnpm $*" >> "$CALLS"
if [[ "$1" == "${MUTATE_PHASE:-run}" ]]; then
  case "${MUTATION:-none}" in
    head) git -c user.name=Fixture -c user.email=fixture@example.invalid commit --allow-empty -qm moved ;;
    tracked) echo modified >> source.txt ;;
    index) echo modified >> source.txt; git add source.txt ;;
    untracked) mkdir -p extra; echo unexpected > extra/source.txt ;;
  esac
fi
''')
    script = tmp_path / "deploy.sh"
    text = SCRIPT.read_text(encoding="utf-8")
    text = text.replace('DEV_ROOT="/srv/dev/ai/deepseek-harness"', f'DEV_ROOT="{repo}"')
    text = text.replace('PROD_ROOT="/srv/deepseek-harness"', f'PROD_ROOT="{prod}"')
    script.write_text(text, encoding="utf-8")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "CALLS": str(calls)}
    return dict(repo=repo, source=source, prod=prod, sha=sha, script=script, env=env, calls=calls, tmp=tmp_path)


def _run(deployment, args=None, **environment):
    if args is None:
        args = ["--source", str(deployment["source"]), "--expected-sha", deployment["sha"]]
    return subprocess.run(["bash", str(deployment["script"]), *args], text=True,
                          capture_output=True, env={**deployment["env"], **environment})


def _rejected(deployment, result):
    assert result.returncode != 0, result.stdout
    assert "ERROR:" in result.stderr, result.stderr
    assert os.readlink(deployment["prod"] / "current") == "releases/previous"
    calls = deployment["calls"].read_text() if deployment["calls"].exists() else ""
    assert "restart" not in calls
    assert sorted(p.name for p in (deployment["prod"] / "releases").iterdir()) == ["previous"]
    assert not list(deployment["prod"].glob(".current-tmp-*"))
    assert (deployment["prod"] / "data" / ".credentials.json").read_text() == "protected fixture"
    assert (deployment["prod"] / "data" / "signing-secret").read_text() == "protected signing fixture"


@pytest.mark.parametrize("args", [[], ["--source"], ["--expected-sha"], ["--unknown"],
                                     ["--source", "relative", "--expected-sha", "0" * 40],
                                     ["--source", "/tmp", "--source", "/tmp"],
                                     ["--expected-sha", "0" * 40, "--expected-sha", "0" * 40]])
def test_missing_or_invalid_arguments_fail_before_production_writes(deployment, args):
    _rejected(deployment, _run(deployment, args))
    assert not (deployment["prod"] / ".deploy.lock").exists()


@pytest.mark.parametrize("selection", ["alias", "dotdot", "wrong-repo", "unregistered", "subdirectory", "missing"])
def test_source_must_be_canonical_registered_owning_worktree(deployment, selection):
    source = deployment["source"]
    if selection == "alias":
        alias = deployment["tmp"] / "alias"
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif selection == "dotdot":
        source = str(source) + "/../source"
    elif selection == "wrong-repo":
        other = deployment["tmp"] / "other"
        subprocess.run(["git", "clone", "-q", str(deployment["repo"]), str(other)], check=True)
        source = other
    elif selection == "unregistered":
        _git(deployment["repo"], "worktree", "lock", str(source))
        metadata = Path(_git(source, "rev-parse", "--absolute-git-dir"))
        (metadata / "gitdir").write_text(str(deployment["tmp"] / "absent" / ".git") + "\n")
    elif selection == "missing":
        source = deployment["tmp"] / "missing"
    else:
        source = source / "dist"
    result = _run(deployment, ["--source", str(source), "--expected-sha", deployment["sha"]])
    _rejected(deployment, result)
    assert not (deployment["prod"] / ".deploy.lock").exists()


@pytest.mark.parametrize("sha", ["0" * 40, "HEAD", "short", "0" * 64])
def test_expected_sha_must_be_full_and_match_head(deployment, sha):
    _rejected(deployment, _run(deployment, ["--source", str(deployment["source"]), "--expected-sha", sha]))
    assert not (deployment["prod"] / ".deploy.lock").exists()


@pytest.mark.parametrize("dirty", ["tracked", "index", "untracked"])
def test_dirty_source_rejected_before_install(deployment, dirty):
    source = deployment["source"]
    if dirty == "untracked":
        (source / "unexpected.txt").write_text("unexpected")
    else:
        (source / "source.txt").write_text("modified")
        if dirty == "index":
            _git(source, "add", "source.txt")
    _rejected(deployment, _run(deployment))
    assert not deployment["calls"].exists()
    assert not (deployment["prod"] / ".deploy.lock").exists()


@pytest.mark.parametrize("phase", ["install", "run", "staging"])
@pytest.mark.parametrize("mutation", ["head", "tracked", "index", "untracked"])
def test_install_and_build_cannot_move_or_dirty_source(deployment, phase, mutation):
    _rejected(deployment, _run(deployment, MUTATE_PHASE=phase, MUTATION=mutation))
    if phase == "install":
        assert "pnpm run build" not in deployment["calls"].read_text()


def test_clean_selected_worktree_supplies_archive_and_outputs(deployment):
    result = _run(deployment)
    assert result.returncode == 0, result.stderr
    release = (deployment["prod"] / "current").resolve()
    assert release != (deployment["prod"] / "releases" / "previous")
    assert (release / "source.txt").read_text() == "committed source\n"
    assert (release / "dist" / "index.js").read_text() == "selected output"
    assert not list(release.rglob(".git"))
    assert "systemctl restart dsh.service" in deployment["calls"].read_text()
    assert "Production boundary check passed" in result.stdout
    assert _git(deployment["source"], "rev-parse", "HEAD") == deployment["sha"]
    assert _git(deployment["source"], "status", "--porcelain", "--untracked-files=all") == ""
    assert (deployment["prod"] / "data" / ".credentials.json").read_text() == "protected fixture"
    assert (deployment["prod"] / "data" / "signing-secret").read_text() == "protected signing fixture"


def test_dirty_main_does_not_replace_or_block_clean_selected_worktree(deployment):
    (deployment["repo"] / "source.txt").write_text("operator-owned main changes\n")
    result = _run(deployment)
    assert result.returncode == 0, result.stderr
    release = (deployment["prod"] / "current").resolve()
    assert (release / "source.txt").read_text() == "committed source\n"
    assert (release / "dist" / "index.js").read_text() == "selected output"
    assert (deployment["repo"] / "source.txt").read_text() == "operator-owned main changes\n"
    assert _git(deployment["repo"], "status", "--porcelain") == "M source.txt"


def test_external_output_symlink_fails_confinement_before_promotion(deployment):
    (deployment["source"] / "dist" / "external").symlink_to(deployment["repo"] / "source.txt")
    result = _run(deployment)
    _rejected(deployment, result)
    assert "External link forbidden" in result.stderr
