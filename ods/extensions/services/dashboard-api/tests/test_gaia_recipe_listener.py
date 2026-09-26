"""AMD GAIA's full-mode backend must be reachable off loopback.

amd-gaia 0.19.0 runs uvicorn on 127.0.0.1 only, so the dashboard's health
check (http://gaia:4200/ from another container) and the published host port
got "connection refused" while Docker's in-container healthcheck passed. The
recipe's entrypoint runs the backend on a loopback-only port behind
ods-gaia-forward. These tests run the real entrypoint and forwarder against a
stub gaia-ui that, like AMD's backend, listens on 127.0.0.1 only (no GAIA
download), then reach it through a non-loopback address of this host.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
import yaml


ODS = Path(__file__).resolve().parents[4]
RECIPE = ODS / "extensions/library/services/gaia"

STUB_GAIA_UI = r"""#!/usr/bin/env node
// Stub gaia-ui. Full mode binds 127.0.0.1 like amd-gaia's uvicorn backend;
// --serve binds every interface like gaia-ui's static server.
const fs = require("fs");
const http = require("http");
const state = process.env.STUB_STATE_DIR;
const args = process.argv.slice(2);
fs.writeFileSync(`${state}/argv.json`, JSON.stringify(args));
const port = Number(args[args.indexOf("--port") + 1]);
const server = http.createServer((req, res) => {
  res.writeHead(200, { "content-type": "text/plain" });
  res.end(`stub gaia ${req.url}`);
});
setTimeout(() => {
  server.listen(port, args.includes("--serve") ? undefined : "127.0.0.1",
    () => fs.writeFileSync(`${state}/listening`, String(process.pid)));
}, Number(process.env.STUB_LISTEN_DELAY_MS || 0));
setInterval(() => {}, 1 << 30);  // like the wrapper: alive without its server
process.on("SIGTERM", () => { fs.writeFileSync(`${state}/signal`, "SIGTERM"); process.exit(0); });
process.on("SIGUSR1", () => server.close());  // the backend dies, the wrapper lives
process.on("SIGUSR2", () => process.exit(0));  // the wrapper exits by itself
"""

needs_linux_node = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("node") is None or shutil.which("bash") is None,
    reason="runs the recipe's Bash entrypoint and Node forwarder on Linux",
)
# Direct connections only: a proxy from the environment must not answer for GAIA.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _free_ports(count):
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _non_loopback_ipv4():
    """This host's source address toward TEST-NET-1 (connecting UDP sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
        except OSError as error:
            pytest.skip(f"no non-loopback IPv4 route: {error}")
        address = sock.getsockname()[0]
    assert not address.startswith("127."), address
    return address


def _wait_for(predicate, what, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _get(url):
    with _OPENER.open(url, timeout=5) as response:
        return response.status, response.read().decode()


def _refused(host, port):
    with socket.socket() as sock:
        sock.settimeout(2)
        return sock.connect_ex((host, port)) != 0


def _listen_address(port):
    """Local IPv4 address of the LISTEN socket on `port`, from /proc/net/tcp."""
    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        fields = line.split()
        address, hex_port = fields[1].split(":")
        if int(hex_port, 16) == port and fields[3] == "0A":
            return socket.inet_ntoa(bytes.fromhex(address)[::-1])
    return None


class Entrypoint:
    def __init__(self, tmp_path, **env):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "gaia-ui").write_text(STUB_GAIA_UI)
        shutil.copyfile(RECIPE / "ods-gaia-forward.cjs", bin_dir / "ods-gaia-forward")
        for name in ("gaia-ui", "ods-gaia-forward"):
            (bin_dir / name).chmod(0o755)
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.port, self.backend_port = _free_ports(2)
        self.log = tmp_path / "entrypoint.log"
        self.env = {
            "PATH": os.pathsep.join([str(bin_dir), str(Path(shutil.which("node")).parent), "/usr/bin", "/bin"]),
            "HOME": str(self.home),
            "STUB_STATE_DIR": str(self.state),
            "GAIA_INTERNAL_PORT": str(self.port),
            "GAIA_BACKEND_PORT": str(self.backend_port),
            "ODS_GAIA_PROBE_INTERVAL_MS": "100",
            **env,
        }
        self.proc = None

    def start(self):
        with self.log.open("wb") as log:
            self.proc = subprocess.Popen(["bash", str(RECIPE / "docker-entrypoint.sh")], env=self.env,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        return self

    def argv(self):
        return json.loads((self.state / "argv.json").read_text())

    def wait_listening(self):
        _wait_for(lambda: (self.state / "listening").exists(), "the stub gaia-ui to listen")
        _wait_for(lambda: _listen_address(self.port) is not None, f"a listener on port {self.port}")

    def wait_exit(self, timeout=15):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"entrypoint still running:\n{self.log.read_text()}") from None

    def kill_all(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=5)


@pytest.fixture()
def entrypoint(tmp_path):
    started = []

    def make(**env):
        started.append(Entrypoint(tmp_path, **env).start())
        return started[-1]

    yield make
    for item in started:
        item.kill_all()


@needs_linux_node
def test_full_mode_serves_the_loopback_backend_on_a_non_loopback_address(entrypoint):
    ep = entrypoint()
    ep.wait_listening()
    host = _non_loopback_ipv4()
    # Before the fix gaia-ui itself got the service port: connection refused here.
    assert _get(f"http://{host}:{ep.port}/api/health") == (200, "stub gaia /api/health")
    assert _get(f"http://127.0.0.1:{ep.port}/") == (200, "stub gaia /")
    assert _listen_address(ep.port) == "0.0.0.0"
    # The stub reproduces AMD's backend: its own port stays loopback-only.
    assert ep.argv() == ["--port", str(ep.backend_port), "--no-open"]
    assert _listen_address(ep.backend_port) == "127.0.0.1"
    assert _refused(host, ep.backend_port)

    ep.proc.send_signal(signal.SIGTERM)
    assert ep.wait_exit() == 128 + signal.SIGTERM
    assert (ep.state / "signal").read_text() == "SIGTERM"
    assert _refused("127.0.0.1", ep.port)
    assert "received SIGTERM" in ep.log.read_text()


@needs_linux_node
def test_entrypoint_exits_non_zero_when_gaia_ui_exits(entrypoint):
    ep = entrypoint()
    ep.wait_listening()
    os.kill(int((ep.state / "listening").read_text()), signal.SIGUSR2)  # exits with status 0
    assert ep.wait_exit() == 1
    assert "gaia-ui exited with status 0" in ep.log.read_text()
    assert _refused("127.0.0.1", ep.port)


@needs_linux_node
def test_entrypoint_exits_non_zero_when_the_backend_dies_under_a_live_wrapper(entrypoint):
    ep = entrypoint()
    ep.wait_listening()
    _wait_for(lambda: "accepting connections" in ep.log.read_text(), "the forwarder to see the backend")
    os.kill(int((ep.state / "listening").read_text()), signal.SIGUSR1)  # closes its server only
    assert ep.wait_exit() == 1
    log = ep.log.read_text()
    assert "stopped accepting connections" in log
    assert "ods-gaia-forward exited with status 1; stopping gaia-ui" in log
    assert (ep.state / "signal").read_text() == "SIGTERM"


@needs_linux_node
def test_forwarder_waits_while_the_first_start_installs_the_backend(entrypoint):
    ep = entrypoint(STUB_LISTEN_DELAY_MS="3000")
    _wait_for(lambda: _listen_address(ep.port) == "0.0.0.0", "the forwarder to listen")
    with pytest.raises(OSError):  # accepted, then closed: no backend yet
        _get(f"http://127.0.0.1:{ep.port}/")
    ep.wait_listening()
    assert _get(f"http://{_non_loopback_ipv4()}:{ep.port}/") == (200, "stub gaia /")
    assert ep.proc.poll() is None


@needs_linux_node
def test_serve_only_mode_runs_gaia_ui_directly_on_the_service_port(entrypoint):
    ep = entrypoint(GAIA_UI_SERVE_ONLY="true")
    _wait_for(lambda: (ep.state / "listening").exists(), "the stub gaia-ui to listen")
    assert ep.argv() == ["--serve", "--port", str(ep.port), "--no-open"]
    assert int((ep.state / "listening").read_text()) == ep.proc.pid  # exec, no forwarder
    host = _non_loopback_ipv4()
    _wait_for(lambda: not _refused(host, ep.port), "the serve-only listener")
    assert _get(f"http://{host}:{ep.port}/") == (200, "stub gaia /")


@needs_linux_node
@pytest.mark.parametrize("bin_dir", [".gaia/bin", ".local/bin", ".cargo/bin"])
def test_refuses_to_start_when_ngrok_could_enable_the_tunnel(entrypoint, tmp_path, bin_dir):
    """gaia-ui puts these HOME dirs first on the backend's PATH."""
    ngrok_dir = tmp_path / "home" / bin_dir
    ngrok_dir.mkdir(parents=True)
    (ngrok_dir / "ngrok").write_text("#!/bin/sh\n")
    (ngrok_dir / "ngrok").chmod(0o755)
    ep = entrypoint()
    assert ep.wait_exit() == 1
    assert "refusing to start" in ep.log.read_text()
    assert not (ep.state / "argv.json").exists()


def test_image_and_compose_wire_the_forwarder_without_tunnel_support():
    dockerfile = (RECIPE / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY ods-gaia-forward.cjs /usr/local/bin/ods-gaia-forward\n" in dockerfile
    assert re.search(r"chmod 0755 [^\n]*/usr/local/bin/ods-gaia-forward", dockerfile)
    assert 'ENTRYPOINT ["ods-gaia-entrypoint"]' in dockerfile
    service = yaml.safe_load((RECIPE / "compose.yaml").read_text(encoding="utf-8"))["services"]["gaia"]
    manifest = yaml.safe_load((RECIPE / "manifest.yaml").read_text(encoding="utf-8"))["service"]
    assert manifest["port"] == 4200
    assert service["ports"] == ["${BIND_ADDRESS:-127.0.0.1}:${GAIA_PORT:-7822}:4200"]
    assert "http://127.0.0.1:4200/" in service["healthcheck"]["test"][-1]  # through the forwarder
    recipe_text = "\n".join((RECIPE / name).read_text(encoding="utf-8").lower()
                            for name in ("Dockerfile", "compose.yaml", "manifest.yaml"))
    assert "ngrok" not in recipe_text and "tunnel" not in recipe_text
