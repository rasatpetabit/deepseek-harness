"""Exercise the rendered HTTPS template with real nginx and a deterministic backend.

Requires nginx with the HTTP TLS module (>=1.19.3), openssl, and a local IPv4
address in DSH_TEST_LAN_CIDR. No network configuration or live service is changed.
Set DSH_TEST_NGINX to a scratch executable when nginx is not installed.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "scripts/deploy/nginx-dsh.conf.template"
LAN_CIDR = os.environ.get("DSH_TEST_LAN_CIDR", "192.168.100.0/24")
PUBLIC_AUTHORITY = os.environ.get("DSH_TEST_PUBLIC_AUTHORITY", "192.168.100.200")
ORIGIN = f"https://{PUBLIC_AUTHORITY}"
COOKIE = "dsh_session=unchanged.signed-value"
ROUTES = ["/", "/assets/app.js", "/api", "/api/stream", "/api/ws"]


def test_template_exists():
    assert TEMPLATE.is_file(), "Task 3 requires the repository-owned nginx template"


def _lan_address():
    configured = os.environ.get("DSH_TEST_LAN_ADDRESS")
    if configured:
        assert ipaddress.ip_address(configured) in ipaddress.ip_network(LAN_CIDR)
        return configured
    interfaces = json.loads(subprocess.check_output(["ip", "-json", "address"], text=True))
    for interface in interfaces:
        for address in interface["addr_info"]:
            if address["family"] == "inet" and ipaddress.ip_address(address["local"]) in ipaddress.ip_network(LAN_CIDR):
                return address["local"]
    pytest.fail("No local LAN address; set DSH_TEST_LAN_ADDRESS on a host in DSH_TEST_LAN_CIDR")


class Backend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        record = {"path": self.path, "headers": dict(self.headers), "peer": self.client_address[0],
                  "body": body.decode()}
        with self.server.record_lock:
            self.server.records.append(record)
        if self.path.startswith("/api/ws"):
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.write(b"upgraded")
            self.wfile.flush()
            self.connection.settimeout(5)
            try:
                data = self.connection.recv(4)
                self.wfile.write(data)
                self.wfile.flush()
            except (OSError, TimeoutError):
                pass  # The client may close immediately after inspecting the upgrade.
            self.close_connection = True
            return
        if self.path.startswith("/api/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "12")
            self.end_headers()
            self.wfile.write(b"first\n")
            self.wfile.flush()
            # Release only after the client observes the first chunk: buffering deadlocks.
            self.server.release_stream.wait(5)
            self.wfile.write(b"last!\n")
            self.wfile.flush()
            return
        body = json.dumps(record).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", COOKIE + "; Path=/; HttpOnly; SameSite=Strict")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass  # Backend request records are asserted directly, without query-bearing logs.


class Proxy:
    def __init__(self, port, address, directory, backend):
        self.port = port
        self.address = address
        self.directory = directory
        self.backend = backend
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.context.check_hostname = False
        self.context.verify_mode = ssl.CERT_NONE  # Scratch certificates are never a browser-trust claim.

    @contextmanager
    def connection(self, source=None):
        connection = http.client.HTTPSConnection(
            self.address, self.port, timeout=3, context=self.context,
            source_address=(source or self.address, 0),
        )
        try:
            yield connection
        finally:
            connection.close()

    def request(self, path="/", headers=None, source=None):
        with self.connection(source) as connection:
            supplied = {"Host": PUBLIC_AUTHORITY, **(headers or {})}
            connection.request("GET", path, headers=supplied)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()


@pytest.fixture(scope="module")
def proxy(tmp_path_factory):
    executable = os.environ.get("DSH_TEST_NGINX") or shutil.which("nginx")
    assert executable and Path(executable).is_file(), "Actual nginx is required; set DSH_TEST_NGINX"
    address = _lan_address()
    directory = tmp_path_factory.mktemp("https-proxy")
    os.chmod(directory, 0o700)
    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    backend.daemon_threads = False
    backend.records = []
    backend.record_lock = threading.Lock()
    backend.release_stream = threading.Event()
    thread = threading.Thread(target=backend.serve_forever)
    thread.start()
    process = None
    child_env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=scratch-only", "-addext", f"subjectAltName=IP:{address}",
            "-keyout", str(directory / "key.pem"), "-out", str(directory / "cert.pem"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_env)
        # nginx's inherited-listener mechanism transfers the bound socket without a free-port race.
        with socket.socket() as reservation:
            reservation.bind((address, 0))
            reservation.listen()
            port = reservation.getsockname()[1]
            values = {
                "LISTEN_ADDRESS": address, "HTTPS_PORT": str(port), "LAN_CIDR": LAN_CIDR,
                "PUBLIC_AUTHORITY": PUBLIC_AUTHORITY,
                "PUBLIC_AUTHORITY_REGEX": re.escape(PUBLIC_AUTHORITY),
                "CERTIFICATE_PATH": str(directory / "cert.pem"), "KEY_PATH": str(directory / "key.pem"),
                "BACKEND_ADDRESS": "127.0.0.1", "BACKEND_PORT": str(backend.server_port),
                "ACCESS_LOG_PATH": str(directory / "access.log"),
            }
            rendered = TEMPLATE.read_text()
            for key, value in values.items():
                rendered = rendered.replace(f"@{key}@", value)
            assert not re.search(r"@[A-Z_]+@", rendered), "Unresolved template parameter"
            (directory / "edge.conf").write_text(rendered)
            config = directory / "nginx.conf"
            config.write_text(
                f"pid {directory}/nginx.pid;\nerror_log {directory}/error.log info;\n"
                "worker_processes 1;\nevents { worker_connections 64; }\nhttp {\n"
                f"client_body_temp_path {directory}/body;\nproxy_temp_path {directory}/proxy;\n"
                f"fastcgi_temp_path {directory}/fastcgi;\nuwsgi_temp_path {directory}/uwsgi;\n"
                f"scgi_temp_path {directory}/scgi;\ninclude {directory}/edge.conf;\n}}\n"
            )
            command = [executable, "-p", str(directory) + "/", "-c", str(config)]
            syntax = subprocess.run(command + ["-t"], capture_output=True, text=True, env=child_env)
            assert syntax.returncode == 0, syntax.stderr
            print(syntax.stderr.strip())
            process = subprocess.Popen(
                command + ["-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, pass_fds=(reservation.fileno(),),
                env={**child_env, "NGINX": f"{reservation.fileno()};"},
            )
        edge = Proxy(port, address, directory, backend)
        deadline = time.monotonic() + 5
        while True:
            assert process.poll() is None, process.communicate()[1]
            try:
                edge.request()
                break
            except (OSError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    pytest.fail("Scratch nginx did not listen before the startup deadline")
                time.sleep(0.02)
        yield edge
    finally:
        backend.release_stream.set()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "Backend teardown did not reach quiescence"
        shutil.rmtree(directory)


@pytest.mark.parametrize("path", ROUTES)
def test_lan_allowed_and_off_network_denied_on_every_route(proxy, path):
    headers = {"Origin": ORIGIN}
    if path == "/api/ws":
        headers.update({"Upgrade": "websocket", "Connection": "Upgrade"})
        with proxy.connection() as connection:
            connection.request("GET", path, headers={"Host": PUBLIC_AUTHORITY, **headers})
            assert connection.getresponse().status == 101
    elif path == "/api/stream":
        proxy.backend.release_stream.set()
        assert proxy.request(path, headers)[0] == 200
    else:
        assert proxy.request(path, headers)[0] == 200
    before = len(proxy.backend.records)
    assert proxy.request(path, headers, source="127.0.0.1")[0] == 403
    assert len(proxy.backend.records) == before


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("headers", [
    {"Host": "wrong.invalid"}, {"Host": PUBLIC_AUTHORITY + ":443"},
    {"Origin": "http://" + PUBLIC_AUTHORITY}, {"Origin": "https://wrong.invalid"},
    {"Origin": "null"}, {"Origin": ORIGIN + "/"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_browser_admission_rejects_without_reaching_backend(proxy, path, headers):
    before = len(proxy.backend.records)
    assert proxy.request(path, headers)[0] == 403
    assert len(proxy.backend.records) == before


@pytest.mark.parametrize("origin", [None, "", "   ", "\t", "null", "http://" + PUBLIC_AUTHORITY, "https://wrong.invalid"])
def test_websocket_requires_exact_https_origin(proxy, origin):
    headers = {"Upgrade": "websocket", "Connection": "Upgrade"}
    if origin is not None:
        headers["Origin"] = origin
    assert proxy.request("/api/ws", headers)[0] == 403


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/api"])
@pytest.mark.parametrize("origin", [None, "", "   "])
def test_native_nginx_collapses_empty_origin_into_absent(proxy, path, origin):
    # Ordinary HTTP admits parsed-empty Origin as absent under receipt 5de578ef.
    headers = {} if origin is None else {"Origin": origin}
    status, _, body = proxy.request(path, headers)
    assert status == 200
    assert "Origin" not in json.loads(body)["headers"]
    assert proxy.request(path, headers, source="127.0.0.1")[0] == 403
    assert proxy.request(path, {**headers, "Sec-Fetch-Site": "cross-site"})[0] == 403


@pytest.mark.parametrize("origin", ["\t", " \t "])
def test_tab_containing_origin_is_rejected_without_backend_admission(proxy, origin):
    before = len(proxy.backend.records)
    assert proxy.request("/", {"Origin": origin})[0] == 403
    assert len(proxy.backend.records) == before


def test_cross_site_upgrade_and_spoofed_lan_identity_are_rejected(proxy):
    before = len(proxy.backend.records)
    assert proxy.request("/api/ws", {
        "Origin": ORIGIN, "Upgrade": "websocket", "Connection": "Upgrade", "Sec-Fetch-Site": "cross-site",
    })[0] == 403
    assert proxy.request("/", {
        "Forwarded": f"for={proxy.address}", "X-Forwarded-For": proxy.address, "X-Real-IP": proxy.address,
    }, source="127.0.0.1")[0] == 403
    assert len(proxy.backend.records) == before


def test_plain_http_cannot_reach_backend(proxy):
    before = len(proxy.backend.records)
    connection = http.client.HTTPConnection(proxy.address, proxy.port, timeout=3)
    try:
        connection.request("GET", "/", headers={"Host": PUBLIC_AUTHORITY})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
    assert len(proxy.backend.records) == before


def test_upgrade_forwards_protocol_and_bidirectional_bytes(proxy):
    with proxy.connection() as connection:
        connection.request("GET", "/api/ws?channel=1", headers={
            "Host": PUBLIC_AUTHORITY, "Origin": ORIGIN, "Upgrade": "websocket", "Connection": "Upgrade",
            "Sec-WebSocket-Key": "dGVzdC1ub25jZQ==", "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Protocol": "scratch", "Sec-WebSocket-Extensions": "permessage-deflate",
        })
        response = connection.getresponse()
        assert response.status == 101
        assert response.getheader("Upgrade").lower() == "websocket"
        assert response.fp.read(8) == b"upgraded"
        connection.sock.sendall(b"ping")
        assert response.fp.read(4) == b"ping"
    record = proxy.backend.records[-1]
    assert record["path"] == "/api/ws?channel=1"
    for key, value in {"Upgrade": "websocket", "Connection": "upgrade", "Sec-WebSocket-Key": "dGVzdC1ub25jZQ==",
                       "Sec-WebSocket-Version": "13", "Sec-WebSocket-Protocol": "scratch",
                       "Sec-WebSocket-Extensions": "permessage-deflate"}.items():
        assert record["headers"][key] == value


def test_spoofed_headers_are_absent_and_signed_cookie_value_survives(proxy):
    status, headers, body = proxy.request("/api?token=scratch-sensitive&x=1", {
        "Forwarded": "for=192.168.100.5;host=evil.invalid;proto=http",
        "X-Forwarded-For": "192.168.100.5", "X-Forwarded-Host": "evil.invalid",
        "X-Forwarded-Proto": "http", "X-Forwarded-Port": "80", "X-Forwarded-Arbitrary": "spoof",
        "X-Real-IP": "192.168.100.5", "Cookie": COOKIE,
        "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", "Accept": "application/json",
    })
    assert status == 200
    record = json.loads(body)
    assert record["path"] == "/api?token=scratch-sensitive&x=1"
    assert record["peer"] == "127.0.0.1"
    assert record["headers"]["Host"] == PUBLIC_AUTHORITY
    assert record["headers"]["Cookie"] == COOKIE
    assert not any(key.lower() == "forwarded" or key.lower().startswith("x-forwarded-")
                   or key.lower() == "x-real-ip" for key in record["headers"])
    cookie = headers["Set-Cookie"]
    assert cookie.split(";")[0] == COOKIE
    for flag in ["secure", "httponly", "samesite=strict"]:
        assert flag in [part.strip().lower() for part in cookie.split(";")[1:]]
    # Include rejected requests: request URIs must not leak through nginx's error log either.
    assert proxy.request("/?token=scratch-sensitive", source="127.0.0.1")[0] == 403
    assert proxy.request("/?token=scratch-sensitive", {"Origin": "null"})[0] == 403
    logs = (proxy.directory / "access.log").read_text() + (proxy.directory / "error.log").read_text()
    assert "scratch-sensitive" not in logs
    assert "token=" not in logs
    assert "/api" in logs


def test_post_preserves_body_and_application_headers(proxy):
    body = '{"method":"scratch"}'
    with proxy.connection() as connection:
        connection.request("POST", "/api?mode=rpc", body=body, headers={
            "Host": PUBLIC_AUTHORITY, "Origin": ORIGIN, "Content-Type": "application/json",
            "Cookie": COOKIE, "Authorization": "Bearer scratch", "Last-Event-ID": "event-1",
        })
        response = connection.getresponse()
        assert response.status == 200
        record = json.loads(response.read())
    assert record["path"] == "/api?mode=rpc"
    assert record["body"] == body
    for key, value in {"Content-Type": "application/json", "Content-Length": str(len(body)),
                       "Cookie": COOKIE, "Authorization": "Bearer scratch", "Last-Event-ID": "event-1"}.items():
        assert record["headers"][key] == value


def test_stream_delivers_first_chunk_before_backend_completion(proxy):
    proxy.backend.release_stream.clear()
    try:
        with proxy.connection() as connection:
            connection.request("GET", "/api/stream", headers={"Host": PUBLIC_AUTHORITY})
            response = connection.getresponse()
            assert response.status == 200
            assert response.read(6) == b"first\n"
            assert not proxy.backend.release_stream.is_set()
            proxy.backend.release_stream.set()
            assert response.read() == b"last!\n"
    finally:
        proxy.backend.release_stream.set()
