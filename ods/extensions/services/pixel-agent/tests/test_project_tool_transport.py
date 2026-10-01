"""Real Node tool -> UNIX socket -> Python dispatcher/controller boundary."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_transport import serve_project_connection
from project_controller import ProjectController


@unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "POSIX socket transport")
class ProjectToolTransportTests(unittest.TestCase):
    def test_real_tool_receives_controller_permission_denial(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / "state", "sha256:" + "a" * 64,
                                           authorize=lambda *args: False)
            path = str(Path(root) / "control.sock")
            errors = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(1)
                listener.settimeout(10)
                def serve():
                    try:
                        connection, _ = listener.accept()
                        with connection:
                            serve_project_connection(connection, controller=controller, owner_uid=os.getuid())
                    except Exception as error:
                        errors.append(error)
                worker = threading.Thread(target=serve)
                worker.start()
                plugin = Path(__file__).resolve().parents[1] / "plugin"
                script = "\n".join([
                    "import {createProjectBuildTool} from " + json.dumps((plugin / "project-build.mjs").as_uri()) + ";",
                    "import {createProjectTransport} from " + json.dumps((plugin / "project-transport.mjs").as_uri()) + ";",
                    "const tool=createProjectBuildTool({request:createProjectTransport({socketPath:"
                    + json.dumps(path) + ",sessionId:'test-session'})});",
                    "const result=await tool.execute('call-1',{action:'submit',project:'missing',outputDirectory:'out'});",
                    "console.log(JSON.stringify(result.details));",
                ])
                try:
                    result = subprocess.run(["node", "--input-type=module", "-e", script],
                                            capture_output=True, text=True, timeout=15)
                    worker.join(10)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["status"], "denied")
                    self.assertFalse(controller.futures)
                finally:
                    controller.close()
