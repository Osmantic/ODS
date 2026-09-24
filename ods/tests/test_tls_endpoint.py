"""Exercise the operator CLI against real, ephemeral TLS endpoints."""

import hashlib
import json
from pathlib import Path
import shutil
import socketserver
import ssl
import subprocess
import sys
import threading

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-tls-endpoint.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True, text=True, timeout=10,
    )


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is required to generate ephemeral TLS fixtures")
    directory = tmp_path_factory.mktemp("TLS certificate with spaces")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run([
        openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "2",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
    ], check=True, capture_output=True)
    return cert, key


@pytest.fixture
def endpoint(certificate):
    cert, key = certificate
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                connection = context.wrap_socket(self.request, server_side=True)
            except ssl.SSLError:
                # Negative tests intentionally reject this server's certificate.
                return
            with connection:
                assert connection.recv(1) == b"", "CLI must not send application data"

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        yield server.server_address[1]
        server.shutdown()
        worker.join(timeout=5)


def test_verified_endpoint_receipt(endpoint, certificate):
    result = run_cli("127.0.0.1", "--port", endpoint, "--server-name", "localhost",
                     "--ca-file", certificate[0], "--min-valid-days", "1")
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["schema_version"] == 1
    assert receipt["ok"] is True
    assert receipt["reason"] == "healthy"
    assert receipt["server_name"] == "localhost"
    assert 1 < receipt["certificate"]["remaining_days"] <= 2
    der = ssl.PEM_cert_to_DER_cert(certificate[0].read_text())
    assert receipt["certificate"]["sha256"] == hashlib.sha256(der).hexdigest()
    assert receipt["certificate"]["expires_at"].endswith("+00:00")
    assert receipt["tls_version"].startswith("TLSv1.")


def test_valid_but_soon_expiring_certificate_fails_threshold(endpoint, certificate):
    result = run_cli("127.0.0.1", "--port", endpoint, "--server-name", "localhost",
                     "--ca-file", certificate[0])
    assert result.returncode == 1
    receipt = json.loads(result.stdout)
    assert receipt["ok"] is False
    assert receipt["reason"] == "expires_soon"
    assert receipt["min_valid_days"] == 7


@pytest.mark.parametrize("trusted,server_name", [(False, "localhost"), (True, "wrong.example")])
def test_chain_and_hostname_verification_cannot_be_bypassed(endpoint, certificate, trusted, server_name):
    args = ["--ca-file", certificate[0]] if trusted else []
    result = run_cli("127.0.0.1", "--port", endpoint, "--server-name", server_name, *args)
    assert result.returncode == 1
    receipt = json.loads(result.stdout)
    assert receipt["reason"] == "certificate_verification_failed"
    assert receipt["certificate"] is None


@pytest.mark.parametrize("args", [
    ["--timeout", "nan"], ["--timeout", "0"], ["--timeout", "inf"],
    ["--min-valid-days", "-1"], ["--min-valid-days", "nan"],
    ["--port", "0"], ["--port", "65536"],
])
def test_invalid_arguments_fail_before_connecting(args):
    result = run_cli("localhost", *args)
    assert result.returncode == 2
    assert not result.stdout


def test_missing_ca_is_configuration_error(tmp_path):
    result = run_cli("localhost", "--ca-file", tmp_path / "missing.pem")
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "ca_configuration_error"


def test_handshake_timeout_is_reported():
    stop = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            stop.wait(2)

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        try:
            result = run_cli("127.0.0.1", "--port", server.server_address[1], "--timeout", ".1")
            assert result.returncode == 1
            assert json.loads(result.stdout)["reason"] == "timeout"
        finally:
            stop.set()
            worker.join(timeout=5)
