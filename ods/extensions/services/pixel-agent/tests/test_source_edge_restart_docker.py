"""Opt-in Linux Docker qualification; never starts a local Docker daemon.

The real production image/Compose/volume/gate run here. A private UDS health
fixture represents already healthy host ingress; native mode/source custody
proofs have separate adversarial tests in test_source_edge_restart.py.
Run only on an isolated runner with ODS_TEST_SOURCE_EDGE_DOCKER=1, as root.
"""
import copy
import http.server
import os
from pathlib import Path
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
import pixel_access_bridge as bridge

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("ODS_TEST_SOURCE_EDGE_DOCKER") != "1",
    reason="isolated Linux Docker qualification is opt-in",
)


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=180, **kwargs).stdout.strip()


def test_real_stopped_edge_restarts_with_same_gate_and_never_reopens_a_hold(tmp_path):
    assert os.geteuid() == 0, "The real coordinator copies private Docker state as root"
    # Refuse an occupied fixed name before creating anything: this test must
    # never stop or remove a pre-existing user's Edge.
    assert not run(["docker", "ps", "-aq", "--filter", "name=^/ods-pixel-edge$"])
    install = tmp_path / "ods"
    install.mkdir()
    service_dir = install / "extensions/services/pixel-edge"
    shutil.copytree(ROOT / "extensions/services/pixel-edge", service_dir)
    runtime = install / "fixture-runtime"
    runtime.mkdir(mode=0o755)
    preview = install / "fixture-preview"
    preview.mkdir(mode=0o755)
    project = "ods_source_edge_test_" + secrets.token_hex(6)
    key = secrets.token_hex(32)
    client_key = secrets.token_hex(32)
    fragment = yaml.safe_load((service_dir / "compose.yaml.disabled").read_text())
    edge = copy.deepcopy(fragment["services"]["pixel-edge"])
    edge["environment"] = {
        "PIXEL_OPENWEBUI_KEY": client_key, "PIXEL_PREVIEW_PROXY_KEY": key,
        "PIXEL_EDGE_PORT_INTERNAL": "9595", "PIXEL_INGRESS_SOCKET": "/pixel-runtime/pixel-ingress.sock",
        "PIXEL_PREVIEW_SOCKET": "/pixel-preview-runtime/http.sock",
        "PIXEL_TRANSITION_STATE_DIR": "/pixel-transition-state",
    }
    edge["group_add"] = ["0"]
    edge["volumes"][0]["source"] = str(runtime)
    edge["volumes"][1]["source"] = str(preview)
    for mount in edge["volumes"][:2]:
        mount["bind"]["propagation"] = "rprivate"
    base = install / "docker-compose.base.yml"
    base.write_text("services: {}\nnetworks:\n  default: {}\n")
    compose_file = service_dir / "compose.yaml"
    compose_file.write_text(yaml.safe_dump({"services": {"pixel-edge": edge},
                                          "volumes": fragment["volumes"]}))
    compose = ["docker", "compose", "--project-name", project,
               "--project-directory", str(install), "-f", str(base), "-f", str(compose_file)]

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/health"
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = socketserver.UnixStreamServer(str(runtime / "pixel-ingress.sock"), Health)
    os.chmod(runtime / "pixel-ingress.sock", 0o666)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run(compose + ["up", "--detach", "--build", "--wait", "--wait-timeout", "90", "pixel-edge"])
        adapter = bridge.SystemdAccessBridge(str(install), key)
        # Only host custody is simulated. Docker metadata, private offline
        # gate inspection, start and authenticated exec transport are real.
        adapter._source_edge_restart_proof = Mock(return_value="a" * 64)
        original_id = run(["docker", "inspect", "ods-pixel-edge", "--format", "{{.Id}}"])
        initial = adapter.edge()
        assert initial["phase"] == "idle"
        run(["docker", "stop", original_id])
        stopped = adapter._source_edge_container()
        assert stopped["State"]["ExitCode"] == 0
        offline = adapter._source_edge_idle_state(original_id)
        assert offline["revision"] == initial["revision"]
        commands = []
        original_command = adapter.command
        def command(args, **kwargs):
            commands.append(args)
            return original_command(args, **kwargs)
        adapter.command = command
        adapter.restart_stopped_source_edge()
        assert [args for args in commands if args[:2] == ["docker", "start"]] == [
            ["docker", "start", original_id]]
        assert adapter._source_edge_container()["Id"] == original_id
        assert adapter.edge()["revision"] == initial["revision"]
        assert adapter._source_edge_restart_proof.call_count == 2

        # A retained hold must survive, even though the same container has a
        # clean exit code and its already healthy host still proves its mode.
        held = adapter.edge("acquire", "e" * 64, initial["revision"])
        assert held["phase"] == "held"
        run(["docker", "stop", original_id])
        commands.clear()
        with pytest.raises(bridge.AccessError, match="source-edge-admission-unverified"):
            adapter.restart_stopped_source_edge()
        assert not [args for args in commands if args[:2] == ["docker", "start"]]
        current = adapter._source_edge_container()
        assert current["State"]["Running"] is False
        assert current["Id"] == original_id
    finally:
        # The project name is freshly generated above. Compose removes only
        # this test's named objects; it never prunes the daemon.
        run(compose + ["down", "--volumes", "--remove-orphans"])
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
