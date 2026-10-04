"""Check preparation, snapshot verification and unit checks without live changes."""
import os
import subprocess

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("damage", ["none", "link", "data", "missing", "extra"])
def test_snapshot_compares_dangling_links_and_data(tmp_path, damage):
    prod = tmp_path / "prod"
    data = prod / "data"
    data.mkdir(parents=True)
    (data / "state").write_text("persisted data\n")
    (data / "dependency").symlink_to("missing-package")
    backup = tmp_path / "backup"
    backup.mkdir()
    text = (ROOT / "scripts/commission-dsh.sh").read_text()
    snapshot = text[text.index('  tar -C "$ROOT" -cpf "$BACKUP/data.tar" data'):text.index('  printf \'%s\\n\' "$RECEIPT"')]
    # Config capture belongs to a separate snapshot; keep this test data-only.
    snapshot = "\n".join(line for line in snapshot.splitlines() if '"$BACKUP/config.tar"' not in line)
    mutations = {
        "none": ":",
        "link": 'ln -sfn other-missing-package "$BACKUP/restore-test/data/dependency"',
        "data": 'printf changed > "$BACKUP/restore-test/data/state"',
        "missing": 'rm "$BACKUP/restore-test/data/state"',
        "extra": 'touch "$BACKUP/restore-test/data/extra"',
    }
    snapshot = snapshot.replace("  diff ", mutations[damage] + "\n  diff ")
    env = {**os.environ, "ROOT": str(prod), "BACKUP": str(backup)}
    result = subprocess.run(["bash", "-euc", snapshot], env=env, capture_output=True, text=True)
    if damage == "none":
        assert result.returncode == 0, result.stderr
        assert not (backup / "restore-test").exists()
    else:
        assert result.returncode == 1, result.stderr
        assert (backup / "restore-test").exists()
    assert (data / "state").read_text() == "persisted data\n"
    assert os.readlink(data / "dependency") == "missing-package"


@pytest.mark.parametrize("inactive", ["none", "dsh.service", "dsh-edge.service"])
def test_activation_requires_each_unit_active(tmp_path, inactive):
    text = (ROOT / "scripts/commission-dsh.sh").read_text()
    activation = text.split("activate)\n", 1)[1].split("rollback)\n", 1)[0]
    start = "  for unit in " if "  for unit in " in activation else "  systemctl is-active --quiet dsh.service"
    checks = activation[activation.index(start):activation.index('  echo "Activated;')]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    systemctl = bindir / "systemctl"
    systemctl.write_text('#!/bin/sh\nfor unit in "$@"; do\n case "$unit" in dsh.service|dsh-edge.service) [ "$unit" != "$INACTIVE" ] && exit 0 ;; esac\ndone\nexit 3\n')
    systemctl.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "INACTIVE": inactive}
    result = subprocess.run(["bash", "-euc", checks], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (inactive == "none")


def test_exact_exemption_precedes_supported_plugin_install():
    text = (ROOT / "scripts/commission-dsh.sh").read_text()
    assert text.index("plugin --profile web allow-version") < text.index("plugin --profile web add --save-exact")
    assert "dsh-plugin-subscriptions@0.9.7 --dsh-version 0.2.1-alpha.1 --accept-risk" in text


def test_prepare_renders_valid_scoped_tls_and_never_activates(tmp_path):
    prod = tmp_path / "prod"
    release = prod / "releases" / "candidate"
    (release / "scripts/deploy").mkdir(parents=True)
    sha = "a" * 40
    (release / "RELEASE_COMMIT").write_text(sha + "\n")
    (release / "scripts/deploy/nginx-dsh.conf.template").write_text(
        (ROOT / "scripts/deploy/nginx-dsh.conf.template").read_text())
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    for name, body in {
        "id": "echo 0",
        "systemctl": 'echo "systemctl $*" >> "$CALLS"; exit 3',
        "nginx": 'echo "nginx $*" >> "$CALLS"',
    }.items():
        p = bindir / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)
    script = tmp_path / "commission.sh"
    text = (ROOT / "scripts/commission-dsh.sh").read_text()
    text = text.replace("ROOT=/srv/deepseek-harness", f"ROOT={prod}")
    text = text.replace("/etc/dsh-edge", str(tmp_path / "etc/dsh-edge"))
    text = text.replace("/var/log/dsh-edge", str(tmp_path / "log"))
    text = text.replace("/run/dsh-edge", str(tmp_path / "run"))
    script.write_text(text)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "CALLS": str(calls)}
    result = subprocess.run(["bash", str(script), "prepare", str(release), sha], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    tls = tmp_path / "etc/dsh-edge/tls"
    subprocess.run(["openssl", "verify", "-CAfile", str(tls / "ca.crt"), "-verify_ip", "192.168.100.200", str(tls / "server.crt")], check=True)
    assert (tls / "server.key").stat().st_mode & 0o777 == 0o600
    config = (tmp_path / "etc/dsh-edge/edge.conf").read_text()
    assert "listen 192.168.100.200:443 ssl" in config
    assert "deny all;" in config
    assert "@" not in config
    assert "restart" not in calls.read_text()
    assert "enable" not in calls.read_text()
    assert not (prod / "current").exists()
    before = (tls / "server.key").read_bytes()
    subprocess.run(["bash", str(script), "prepare", str(release), sha], env=env, check=True, capture_output=True)
    assert (tls / "server.key").read_bytes() == before
    result = subprocess.run(["bash", str(script), "prepare", str(release), "b" * 40], env=env, capture_output=True)
    assert result.returncode != 0
