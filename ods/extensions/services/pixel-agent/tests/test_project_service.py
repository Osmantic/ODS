import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_controller import ProjectController
from project_service import serve


class ProjectServiceLockTests(unittest.TestCase):
    def test_slow_capability_probe_does_not_delay_ready_or_cached_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = 'sha256:' + 'a' * 64
            controller = ProjectController(root, root / 'state', image, authorize=lambda *_: True,
                                           python_image=image)
            stop, ready, probing = threading.Event(), threading.Event(), threading.Event()
            errors = []
            def probe(_image, *, cancel):
                probing.set()
                if not cancel.wait(5):
                    raise TimeoutError('test shutdown did not stop probe')
                raise InterruptedError('service stopped')
            def run():
                try:
                    serve(controller, stop, ready=ready)
                except Exception as error:
                    errors.append(error)
            with patch('project_service.verify_runtime'), patch('project_controller.probe_python_runtime', side_effect=probe):
                thread = threading.Thread(target=run)
                thread.start()
                try:
                    self.assertTrue(probing.wait(1))
                    self.assertTrue(ready.wait(1))
                    self.assertEqual(controller.capabilities('python')['status'], 'unavailable')
                    job, _ = controller.jobs.create('a' * 64, {'project': 'project', 'sourceSha256': 'b' * 64,
                                                            'image': image, 'outputDirectory': 'out'})
                    self.assertEqual(controller.observe(job)['state'], 'queued')
                    controller.cancel(job)
                finally:
                    stop.set()
                    thread.join(3)
                    controller.close()
            self.assertFalse(thread.is_alive())
            self.assertFalse(controller._capability_thread.is_alive())
            self.assertEqual(errors, [])

    def test_replacement_cannot_recover_jobs_while_previous_worker_drains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = "sha256:" + "a" * 64
            controller = ProjectController(root, root / "state", image, authorize=lambda *_: True)
            replacement = ProjectController(root, root / "state", image, authorize=lambda *_: True)
            stop, ready, draining, release = [threading.Event() for _ in range(4)]
            errors = []
            original_close = controller.close

            def drain():
                draining.set()
                if not release.wait(10):
                    raise TimeoutError("test did not release worker drain")
                original_close()

            def run():
                try:
                    serve(controller, stop, ready=ready)
                except Exception as error:
                    errors.append(error)

            with patch("project_service.verify_runtime"), patch.object(controller, "close", side_effect=drain):
                thread = threading.Thread(target=run)
                thread.start()
                try:
                    self.assertTrue(ready.wait(5), errors)
                    job, _ = controller.jobs.create("b" * 64, {
                        "project": "project", "sourceSha256": "c" * 64,
                        "image": image, "outputDirectory": "out",
                    })
                    controller.jobs.claim(job)
                    stop.set()
                    self.assertTrue(draining.wait(5), errors)
                    with self.assertRaises(BlockingIOError):
                        serve(replacement, threading.Event())
                    self.assertEqual(replacement.observe(job)["state"], "running")
                finally:
                    stop.set()
                    release.set()
                    thread.join(10)
                    replacement.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])


@unittest.skipUnless(os.environ.get("ODS_TEST_PROJECT_NODE") == "1", "real Docker opt-in")
class ProjectServiceTests(unittest.TestCase):
    def test_node_tool_runs_complete_job_through_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = Path(__file__).resolve().parents[1] / "host"
            iid = root / "image"
            subprocess.run(["docker", "build", "--iidfile", str(iid), "-f", str(host / "Dockerfile.project-node"), str(host)],
                           check=True, capture_output=True, timeout=180)
            project = root / "project"
            project.mkdir()
            package = {"private": True, "scripts": {"test": "node check.cjs", "build": "node build.cjs"}}
            (project / "package.json").write_text(json.dumps(package))
            (project / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": package}}))
            (project / "check.cjs").write_text("require('assert').notEqual(process.getuid(),0)")
            (project / "build.cjs").write_text("const f=require('fs');f.mkdirSync('out');f.writeFileSync('out/index.html','<h1>Tool built</h1>')")
            # Explicit test scope; no installed policy or owner grant is changed.
            controller = ProjectController(root, root / "state", iid.read_text().strip(),
                                           authorize=lambda project, *_: project in {"project", "interrupted-project"})
            # Startup retains uncertain work, but an independent project's
            # service/Node integration can proceed without bypassing its fence.
            interrupted, _ = controller.jobs.create("a" * 64, {
                "project": "interrupted-project", "sourceSha256": "b" * 64,
                "image": controller.image, "outputDirectory": "out",
            })
            controller.jobs.claim(interrupted)
            stop, ready = threading.Event(), threading.Event()
            errors = []
            def server():
                try:
                    serve(controller, stop, ready=ready)
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=server)
            thread.start()
            try:
                self.assertTrue(ready.wait(10), errors)
                self.assertEqual(controller.observe(interrupted)["state"], "unconfirmed")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(5)
                    probe.connect(str(root / "state" / "control.sock"))
                    probe.sendall(b'{"schemaVersion":1,"action":"health"}\n')
                    with probe.makefile('rb') as stream:
                        health = json.loads(stream.readline(8192))
                self.assertEqual(health['status'], 'ready')
                self.assertEqual(health['image'], controller.image)
                self.assertEqual(health['executionPolicy'], 'runtime-verified-full-access')
                plugin = host.parent / "plugin"
                script = "\n".join([
                    "import {createProjectBuildTool} from " + json.dumps((plugin / "project-build.mjs").as_uri()) + ";",
                    "import {createProjectTransport} from " + json.dumps((plugin / "project-transport.mjs").as_uri()) + ";",
                    "const tool=createProjectBuildTool({request:createProjectTransport({socketPath:"
                    + json.dumps(str(root / "state" / "control.sock")) + ",sessionId:'fixture-session'})});",
                    "let r=(await tool.execute('submit-1',{action:'submit',project:'project',outputDirectory:'out'})).details;",
                    "for(let n=0;n<150 && ['queued','running'].includes(r.status);n++){",
                    "await new Promise(resolve=>setTimeout(resolve,200));",
                    "r=(await tool.execute('observe-'+n,{action:'observe',jobId:r.jobId})).details;}",
                    "console.log(JSON.stringify(r)); if(r.status!=='succeeded')process.exitCode=1;",
                ])
                result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual((root / receipt["output"]["relativeDirectory"] / "index.html").read_text(), "<h1>Tool built</h1>")
                self.assertFalse((project / "out").exists())
                self.assertEqual(controller.observe(interrupted)['state'], 'unconfirmed')
            finally:
                stop.set()
                thread.join(15)
                controller.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertFalse((root / "state" / "control.sock").exists())
