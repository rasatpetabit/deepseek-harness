#!/usr/bin/env bash
# Prepare inert TLS/edge assets; activation is a separate reviewed invocation.
set -euo pipefail
MODE=${1:?Usage: commission-dsh.sh prepare|activate|rollback <release> <sha> [receipt-or-backup]}
RELEASE=$(realpath -e "${2:?release required}")
SHA=${3:?full SHA required}
ROOT=/srv/deepseek-harness
NODE="$ROOT/toolchains/node-v24.21.0-linux-x64/bin/node"
TLS=/etc/dsh-edge/tls
[[ "$SHA" =~ ^[0-9a-f]{40}$ && "$RELEASE" == "$ROOT/releases/"* && ! -L "$RELEASE" ]]
[[ $(cat "$RELEASE/RELEASE_COMMIT") == "$SHA" ]]
[[ $(id -u) == 0 ]] || { echo 'Run through sudo; TLS and rollback files are root-owned.' >&2; exit 1; }
exec 9>"$ROOT/.deploy.lock"
flock -n 9 || { echo 'Deployment lock busy' >&2; exit 1; }
case "$MODE" in
prepare)
  # Default package unit must never expose an alternate listener.
  if ! command -v nginx >/dev/null; then
    systemctl mask nginx.service
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx-light
  fi
  systemctl is-active --quiet nginx.service && { echo 'Unexpected nginx service; stop and inspect.' >&2; exit 1; }
  install -d -m 700 "$TLS"
  if [[ ! -e "$TLS/ca.crt" ]]; then
    [[ ! -e "$TLS/ca.key" && ! -e "$TLS/server.key" ]] || { echo 'Partial TLS state; inspect before retry.' >&2; exit 1; }
    umask 077
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 -subj '/CN=DSH LAN private CA' -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' -keyout "$TLS/ca.key" -out "$TLS/ca.crt" 2>/dev/null
    openssl req -new -newkey rsa:3072 -nodes -subj '/CN=192.168.100.200' -addext 'subjectAltName=IP:192.168.100.200' -keyout "$TLS/server.key" -out "$TLS/server.csr" 2>/dev/null
    printf '%s\n' 'subjectAltName=IP:192.168.100.200' 'basicConstraints=critical,CA:FALSE' 'keyUsage=critical,digitalSignature,keyEncipherment' 'extendedKeyUsage=serverAuth' > "$TLS/server.ext"
    openssl x509 -req -in "$TLS/server.csr" -CA "$TLS/ca.crt" -CAkey "$TLS/ca.key" -CAcreateserial -days 365 -sha256 -extfile "$TLS/server.ext" -out "$TLS/server.crt" 2>/dev/null
    rm "$TLS/server.csr" "$TLS/server.ext"
  fi
  openssl verify -CAfile "$TLS/ca.crt" -verify_ip 192.168.100.200 "$TLS/server.crt"
  openssl x509 -in "$TLS/server.crt" -checkend 2592000 -noout
  install -d -m 755 /etc/dsh-edge /var/log/dsh-edge /run/dsh-edge
  python3 - "$RELEASE" <<'PY'
import re, sys
from pathlib import Path
release = Path(sys.argv[1])
values = dict(LISTEN_ADDRESS='192.168.100.200', HTTPS_PORT='443', PUBLIC_AUTHORITY='192.168.100.200', PUBLIC_AUTHORITY_REGEX=r'192\.168\.100\.200', LAN_CIDR='192.168.100.0/24', BACKEND_ADDRESS='127.0.0.1', BACKEND_PORT='3080', CERTIFICATE_PATH='/etc/dsh-edge/tls/server.crt', KEY_PATH='/etc/dsh-edge/tls/server.key', ACCESS_LOG_PATH='/var/log/dsh-edge/access.log')
text = (release / 'scripts/deploy/nginx-dsh.conf.template').read_text()
for key, value in values.items(): text = text.replace('@' + key + '@', value)
assert not re.search(r'@[A-Z_]+@', text), 'Unresolved nginx parameter'
Path('/etc/dsh-edge/edge.conf').write_text(text)
Path('/etc/dsh-edge/nginx.conf').write_text('pid /run/dsh-edge/nginx.pid;\nerror_log stderr;\nevents {}\nhttp {\n include /etc/dsh-edge/edge.conf;\n}\n')
PY
  nginx -t -c /etc/dsh-edge/nginx.conf
  echo 'Prepared only. No release promotion, unit installation, restart, or remote client trust.'
  ;;
activate)
  RECEIPT=${4:?exact-release independent review receipt required}
  [[ -n "$RECEIPT" ]]
  nginx -t -c /etc/dsh-edge/nginx.conf
  BACKUP="$ROOT/rollback/$(date -u +%Y%m%dT%H%M%SZ)-${SHA:0:12}"
  install -d -m 700 "$BACKUP"
  readlink "$ROOT/current" > "$BACKUP/current"
  systemctl is-active --quiet dsh-edge.service && touch "$BACKUP/edge-active" || true
  systemctl stop dsh-edge.service 2>/dev/null || true
  systemctl stop dsh.service
  # Quiesce before the data snapshot; do not use a pre-migration backup as this snapshot.
  tar -C "$ROOT" -cpf "$BACKUP/data.tar" data
  tar -C / -cpf "$BACKUP/config.tar" etc/systemd/system/dsh.service etc/systemd/system/dsh.service.d etc/dsh-edge
  mkdir "$BACKUP/restore-test"
  tar -C "$BACKUP/restore-test" -xpf "$BACKUP/data.tar"
  diff -qr "$ROOT/data" "$BACKUP/restore-test/data" >/dev/null
  rm -rf "$BACKUP/restore-test"
  printf '%s\n' "$RECEIPT" > "$BACKUP/review-receipt"
  echo "Consistent rollback snapshot: $BACKUP"
  export PATH="$(dirname "$NODE"):/home/ras/.local/share/pnpm/bin:/usr/local/bin:/usr/bin:/bin"
  # Supported profile operations; never edit plugin vendor files or credential stores.
  runuser -u ras -- env PATH="$PATH" DSH_HOME="$ROOT/data" "$NODE" "$RELEASE/apps/cli/lib/bin.js" plugin --profile web add --save-exact dsh-plugin-subscriptions@0.9.7 @vectorize-io/hindsight-coding-agents@0.8.0
  runuser -u ras -- env PATH="$PATH" DSH_HOME="$ROOT/data" "$NODE" "$RELEASE/apps/cli/lib/bin.js" plugin --profile web allow-version dsh-plugin-subscriptions@0.9.7 --dsh-version 0.2.1-alpha.1 --accept-risk
  install -m 644 "$RELEASE/scripts/deploy/dsh-runtime.conf" /etc/systemd/system/dsh.service.d/runtime.conf
  install -m 644 "$RELEASE/scripts/deploy/dsh-edge.service" /etc/systemd/system/dsh-edge.service
  ln -s "releases/$(basename "$RELEASE")" "$ROOT/.current-commission"
  mv -Tf "$ROOT/.current-commission" "$ROOT/current"
  systemctl daemon-reload
  systemctl start dsh.service
  systemctl enable --now dsh-edge.service
  systemctl is-active --quiet dsh.service dsh-edge.service
  echo "Activated; now run cookbook acceptance. On any failure rollback with $BACKUP."
  ;;
rollback)
  BACKUP=$(realpath -e "${4:?consistent snapshot directory required}")
  [[ "$BACKUP" == "$ROOT/rollback/"* && -f "$BACKUP/data.tar" && -f "$BACKUP/config.tar" ]]
  systemctl stop dsh-edge.service 2>/dev/null || true
  systemctl stop dsh.service
  # Preserve failed-generation data privately; it is not downgrade-compatible.
  [[ ! -e "$BACKUP/failed-data" ]]
  mv "$ROOT/data" "$BACKUP/failed-data"
  tar -C "$ROOT" -xpf "$BACKUP/data.tar"
  rm -f /etc/systemd/system/dsh.service.d/runtime.conf
  tar -C / -xpf "$BACKUP/config.tar"
  ln -s "$(cat "$BACKUP/current")" "$ROOT/.current-commission"
  mv -Tf "$ROOT/.current-commission" "$ROOT/current"
  systemctl daemon-reload
  systemctl start dsh.service
  if [[ -f "$BACKUP/edge-active" ]]; then systemctl start dsh-edge.service; else systemctl disable dsh-edge.service; fi
  systemctl is-active --quiet dsh.service
  echo 'Matched release/data/config restored; validate old authenticated catalog before closing.'
  ;;
*) echo 'Unknown mode' >&2; exit 1 ;;
esac
