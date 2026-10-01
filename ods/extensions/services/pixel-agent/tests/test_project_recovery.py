"""Crash recovery uses a private ledger and an identity-checking Docker boundary."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_controller import ProjectController
from project_runtime import deadline_command
from project_storage import volume_options, DEFAULT_JOB_BYTES


@unittest.skipUnless(os.name == "posix", "private POSIX controller state")
class ProjectRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ProjectController(self.temp.name, Path(self.temp.name) / "state",
                                            "sha256:" + "a" * 64, authorize=lambda *_: True)
        self.addCleanup(self.controller.close)
        cleanup = patch.object(self.controller, '_cleanup', return_value=[])
        cleanup.start()
        self.addCleanup(cleanup.stop)
        request = {"project": "project", "sourceSha256": "b" * 64,
                   "image": self.controller.image, "outputDirectory": "out"}
        self.job = self.controller.jobs.create("c" * 64, request)[0]
        self.controller.jobs.claim(self.job)
        self.controller.jobs.recover_interrupted()
        self.commands = []
        self.container = {
            "Id": "d" * 64, "Image": self.controller.image, "Name": "/" + self.job + "-acquire",
            "Config": {"Image": self.controller.image,
                       "Cmd": deadline_command(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund",
                               "--registry=https://registry.npmjs.org"]),
                       "User": "1000:1000", "Labels": {"org.osmantic.ods.project-job": self.job}},
            "HostConfig": {"NetworkMode": "bridge", "ReadonlyRootfs": True, "Privileged": False,
                           "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                           "Memory": 2 * 1024 ** 3, "MemorySwap": 2 * 1024 ** 3,
                           "NanoCpus": 2 * 10 ** 9, "PidsLimit": 256},
            "Mounts": [{"Type": "volume", "Name": self.job, "Destination": "/home/node"}],
            "State": {"Running": True, "Status": "running", "StartedAt": "2026-01-01", "ExitCode": 0},
        }

    def docker(self, args, **kwargs):
        self.commands.append(args)
        if args[:3] == ['docker', 'volume', 'inspect']:
            value = {'Name': self.job, 'Driver': 'local', 'Scope': 'local',
                     'Options': volume_options(DEFAULT_JOB_BYTES),
                     'Labels': {'org.osmantic.ods.project-job': self.job,
                                'org.osmantic.ods.project-storage': 'tmpfs-v1'}}
            return subprocess.CompletedProcess(args, 0, json.dumps([value]).encode())
        if args[:3] == ["docker", "container", "ls"]:
            return subprocess.CompletedProcess(args, 0, (self.container["Id"] + "\n").encode())
        if args[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(args, 0, json.dumps([self.container]).encode())
        if args[:2] == ["docker", "stop"]:
            self.assertEqual(args[-1], self.container["Id"], "never signal a mutable name")
            self.container["State"].update(Running=False, Status="exited", ExitCode=137)
            return subprocess.CompletedProcess(args, 0, b"")
        self.fail("unexpected command: " + repr(args))

    def cancel(self, docker=None):
        with patch("project_runtime.subprocess.run", side_effect=docker or self.docker):
            self.controller.cancel(self.job)
            self.controller.close()  # wait only this private recovery worker
        return self.controller.jobs.observe(self.job)

    def test_restarted_cancel_stops_verified_orphan_by_id_without_replay_or_import(self):
        result = self.cancel()
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(self.container["State"]["Running"])
        self.assertTrue(result["output"]["artifactImportUnconfirmed"])
        self.assertEqual(result["steps"], [])
        self.assertFalse(self.controller.jobs.claim(self.job))

    def test_unconfirmed_recorded_stage_is_the_stage_recovery_must_confirm(self):
        # A CLI timeout records the current stage, not an unseen next stage.
        with self.controller.jobs._connect() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (self.job,))
        self.controller.jobs.record_stage(self.job, 'acquire', {'status': 'unconfirmed', 'exitCode': None})
        self.assertEqual(self.cancel()['state'], 'cancelled')

    def test_recovery_cleanup_uses_original_verified_image_after_service_upgrade(self):
        original = self.controller.image
        self.controller.image = 'sha256:' + 'e' * 64
        self.assertEqual(self.cancel()['state'], 'cancelled')
        self.controller._cleanup.assert_called_once_with(self.job, image=original, runtime='npm')

    def test_restarted_reconcile_reports_runtime_without_resuming_execution(self):
        with patch("project_runtime.subprocess.run", side_effect=self.docker):
            result = self.controller.jobs.reconcile(self.job)
        self.assertIsNotNone(result["runtime"])
        self.assertEqual(result["runtime"]["status"], "running")
        self.assertEqual(result["job"]["state"], "unconfirmed")
        self.assertFalse(any(args[1] == "stop" for args in self.commands))

    def test_foreign_container_is_never_stopped(self):
        self.container["Config"]["Cmd"] = ["node", "foreign.js"]
        result = self.cancel()
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(result["state"], "unconfirmed")
        self.assertFalse(any(args[1] == "stop" for args in self.commands))

    def test_failed_stop_remains_unconfirmed_even_if_cli_succeeds(self):
        def docker(args, **kwargs):
            if args[:2] == ["docker", "stop"]:
                return subprocess.CompletedProcess(args, 0, b"")
            return self.docker(args, **kwargs)
        result = self.cancel(docker)
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(result["state"], "unconfirmed")

    def test_revoked_owner_cannot_cancel_or_inspect_docker(self):
        self.controller.authorize = lambda *_: False
        with patch("project_runtime.subprocess.run") as command:
            with self.assertRaises(PermissionError):
                self.controller.cancel(self.job)
            command.assert_not_called()

    def test_missing_or_unavailable_runtime_is_not_confirmed_cancel(self):
        for outcome in (subprocess.CompletedProcess([], 0, b""),
                        subprocess.CompletedProcess([], 1, b"")):
            with self.subTest(returncode=outcome.returncode):
                from project_runtime import recover_job
                with patch("project_runtime.subprocess.run", return_value=outcome) as command:
                    result = recover_job(self.controller.image, self.job, cancel=True)
                self.assertEqual(result["status"], "unconfirmed")
                self.assertFalse(any(call.args[0][1] == "stop" for call in command.call_args_list))

    def test_unresponsive_docker_obeys_one_total_deadline(self):
        from project_runtime import recover_job
        fake = Path(self.temp.name) / "docker"
        fake.write_text("#!" + sys.executable + "\nimport time\ntime.sleep(20)\n")
        fake.chmod(0o700)
        start = time.monotonic()
        with patch.dict(os.environ, {"PATH": self.temp.name + os.pathsep + os.environ["PATH"]}):
            result = recover_job(self.controller.image, self.job, cancel=True, timeout=0.15)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertLess(time.monotonic() - start, 1.5)

    def test_recovery_deadline_is_shared_between_commands(self):
        from project_runtime import recover_job
        budgets = []
        def docker(args, **kwargs):
            budgets.append(kwargs["timeout"])
            return self.docker(args, **kwargs)
        with patch("project_runtime.time.monotonic", side_effect=[100, 101, 103, 106]), \
                patch("project_runtime.subprocess.run", side_effect=docker):
            result = recover_job(self.controller.image, self.job, timeout=5)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(budgets, [4, 2])

    def test_revocation_while_queued_records_reason_without_docker_or_false_ack(self):
        release = threading.Event()
        self.controller.pool.submit(release.wait)
        try:
            self.controller.cancel(self.job)
            self.controller.authorize = lambda *_: False
            with patch("project_runtime.subprocess.run") as command:
                release.set()
                self.controller.close()
                command.assert_not_called()
            row = self.controller.jobs.observe(self.job)
            self.assertTrue(row["cancel_requested"])
            self.assertEqual(row["state"], "unconfirmed")
            self.assertEqual(row["output"]["runtimeRecovery"]["evidence"], "authorization-denied")
            self.assertIsInstance(self.controller.futures[self.job].exception(), PermissionError)
        finally:
            release.set()

    def test_exited_orphan_is_not_replayed_or_promoted_to_success(self):
        self.container["State"].update(Running=False, Status="exited")
        result = self.cancel()
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["steps"], [])
        self.assertFalse(any(args[1] == "stop" for args in self.commands))

    def test_previous_stage_does_not_confirm_cancellation_of_unseen_next_stage(self):
        from project_runtime import recover_job
        with patch("project_runtime.subprocess.run", side_effect=self.docker):
            result = recover_job(self.controller.image, self.job, cancel=True, required_stage="test")
        self.assertFalse(self.container["State"]["Running"])
        self.assertEqual(result["status"], "unconfirmed")

    def test_seed_orphan_is_verified_and_stopped(self):
        for stage in ("seed-manifests", "seed-source"):
            with self.subTest(stage=stage):
                from project_runtime import recover_job
                self.container["Name"] = "/" + self.job + "-" + stage
                self.container["Config"]["Cmd"] = deadline_command(["tar", "-xf", "-", "--no-same-owner"], 60)
                self.container["HostConfig"]["NetworkMode"] = "none"
                self.container["State"].update(Running=True, Status="running")
                with patch("project_runtime.subprocess.run", side_effect=self.docker):
                    result = recover_job(self.controller.image, self.job, cancel=True)
                self.assertEqual(result["status"], "cancelled")

    def python_job(self):
        from project_runtime import stage_arguments
        image = "sha256:" + "e" * 64
        # setUp already contains an uncertain npm job. The Python recovery
        # fixture is independent; a runtime switch must not bypass its fence.
        request = {"project": "python-project", "sourceSha256": "b" * 64, "image": image,
                   "outputDirectory": "out", "runtime": "python"}
        self.job = self.controller.jobs.create("e" * 64, request)[0]
        self.controller.jobs.claim(self.job)
        self.controller.jobs.recover_interrupted()
        args = stage_arguments(image, self.job, "acquire", runtime="python")
        self.container.update(Image=image, Name="/" + self.job + "-acquire")
        self.container["Config"].update(Image=image, Cmd=args[args.index(image) + 1:],
                                        Labels={"org.osmantic.ods.project-job": self.job})
        self.container["Mounts"][0]["Name"] = self.job

    def test_python_restarted_cancel_uses_persisted_profile_and_image(self):
        self.python_job()
        result = self.cancel()
        self.assertEqual(result["state"], "cancelled", result)
        self.assertFalse(self.container["State"]["Running"])
        self.assertEqual(result["request"]["runtime"], "python")

    def test_python_restarted_reconcile_uses_persisted_profile(self):
        self.python_job()
        with patch("project_runtime.subprocess.run", side_effect=self.docker):
            result = self.controller.jobs.reconcile(self.job)
        self.assertEqual(result["runtime"]["status"], "running", result)
        self.assertEqual(result["job"]["state"], "unconfirmed")

    def test_python_same_image_but_npm_command_is_foreign(self):
        self.python_job()
        from project_runtime import stage_arguments
        image = self.container["Image"]
        args = stage_arguments(image, self.job, "acquire")
        self.container["Config"]["Cmd"] = args[args.index(image) + 1:]
        result = self.cancel()
        self.assertEqual(result["state"], "unconfirmed")
        self.assertTrue(self.container["State"]["Running"])
        self.assertFalse(any(args[1] == "stop" for args in self.commands))


@unittest.skipUnless(os.environ.get("ODS_TEST_PROJECT_NODE") == "1", "disposable Docker opt-in")
class ProjectCrashRuntimeTests(unittest.TestCase):
    runtime = "npm"

    def test_hard_killed_controller_leaves_orphan_that_replacement_can_cancel(self):
        host = Path(__file__).resolve().parents[1] / "host"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iid = root / "image"
            subprocess.run(["docker", "build", "--iidfile", str(iid), "-f",
                            str(host / ("Dockerfile.project-python" if self.runtime == "python"
                                        else "Dockerfile.project-node")), str(host)],
                           check=True, capture_output=True, timeout=180)
            image = iid.read_text().strip()
            project = root / "project"
            if self.runtime == "python":
                from test_project_python import fixture
                fixture(root, test="import unittest, time\nclass T(unittest.TestCase):\n def test_wait(self): time.sleep(120)\n")
            else:
                project.mkdir()
                package = {"private": True, "scripts": {"test": "node wait.cjs", "build": "node --version"}}
                (project / "package.json").write_text(json.dumps(package))
                (project / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": package}}))
                (project / "wait.cjs").write_text("setInterval(()=>{},1000);")
            original = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            # The only killed host process is this fixture's own child PID.
            script = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from project_controller import ProjectController
c = ProjectController(sys.argv[2], sys.argv[2] + '/state', sys.argv[3], authorize=lambda *_: True,
                      python_image=sys.argv[3] if sys.argv[4] == 'python' else None)
r = c.submit('a' * 64, 'project')
print(r['id'], flush=True)
time.sleep(120)
"""
            child = subprocess.Popen([sys.executable, "-c", script, str(host), str(root), image, self.runtime],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            job = None
            controller = None
            try:
                # Child startup is local-only; its controller socket is not installed.
                import select
                ready, _, _ = select.select([child.stdout], [], [], 20)
                self.assertTrue(ready, "private controller failed to start")
                job = child.stdout.readline().strip()
                self.assertRegex(job, r"^ods-project-[a-f0-9]{24}$")
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    read = subprocess.run(["docker", "inspect", job + "-test"], capture_output=True, timeout=10)
                    if read.returncode == 0 and json.loads(read.stdout)[0]["State"]["Running"]:
                        break
                    time.sleep(0.2)
                else:
                    self.fail("fixture test stage did not start")
                child.kill()
                child.communicate(timeout=10)
                controller = ProjectController(root, root / "state", image, authorize=lambda *_: True,
                                               python_image=image if self.runtime == "python" else None)
                controller.jobs.recover_interrupted()
                self.assertEqual(controller.jobs.reconcile(job)["runtime"]["status"], "running")
                controller.cancel(job)
                controller.close()
                row = controller.jobs.observe(job)
                self.assertEqual(row["state"], "cancelled", row)
                self.assertTrue(row["output"]["artifactImportUnconfirmed"])
                self.assertNotEqual(subprocess.run(["docker", "inspect", job + "-test"], capture_output=True).returncode, 0)
                self.assertEqual(controller.storage._read(), {})
                self.assertFalse(controller.jobs.claim(job))
                self.assertFalse((project / "ods-builds").exists())
                self.assertEqual({str(p.relative_to(project)): p.read_bytes()
                                  for p in project.rglob("*") if p.is_file()}, original)
                # A same-name/image/label container with a different command
                # is not ours to stop, even when it mounts this job's volume.
                from project_runtime import recover_job, stage_arguments
                controller.storage.reserve(image, job)
                controller.storage.create_volume(job)
                args = stage_arguments(image, job, "test", runtime=self.runtime)
                foreign = (["python", "-I", "-c", "import time; time.sleep(120)"] if self.runtime == "python"
                           else ["node", "-e", "setInterval(()=>{},1000)"])
                args = args[:args.index(image) + 1] + foreign
                subprocess.run([*args[:2], "-d", *args[2:]], check=True, capture_output=True, timeout=15)
                evidence = recover_job(image, job, cancel=True, runtime=self.runtime)
                self.assertEqual(evidence["evidence"], "identity-mismatch")
                actual = json.loads(subprocess.check_output(["docker", "inspect", job + "-test"]))[0]
                self.assertTrue(actual["State"]["Running"], "foreign command was stopped")
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=10)
                if controller:
                    controller.close()
                if job and re.fullmatch(r"ods-project-[a-f0-9]{24}", job):
                    # Fixture-created random identity only, never broad cleanup.
                    for stage in ("seed-manifests", "seed-source", "acquire", "test", "build", "keeper"):
                        subprocess.run(["docker", "rm", "-f", job + "-" + stage], capture_output=True, timeout=15)
                    subprocess.run(["docker", "volume", "rm", job], capture_output=True, timeout=15)


class PythonCrashRuntimeTests(ProjectCrashRuntimeTests):
    __unittest_skip__ = os.environ.get("ODS_TEST_PROJECT_PYTHON") != "1"
    __unittest_skip_why__ = "disposable Python Docker opt-in"
    runtime = "python"
