"""Regression test for the release-retention block in scripts/deploy-dsh.sh.

The retention logic is an embedded Python heredoc inside a bash installer, so
this test extracts that exact block from the script and executes it against a
fixture tree. It deliberately does not reimplement the algorithm: a copy would
pass while the deployed script diverged.

Run with:

    python3 -m pytest scripts/tests/test_deploy_retention.py

Root `pytest.ini` restricts default collection to `python/sdk/tests`, so this
file is invoked explicitly.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-dsh.sh"

# The oldest release id stands in for a build that must never survive retention
# (in the incident that motivated the tie-break, a security-downgraded release).
OLDEST = "20260915T061612Z-993705-3808-469f32d"
MIDDLE = "20260915T062601Z-1067581-4836-921b5d8"
PREVIOUS = "20260915T062844Z-1088520-30785-519732d"
NEWEST = "20260915T093143Z-2329564-5542-f5b1afe"
TIED_MTIME = 1789449656.351242


def extract_retention_block() -> str:
    """Pull the retention heredoc out of the installer, verbatim.

    deploy-dsh.sh contains more than one `python3 - << PYEOF` block, so select
    by content rather than by taking the first match.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    blocks = re.findall(r"python3 - << PYEOF\n(.*?)\nPYEOF\n", source, re.S)
    retention = [b for b in blocks if "candidates.sort" in b]
    assert len(retention) == 1, (
        f"expected exactly one retention heredoc in deploy-dsh.sh, found {len(retention)} of {len(blocks)} blocks"
    )
    block = retention[0]
    assert "shutil.rmtree" in block, "extracted block does not look like retention"
    return block


class FakeDirEntry:
    """The four attributes the retention block reads off an os.DirEntry."""

    def __init__(self, path: str, mtime: float) -> None:
        self.path = path
        self.name = os.path.basename(path)
        self._mtime = mtime

    def is_dir(self) -> bool:
        return True

    def stat(self) -> Any:
        class Stat:
            st_mtime = self._mtime

        return Stat()


class FakeScan:
    """A scandir result supporting both iteration and the context manager.

    `shutil.rmtree` uses `with os.scandir(...)` internally, so a bare generator
    is not a sufficient stand-in for the real call.
    """

    def __init__(self, entries: list[FakeDirEntry]) -> None:
        self._entries = entries

    def __iter__(self) -> Iterator[FakeDirEntry]:
        return iter(self._entries)

    def __enter__(self) -> "FakeScan":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def close(self) -> None:
        return None


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Any]:
    """A prod root whose three older releases share one mtime, plus a new one.

    Mirrors the deployed state at the moment retention runs: `current` already
    points at the NEW release, because the installer promotes before pruning.
    """
    prod_root = tmp_path / "prod"
    releases = prod_root / "releases"
    releases.mkdir(parents=True)

    mtimes = {OLDEST: TIED_MTIME, MIDDLE: TIED_MTIME, PREVIOUS: TIED_MTIME, NEWEST: TIED_MTIME + 10}
    for name, mtime in mtimes.items():
        (releases / name).mkdir()
        os.utime(releases / name, (mtime, mtime))

    (prod_root / "current").symlink_to(releases / NEWEST, target_is_directory=True)
    assert os.path.realpath(prod_root / "current") == str(releases / NEWEST)

    return {"prod_root": prod_root, "releases": releases, "mtimes": mtimes}


def run_retention(tree: dict[str, Any], order: list[str], keep: int = 3) -> list[str]:
    """Execute the extracted block with a forced enumeration order.

    The block imports `os` itself, so injecting a shim into its namespace would
    be rebound by that import. Patch `os.scandir` instead, which leaves the code
    under test byte-identical to what the installer runs.

    @returns the release ids still on disk afterwards.
    """
    block = extract_retention_block()
    block = block.replace('"$RELEASES_DIR"', repr(str(tree["releases"])))
    block = block.replace('"$PROD_ROOT"', repr(str(tree["prod_root"])))
    block = block.replace("$KEEP_RELEASES", str(keep))
    block = block.replace("$RELEASE_ID", NEWEST)
    assert "$" not in block, f"unsubstituted shell variable remains: {block}"

    mtimes = tree["mtimes"]
    releases = str(tree["releases"])
    real_scandir = os.scandir

    def fake_scandir(target: Any = ".", *args: Any, **kwargs: Any) -> Any:
        # Only the releases directory is forced; shutil.rmtree scandirs the
        # individual release it is deleting and must get the real thing.
        if str(target) != releases:
            return real_scandir(target, *args, **kwargs)
        return FakeScan([
            FakeDirEntry(os.path.join(releases, name), mtimes[name]) for name in order
        ])

    with patch.object(os, "scandir", fake_scandir):
        # The retention block swallows any exception into a stderr WARNING, so a
        # crash would otherwise look like a pass. Capture and forbid it.
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            exec(compile(block, str(SCRIPT), "exec"), {})  # noqa: S102 - the script IS the unit under test
        assert "WARNING" not in captured.getvalue(), (
            f"retention block raised and was swallowed: {captured.getvalue().strip()}"
        )

    return sorted(p.name for p in tree["releases"].iterdir() if p.is_dir())


ALL_ORDERS = list(__import__("itertools").permutations([OLDEST, MIDDLE, PREVIOUS, NEWEST]))


def test_the_fixture_reproduces_the_deployed_state(tree: dict[str, Any]) -> None:
    assert sorted(p.name for p in tree["releases"].iterdir()) == sorted([OLDEST, MIDDLE, PREVIOUS, NEWEST])
    tied = [n for n in (OLDEST, MIDDLE, PREVIOUS) if tree["mtimes"][n] == TIED_MTIME]
    assert len(tied) == 3, "the regression needs three releases sharing one mtime"


@pytest.mark.parametrize("order", ALL_ORDERS)
def test_retention_is_independent_of_enumeration_order(tree: dict[str, Any], order: list[str]) -> None:
    survivors = run_retention(tree, order)
    assert survivors == sorted([NEWEST, PREVIOUS, MIDDLE]), (
        f"enumeration order {order} pruned the wrong release: kept {survivors}"
    )


@pytest.mark.parametrize("order", ALL_ORDERS)
def test_the_oldest_release_is_always_the_one_pruned(tree: dict[str, Any], order: list[str]) -> None:
    survivors = run_retention(tree, order)
    assert OLDEST not in survivors, f"oldest release survived under order {order}"


@pytest.mark.parametrize("order", ALL_ORDERS)
def test_the_active_release_is_never_pruned(tree: dict[str, Any], order: list[str]) -> None:
    survivors = run_retention(tree, order)
    assert NEWEST in survivors, f"active release was pruned under order {order}"


def test_all_tied_releases_still_resolve_deterministically(tree: dict[str, Any]) -> None:
    """Even with every mtime equal, id order alone decides."""
    for name in (OLDEST, MIDDLE, PREVIOUS, NEWEST):
        tree["mtimes"][name] = TIED_MTIME
    survivors = run_retention(tree, [NEWEST, OLDEST, MIDDLE, PREVIOUS])
    assert survivors == sorted([NEWEST, PREVIOUS, MIDDLE])


def test_keep_two_prunes_both_older_releases(tree: dict[str, Any]) -> None:
    survivors = run_retention(tree, [OLDEST, MIDDLE, PREVIOUS, NEWEST], keep=2)
    assert survivors == sorted([NEWEST, PREVIOUS])


def test_extraction_tracks_the_real_script(tree: dict[str, Any]) -> None:
    """Guard against the test silently drifting from the installer."""
    block = extract_retention_block()
    assert "os.scandir" in block
    assert "shutil.rmtree" in block
    assert "active_real" in block
    # The tie-break is the fix under test; if it is removed, fail loudly here
    # rather than through opaque ordering assertions.
    assert re.search(r"sort\(key=lambda x: \(x\[0\], x\[1\]\), reverse=True\)", block), (
        "retention no longer sorts by (mtime, id); the deterministic tie-break was removed"
    )
