# Commission the house release

English | [中文](deploy-house-commissioning.zh.md)

## Scope

This procedure prepares the reviewed house upgrade outside `/srv/dev` and activates only after independent review of its exact commit. It uses the [immutable-source installer](deploy-house-source.md) and [LAN HTTPS policy](deploy-house-https.md). Do not push this fork upstream. Preparation never promotes `current`, restarts DSH, or installs remote browser trust.

## 1. Prepare

Select a clean registered worktree at the assembled commit; concurrent source writers are forbidden. Use the persistent DSH-only Node runtime, not a global Node replacement. Clean removed-package residue before building.

```sh
export PATH=/srv/deepseek-harness/toolchains/node-v24.21.0-linux-x64/bin:$PATH
pnpm run clean
bash scripts/deploy-dsh.sh --source "$SOURCE" --expected-sha "$SHA" --prepare-only
sudo bash "$RELEASE/scripts/commission-dsh.sh" prepare "$RELEASE" "$SHA"
```

Set `RELEASE` to the printed prepared directory. Its `RELEASE_COMMIT` must equal `SHA`. The release includes all runtime outputs and dependencies, with every symlink confined inside it. Preparation uses a usable fleet issuer first; when none is available the scoped local CA is root-owned in `/etc/dsh-edge/tls`. Existing TLS files are reused, not rotated. The server certificate expires after one year; the operator owns renewal using the same scoped issuer before expiry. Validate the IP SAN, chain and at least 30 days remaining validity. Preparation masks the stock nginx unit before package installation and uses a dedicated edge unit with no default-site includes. No other nginx service may be active.

Remote client trust is not installed. HTTPS is valid when verified with `ca.crt`; intended browsers require separately authorized CA installation per host. Never claim browser trust from `curl --cacert`, and never use `-k` for acceptance.

## 2. Review and activate

Review the exact commit, prepared release, rendered nginx configuration, Node runtime, compatibility/migration/restore evidence, and this rollback procedure. Record the independent receipt; a nonempty string is an operator attestation, not automatic validation of the review. Only then:

```sh
sudo bash "$RELEASE/scripts/commission-dsh.sh" activate "$RELEASE" "$SHA" "$RECEIPT"
```

The command takes the deploy lock, stops writers, snapshots all data and matching configuration privately under `/srv/deepseek-harness/rollback`, extracts and compares a restore test, and reports the snapshot path. It uses supported `dsh plugin` operations to pin subscriptions 0.9.7 and Hindsight 0.8.0, with only the subscriptions 0.9.7 / DSH 0.2.1-alpha.1 exemption. This exemption accepts the exact documented incompatibility risk; it is not a global bypass. Credential files and the persisted browser signing secret must remain byte-identical. The runtime drop-in retains `hindsight.conf`; the final profile overlay binds loopback and advertises the HTTPS authority.

If a command fails after stopping writers, the service may remain stopped: use the printed consistent snapshot to roll back immediately. Do not restart the old runtime against changed data. If snapshot creation itself fails, no plugin or release promotion has occurred; inspect the error and start the unchanged old service. Keep the last known-good release and its consistent snapshot; do not prune them during commissioning.

## 3. Validate

```sh
curl --noproxy '*' --cacert /etc/dsh-edge/tls/ca.crt -D /tmp/dsh-headers -o /dev/null https://192.168.100.200/
curl --noproxy '*' --cacert /etc/dsh-edge/tls/ca.crt -o /dev/null -w '%{http_code}\n' https://192.168.100.200/api
ss -ltn
systemctl is-active dsh.service dsh-edge.service
```

The clean root must issue an unchanged signed cookie with Secure, HttpOnly and SameSite=Strict and redirect `./`; reload with that cookie must succeed. Unauthenticated API must return 401. Verify bad Host, nonexact nonempty Origin, cross-site Fetch Metadata and upgrades without exact Origin return 403 across all path classes. Verify direct LAN backend access fails; the only backend listener is `127.0.0.1:3080`, and the only edge listener is `192.168.100.200:443`. Run actual authenticated catalog/status requests and compare all 25 identities, efforts and the default against the recovered old runtime. Provider generation and OAuth refresh remain unverified unless separately authorized; do not spend on model calls merely to close this gate. Hindsight diagnose/sync can use the real backend without generating a model response, but injection and retention remain unverified if not exercised safely. Remove private scratch credential copies only after preserving redacted acceptance evidence.

## 4. Roll back

```sh
sudo bash "$RELEASE/scripts/commission-dsh.sh" rollback "$RELEASE" "$SHA" "$BACKUP"
```

Rollback stops both writers, preserves failed-generation data privately, restores the consistent data/configuration snapshot, and restores the old release pointer and prior edge activity. Verify the old authenticated catalog/status before declaring recovery. v3 predecessors remaining on disk do not establish downgrade compatibility; restoring matched data and plugins is mandatory.
