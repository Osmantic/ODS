"""Actual Background launchd bridge, proxy, respawn, and bystander checks."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from macos_bridge_service import BridgeService, NativeServiceError  # noqa: E402


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data)


@unittest.skipUnless(sys.platform == "darwin", "actual Darwin launchd and libproc")
class BridgeLiveTests(unittest.TestCase):
    def test_background_proxy_respawn_and_unrelated_python(self):
        root = Path(tempfile.mkdtemp(prefix="ods-bridge-proof-")).resolve()
        install = root / "Install With Spaces"
        (install / "bin").mkdir(parents=True)
        shutil.copy2(ROOT / "bin/ods-macos-llm-bridge.py", install / "bin/ods-macos-llm-bridge.py")
        service = BridgeService(install, "/usr/bin/python3", home=root / "home",
                                label="com.ods.fleet-bridge-probe." + uuid.uuid4().hex)
        service.domains = lambda: [f"user/{os.getuid()}"]
        target = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
        target.daemon_threads = True
        threading.Thread(target=target.serve_forever, daemon=True).start()
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            port = port_probe.getsockname()[1]
        arguments = [str(service.script), "--listen-host", "127.0.0.1", "--listen-port", str(port),
                     "--target-port", str(target.server_address[1]), "--allow-peer", "127.0.0.1"]
        bystander = subprocess.Popen(["/usr/bin/python3", "-I", "-c",
                                      "import time; print('ready',flush=True); time.sleep(120)"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        evidence = {"scope": "isolated Background launchd jobs; GUI remains logged in"}
        try:
            self.assertEqual(bystander.stdout.readline(), b"ready\n")
            first = service.configure(arguments)
            self.assertEqual(first["domain"], f"user/{os.getuid()}")
            self.assertEqual(first["identity"][-1], service._probe_kernel_exe(service.executable))
            evidence["interpreter_kernel_path_differs"] = first["identity"][-1] != str(service.executable)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                client.sendall(b"actual-bridge-proxy-proof")
                self.assertEqual(client.recv(4096), b"actual-bridge-proxy-proof")
            evidence["background_proxy"] = True
            snapshots = {path: path.read_bytes() for path in (service.plist, service.journal, service.pid_file)}
            self.assertEqual(service.status()["identity"], first["identity"])
            self.assertEqual(snapshots, {path: path.read_bytes() for path in snapshots})
            evidence["status_read_only"] = True
            self.assertTrue(service.live(first["identity"]))
            os.kill(first["identity"][0], signal.SIGTERM)
            deadline = time.monotonic() + 20
            respawn = None
            while time.monotonic() < deadline:
                job = service.job(first["domain"])
                if job and job.get("pid") and job["pid"] != str(first["identity"][0]):
                    respawn = service.capture(first["domain"], token=first["token"])
                    if respawn is not None:
                        break
                time.sleep(.2)
            self.assertIsNotNone(respawn)
            self.assertNotEqual(respawn, first["identity"])
            service.stop()
            self.assertFalse(service.live(respawn))
            evidence["keepalive_respawn_stopped"] = True
            second = service.configure(arguments)
            with self.assertRaises(NativeServiceError):
                service.rollback(first)
            self.assertTrue(service.live(second["identity"]))
            evidence["old_receipt_spares_replacement"] = True
            service.stop()
            self.assertIsNone(bystander.poll())
            evidence["unrelated_python_spared"] = True
            # A different listener must not satisfy readiness while our Python
            # process is alive but has not yet attempted to bind its socket.
            script = service.script.read_text()
            script = script.replace("from __future__ import annotations",
                                    "from __future__ import annotations\nimport time; time.sleep(2)")
            service.script.write_text(script)
            with socket.socket() as occupied:
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                occupied.bind(("127.0.0.1", port))
                occupied.listen(2)
                with self.assertRaises(NativeServiceError):
                    service.configure(arguments, timeout=5)
            evidence["foreign_listener_not_readiness"] = True
        finally:
            try:
                service.stop()
                self.assertFalse(service.jobs())
                self.assertFalse(service.processes())
                evidence["fixture_cleanup"] = True
            finally:
                bystander.terminate()
                bystander.wait(timeout=10)
                bystander.stdout.close()
                bystander.stderr.close()
                target.shutdown()
                target.server_close()
            if evidence.get("fixture_cleanup"):
                shutil.rmtree(root)
            print(json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
