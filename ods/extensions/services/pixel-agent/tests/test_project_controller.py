import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch

HOST = Path(__file__).resolve().parents[1] / "host"
sys.path.insert(0, str(HOST))
from project_controller import ProjectController


@unittest.skipUnless(os.name == "posix", "POSIX candidate coordinator")
class ProjectControllerPolicyTests(unittest.TestCase):
    def test_denied_request_does_not_read_source_or_execute(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / "state", "sha256:" + "a" * 64,
                                           authorize=lambda *args: False)
            try:
                with patch("project_controller.snapshot_project") as snapshot, patch("project_controller.subprocess.run") as command:
                    with self.assertRaises(PermissionError):
                        controller.submit("a" * 64, "project")
                    snapshot.assert_not_called()
                    command.assert_not_called()
            finally:
                controller.close()

    def test_no_default_authorization_adapter(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                ProjectController(root, Path(root) / "state", "sha256:" + "a" * 64, authorize=None)

    def test_controller_exception_is_unconfirmed_and_not_reclaimed(self):
        with tempfile.TemporaryDirectory() as root:
            controller = ProjectController(root, Path(root) / "state", "sha256:" + "a" * 64,
                                           authorize=lambda *args: True)
            request = {"project": "project", "sourceSha256": "b" * 64,
                       "image": controller.image, "outputDirectory": "out"}
            job, _ = controller.jobs.create("a" * 64, request)
            try:
                with patch("project_controller.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)):
                    controller._work(job, request, {}, threading.Event())
                self.assertEqual(controller.observe(job)["state"], "unconfirmed")
                self.assertFalse(controller.jobs.claim(job))
            finally:
                controller.close()

    def test_revocation_or_cancellation_during_collection_prevents_import(self):
        for change in ("revoke", "cancel"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                allowed = [True]
                cancel = threading.Event()
                controller = ProjectController(root, Path(root) / "state", "sha256:" + "a" * 64,
                                               authorize=lambda *args: allowed[0])
                request = {"project": "project", "sourceSha256": "b" * 64,
                           "image": controller.image, "outputDirectory": "out"}
                job, _ = controller.jobs.create("a" * 64, request)
                def collect(*args):
                    if change == "revoke":
                        allowed[0] = False
                    else:
                        cancel.set()
                    return {}
                try:
                    with ExitStack() as mocks:
                        mocks.enter_context(patch("project_controller.subprocess.run", side_effect=[
                            subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]))
                        mocks.enter_context(patch("project_controller.seed_project"))
                        mocks.enter_context(patch("project_controller.run_stage", return_value={"status": "succeeded", "exitCode": 0}))
                        mocks.enter_context(patch("project_controller.collect_artifacts", side_effect=collect))
                        imported = mocks.enter_context(patch("project_controller.import_artifacts"))
                        mocks.enter_context(patch.object(controller, "_cleanup", return_value=[]))
                        controller._work(job, request, {}, cancel)
                        imported.assert_not_called()
                    result = controller.jobs.observe(job)
                    self.assertEqual(result["state"], "unconfirmed" if change == "revoke" else "cancelled")
                    self.assertFalse(controller.jobs.claim(job))
                finally:
                    controller.close()


@unittest.skipUnless(os.environ.get("ODS_TEST_PROJECT_NODE") == "1", "real Docker opt-in")
class ProjectControllerRuntimeTests(unittest.TestCase):
    def test_complete_pipeline_and_replay_after_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            iid = root / "image"
            subprocess.run(["docker", "build", "--iidfile", str(iid), "-f",
                            str(HOST / "Dockerfile.project-node"), str(HOST)],
                           check=True, capture_output=True, timeout=180)
            image = iid.read_text().strip()
            project = root / "project"
            project.mkdir()
            package = {"private": True, "scripts": {"test": "node check.cjs", "build": "node build.cjs"}}
            (project / "package.json").write_text(json.dumps(package))
            (project / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": package}}))
            (project / "check.cjs").write_text("require('assert').notEqual(process.getuid(),0)")
            (project / "build.cjs").write_text("const f=require('fs');f.mkdirSync('out');f.writeFileSync('out/index.html','<h1>Controller built</h1>')")
            # Test-only explicit scope; no production policy or receipt is changed.
            def authorized(selected, action, binding):
                return selected == "project" and action in {"snapshot", "execute", "import", "observe"}
            controller = ProjectController(root, root / "state", image, authorize=authorized)
            try:
                accepted = controller.submit("a" * 64, "project")
            finally:
                controller.close()
            result = controller.observe(accepted["id"])
            self.assertEqual(result["state"], "succeeded", result)
            self.assertNotIn("cleanupWarnings", result["output"])
            self.assertEqual([s["stage"] for s in result["steps"]], ["acquire", "test", "build"])
            destination = root / result["output"]["relativeDirectory"] / "index.html"
            self.assertEqual(destination.read_bytes(), b"<h1>Controller built</h1>")
            self.assertFalse((project / "out").exists())
            reopened = ProjectController(root, root / "state", image, authorize=authorized)
            try:
                replay = reopened.submit("a" * 64, "project")
                self.assertEqual(replay["id"], result["id"])
                self.assertEqual(replay["state"], "succeeded")
                self.assertFalse(reopened.futures)
            finally:
                reopened.close()
