"""Check inert preparation and rejected release identity without live service changes."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
