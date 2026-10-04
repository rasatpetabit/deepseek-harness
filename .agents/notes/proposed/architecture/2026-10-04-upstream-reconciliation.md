# Agent Note: Pinned upstream reconciliation and LAN HTTPS

Status: proposed

English | [中文](2026-10-04-upstream-reconciliation.zh.md)

## Problem

The fleet fork needs the published dsh-v0.2.1-alpha.1 runtime while retaining automatic LAN login, remote settings administration, durable credentials, and a reversible production release. The main checkout contains operator-owned operations documentation that cannot be included, stashed, or changed by this upgrade.

## Proposal

Merge the immutable commit named by dsh-v0.2.1-alpha.1 into an isolated repository-local worktree. Resolve upstream-owned conflicts against the target APIs, retain only justified house behavior, and commit candidate changes without pushing or integrating master. This serves the repository intent: upstream owns product architecture; the house delta owns fleet operation.

The approved design receipt is run bf5b55e1-45fd-4357-af18-09594f3bda04, reviewer gpt-6.1-sol, exact session c68eafac/run-0/session.jsonl under the operator-supplied reconciliation session. Its disposition is PASS for implementation, not activation. The parent owns serving-model attestation, independent implementation-plan review, and exact-artifact release review. Implementation plan receipt 487d53b8 is PASS. Proxy runtime amendment receipt 2c4a313f and source-selector receipt 5de578ef are PASS; they bind the reviewed template and immutable-source changes, not the subsequently assembled release.

## HTTPS and authentication

A standard nginx TLS proxy admits only 192.168.100.0/24 at server level and denies every other client uniformly across HTTP paths, assets, streams, and upgrades. The backend binds 127.0.0.1:3080. The browser-facing authority is https://192.168.100.200; nginx rejects other Host values, every nonempty nginx-parsed Origin other than that exact HTTPS origin (ordinary HTTP treats parsed-empty Origin as absent), opaque null origins, and cross-site Fetch Metadata. Browser WebSocket upgrades require the exact Origin. The backend receives the public Host; incoming forwarding headers are stripped and never supply identity. Cookies gain Secure at the edge, streams remain unbuffered, upgrades retain their protocol headers, and access logs omit query strings and tokens.

Retain the existing trustedNetworks cookie exchange only for clean GET / after browser admission. The accepted loopback socket denotes the trusted proxy hop, not the original browser. API calls still require a valid authority-bound signed cookie. Retain upstream directory-relative redirects, publicUrl behavior, persistent signing-secret ownership, and authenticated remote administration using the target ConfigForms API. Do not introduce an identity header parser or another secret.

A usable existing fleet issuer takes precedence; otherwise commissioning provisions a DSH-scoped private CA with an IP-SAN server certificate. The fleet Caddy internal-CA precedent does not demonstrate client trust. Preparation validates the chain without bypass, but installs no remote client trust. Exact-artifact review gates service activation, not inert certificate/release preparation. Remote browser trust remains a reported per-host limitation.

## Immutable deployment source

The deployment script consumes an explicit canonical clean committed source worktree and expected full immutable SHA. It verifies repository identity, source cleanliness including untracked files, and HEAD before and after dependency installation/build, archives that exact SHA, and overlays outputs only from the same source. It retains production confinement and service health checks. Wrong repositories, dirty sources, moving HEAD, and source mutations fail before promotion. It never commits or stashes main-checkout changes to bypass the clean-source requirement.

The [source-selection cookbook](../../../../docs/cookbook/deploy-house-source.md) owns the script arguments and scratch verification procedure. Worktree-local ignored build outputs remain necessary; final confinement rejects external dependency links, and `.git` files and directories are excluded from the output overlay. The selected worktree must have no concurrent writer during deployment. Phase checks detect observed changes but do not provide a filesystem lock against an unrelated writer changing and restoring source between checks.

## Plugin and data verification

Select newer compatible published plugin versions through scratch-profile runtime tests, not declared peer ranges alone. Installed subscriptions 0.9.2 and Hindsight 0.6.1 are baselines; subscriptions 0.9.7 excludes the target peers and Hindsight 0.8.0 fixes metadata. An exact-version compatibility exemption needs scratch capability evidence and independent review; global bypasses and vendor edits are forbidden.

Inventory every production Session generation and historical child before using the supported catalog migration machinery on copied scratch data. The reported 36 v3 logs require read/write/reopen verification at v4 with predecessor hashes unchanged. Compare provider, model, and effort identities across all seven routes, not only baseline counts: code 9/9, deepseek 1/1, LiteLLM 15/15. Restore the existing restic backup outside production, including configuration, credentials, plugin manifests, locks, and exact plugin trees; boot the matched old runtime against restored data without paid model calls. Credential contents never appear in reports.

## Alternatives considered

**Manual token login only** violates the required automatic LAN login. **Forwarded-client identity or a shared proxy secret** adds unnecessary parsing and credential lifecycle. **Direct LAN backend access** bypasses TLS and the uniform edge policy. **Deploying dirty main or archiving moving HEAD** disconnects the release from the reviewed artifact. **Code-only rollback** cannot establish compatibility after a durable-format upgrade.

## Acceptance criteria

The candidate includes the pinned upstream merge, narrowly justified conflict fixes, authentication rejection tests, proxy configuration tests, immutable-source deployment tests, affected README/JSDoc updates, and a decision record. Focused outgoing checks include build and typecheck because the merge changes published runtime paths; exhaustive local tests are not the default.

Before activation, independently review the exact committed artifact and demonstrate allowed/denied clients across every route class, automatic login/reload, no-cookie API denial, wrong Host/Origin rejection, spoofed forwarding-header non-identity, WebSocket Origin enforcement, backend LAN refusal, certificate IP SAN/chain/expiry, explicitly reported client-trust limits, consumed plugin capabilities, complete migration integrity, identity-preserving catalog comparison, and matched old-runtime restoration.

Rollback restores the old release together with its consistent data, plugins, settings, credentials, service configuration, and edge configuration after quiescing writers. Preserved predecessor logs are not a downgrade guarantee. Production never executes from the development worktree.

## Risks

The seven forecast merge conflicts and plugin compatibility remain implementation work, not assumed successes. TLS client trust, migration integrity, restored-runtime evidence, and independent review are activation prerequisites. This proposal does not authorize workflow, installer, vendor, or harness-core repairs unrelated to the reconciliation. The operator-owned operations file remains byte-for-byte unchanged.
