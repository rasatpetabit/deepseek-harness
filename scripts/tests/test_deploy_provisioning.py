"""Regression test for the fail-closed provisioning guards in scripts/deploy-dsh.sh.

deploy-dsh.sh restarts an existing dsh.service; it never creates one. Before the
guard existed, a host that had never run the install step was handed a promoted
release with no service while the run still printed "Deploy complete" - the
2026-09-16 ai-vm cutover.

Like test_deploy_retention.py, this extracts the guard blocks out of the
installer verbatim and executes them with a stubbed PATH. It deliberately does
not reimplement the logic: a copy would pass while the deployed script diverged.

Run with:

    python3 -m pytest scripts/tests/test_deploy_provisioning.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-dsh.sh"


def _extract(start_marker: str) -> str:
    """Pull a guard block out of the installer, verbatim."""
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index(start_marker)
    end = source.index("\nfi\n", start) + len("\nfi\n")
    return source[start:end]


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, block: str, systemctl_body: str, extra_env: dict | None = None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _stub(bindir, "systemctl", systemctl_body)
    _stub(bindir, "sudo", 'exec "$@"\n')
    script = tmp_path / "block.sh"
    script.write_text(block, encoding="utf-8")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **(extra_env or {})}
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


PREFLIGHT = _extract("if ! systemctl cat dsh.service")
RESTART = _extract('echo "Restarting dsh.service to active release..."')

RESTART_ENV = {
    "RELEASE_ID": "TESTRELEASE",
    "RELEASES_DIR": "/tmp/releases",
    "PROD_ROOT": "/tmp/prod",
}


def test_preflight_refuses_when_unit_absent(tmp_path):
    """A host with no dsh.service must fail closed before anything is promoted."""
    result = _run(tmp_path, PREFLIGHT, "exit 1\n")
    assert result.returncode == 1
    assert "not installed on this host" in result.stderr
    assert "refusing to deploy" in result.stderr


def test_preflight_passes_when_unit_present(tmp_path):
    """A provisioned host is not blocked."""
    result = _run(tmp_path, PREFLIGHT, "exit 0\n")
    assert result.returncode == 0


def test_restart_fails_closed_when_service_not_active(tmp_path):
    """A promoted release behind a dead unit must never report success."""
    body = 'case "$1" in is-active) exit 1 ;; status) echo stubbed-status; exit 3 ;; *) exit 0 ;; esac\n'
    result = _run(tmp_path, RESTART, body, RESTART_ENV)
    assert result.returncode == 1
    assert "not active after restart" in result.stderr
    assert "TESTRELEASE" in result.stderr


def test_restart_passes_when_service_active(tmp_path):
    """A healthy restart proceeds to retention and completion."""
    body = 'case "$1" in is-active) exit 0 ;; *) exit 0 ;; esac\n'
    result = _run(tmp_path, RESTART, body, RESTART_ENV)
    assert result.returncode == 0
