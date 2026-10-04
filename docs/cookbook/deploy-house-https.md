# Prepare the house HTTPS proxy

English | [中文](deploy-house-https.zh.md)

## Summary

Prepare a LAN-only nginx edge for automatic browser login at `https://192.168.100.200` without exposing the loopback backend or changing its persistent signing secret. This procedure prepares and tests repository artifacts; it does not authorize installation, certificate commissioning, service restart, or activation. The [reconciliation decision](../../.agents/notes/proposed/architecture/2026-10-04-upstream-reconciliation.md) owns the security policy and unresolved commissioning choices.

## Table of Contents

- [1. Select parameters](#1-select-parameters)
- [2. Verify the scratch edge](#2-verify-the-scratch-edge)
- [3. Satisfy activation prerequisites](#3-satisfy-activation-prerequisites)
- [Forwarding reference](#forwarding-reference)

<a id="1-select-parameters"></a>

## 1. Select parameters

Use nginx with the HTTP TLS module and `proxy_cookie_flags` support (nginx 1.19.3 or newer). Render the [template](../../scripts/deploy/nginx-dsh.conf.template) by literal replacement of each `@PARAMETER@`, then include the rendered file inside a dedicated nginx `http` block. Never use unrestricted environment substitution: nginx variables such as `$http_host` must survive. Do not combine this configuration with another listener or inherited real-IP configuration that can bypass or change the socket-address ACL.

| Parameter | Deployment value or source |
| --- | --- |
| `LISTEN_ADDRESS`, `HTTPS_PORT` | Approved public listener: `192.168.100.200`, `443` |
| `PUBLIC_AUTHORITY` | `192.168.100.200` without scheme or port |
| `PUBLIC_AUTHORITY_REGEX` | Regex-escaped authority: `192\.168\.100\.200` |
| `LAN_CIDR` | Approved client network: `192.168.100.0/24`; never add loopback |
| `BACKEND_ADDRESS`, `BACKEND_PORT` | Existing loopback backend: `127.0.0.1`, `3080` |
| `CERTIFICATE_PATH`, `KEY_PATH` | Absolute paths supplied by the operator's certificate procedure |
| `ACCESS_LOG_PATH` | Absolute log path supplied by the deployment configuration |

Treat parameters as trusted nginx configuration, not user input. Refuse unresolved placeholders. Preserve the one server-level ACL and one forwarding location for root, assets, API, streams, and upgrades. The public Host must exactly match the authority without `:443`; any nonempty Origin must exactly match the HTTPS origin. Every upgrade requires that Origin, and cross-site Fetch Metadata is rejected.

<a id="2-verify-the-scratch-edge"></a>

## 2. Verify the scratch edge

On a Linux host with a local address in the approved LAN, install no system packages or services for this check. Supply an existing nginx executable, plus `openssl`, `ip`, Python, and pytest. The owner-local test creates a private temporary prefix, self-signed test certificate, high-port HTTPS listener, and deterministic loopback backend; it runs the real `nginx -t` and tears down both servers. Scratch certificates do not establish browser trust.

```sh
DSH_TEST_NGINX=/tmp/dsh-nginx-runtime/usr/sbin/nginx python3 -m pytest -q -s scripts/tests/test_https_proxy.py
```

The command must report successful nginx syntax validation and passing runtime cases. A missing executable, TLS module, or LAN address fails verification rather than skipping it. `DSH_TEST_LAN_ADDRESS` can select an existing local address; `DSH_TEST_LAN_CIDR` and `DSH_TEST_PUBLIC_AUTHORITY` allow a separately isolated test network without changing deployment policy. The listener/backend ports are allocated dynamically, never copied from a running service. Runtime checks cover allowed LAN and denied loopback source sockets across every route class, browser admission, bidirectional upgrades, header removal, unchanged cookie values, query-free logging, and first-chunk streaming before backend completion.

<a id="3-satisfy-activation-prerequisites"></a>

## 3. Satisfy activation prerequisites

The release operator owns these checks after independent exact-artifact review. This cookbook records required evidence, not a completed production deployment.

1. Resolve issuer selection and certificate renewal ownership. Verify the certificate IP SAN contains the approved public IP, the chain is complete, expiry is acceptable, and each intended browser trusts that chain without a bypass. Do not substitute scratch certificate success for this evidence.
2. Verify the backend binds only `127.0.0.1:3080` and refuses direct LAN connections. Configure the existing browser trust policy for the expected public Host and HTTPS origin, with trusted loopback identifying only the proxy hop. Preserve the persisted browser signing secret and existing credentials; do not generate a new proxy secret.
3. Validate the final rendered configuration with `nginx -t` in its intended isolated runtime configuration. Repeat allowed/denied route checks using actual LAN and off-network clients; ensure no alternate listener, location, inherited real-IP directive, or HTTP fallback bypasses admission.
4. From an intended trusted browser, verify clean `GET /` automatically sets the signed cookie and redirects `./`, reload succeeds, and API without a cookie fails. Verify wrong Host/Origin, HTTP/null Origin, cross-site Fetch Metadata, forged forwarding headers, and upgrades without the exact Origin are rejected. Confirm the backend receives the original accepted public Host, not its loopback authority.
5. Retain the reviewed release and matched rollback configuration outside `/srv/dev`, including consistent data, plugins, settings, credentials, service configuration, and edge configuration. Installation and activation require their separate authorization; this procedure performs neither.

<a id="forwarding-reference"></a>

## Forwarding reference

The edge forwards an explicit request-header allowlist rather than parsing forwarding identities. It retains cookies, Origin, Fetch Metadata, authorization, content framing, browser content negotiation, conditional/range requests, SSE resumption, and WebSocket protocol headers. It drops `Forwarded`, `X-Real-IP`, all `X-Forwarded-*`, and other unlisted headers; extensions needing another header must update the template and runtime tests. Signed cookie values remain unchanged while response cookies gain Secure, HttpOnly, and SameSite=Strict. Streams and request bodies are unbuffered, and the proxy preserves query strings when forwarding.

Access logs record the method and path, never `$request`, `$request_uri`, or query arguments. The server discards request error logs because nginx error messages can include full query-bearing URIs; this sacrifices request-level proxy diagnostics to avoid login-token logging. Global startup diagnostics remain outside that server log and must not add request-bearing logging. nginx's standard proxy timeouts remain in force; long-lived streams and upgrades need backend keepalive traffic within those limits.
