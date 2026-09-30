import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import Mock, patch

HOST = Path(__file__).resolve().parents[1] / "host"
sys.path.insert(0, str(HOST))
from project_runtime import run_stage, stage_arguments, seed_project, observe_stage
from project_snapshot import snapshot_project
from project_artifacts import collect_artifacts, import_artifacts


class ProjectStageInputTests(unittest.TestCase):
    def test_failed_stop_without_verified_exit_remains_unconfirmed(self):
        for evidence in ({"status": "running", "evidence": "docker-state"},
                         {"status": "unconfirmed", "evidence": "unavailable"},
                         {"status": "unconfirmed", "evidence": "identity-mismatch"}):
            with self.subTest(evidence=evidence):
                cancel = threading.Event()
                process = Mock(stdout=io.BytesIO(b""), stderr=io.BytesIO(b""))
                process.poll.return_value = None
                process.wait.return_value = 0

                def request_cancel(_timeout):
                    cancel.set()
                    return True

                with patch.object(cancel, "wait", side_effect=request_cancel), \
                        patch("project_runtime.subprocess.Popen", return_value=process), \
                        patch("project_runtime.subprocess.run", return_value=Mock(returncode=1)), \
                        patch("project_runtime.observe_stage", return_value=evidence):
                    result = run_stage("sha256:" + "a" * 64, "ods-project-" + "a" * 24,
                                       "build", cancel=cancel)
                self.assertEqual(result["status"], "unconfirmed")

    def test_no_mutable_image_or_host_path_or_custom_stage(self):
        for image, job, stage in (("node:latest", "ods-project-" + "a" * 24, "build"),
                                  ("sha256:" + "a" * 64, "/home/user", "build"),
                                  ("sha256:" + "a" * 64, "ods-project-" + "a" * 24, "shell")):
            with self.assertRaises(ValueError):
                stage_arguments(image, job, stage)

    def test_cancellation_before_start_never_creates_a_process(self):
        event = threading.Event()
        event.set()
        self.assertEqual(run_stage("sha256:" + "a" * 64, "ods-project-" + "a" * 24,
                                   "build", cancel=event),
                         {"status": "cancelled", "exitCode": None, "started": False})


@unittest.skipUnless(os.environ.get("ODS_TEST_PROJECT_NODE") == "1", "real Docker opt-in")
class ProjectStageRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            iid = Path(temp) / "iid"
            subprocess.run(["docker", "build", "--iidfile", str(iid), "-f",
                            str(HOST / "Dockerfile.project-node"), str(HOST)],
                           check=True, capture_output=True, timeout=180)
            cls.image = iid.read_text().strip()

    def setUp(self):
        self.job = "ods-project-" + uuid.uuid4().hex[:24]

    def tearDown(self):
        for stage in ("init", "build", "test", "acquire", "seed-manifests", "seed-source"):
            subprocess.run(["docker", "rm", "-f", self.job + "-" + stage], capture_output=True)
        subprocess.run(["docker", "volume", "rm", self.job], capture_output=True)

    def seed(self, code):
        args = stage_arguments(self.image, self.job, "build")
        args[args.index("--name") + 1] = self.job + "-init"
        args = args[:args.index(self.image) + 1]
        files = {"package.json": json.dumps({"private": True, "scripts": {"build": "node build.cjs"}}),
                 "build.cjs": code}
        script = "const f=require('fs');for(const [p,v] of Object.entries(" + json.dumps(files) + "))f.writeFileSync(p,v)"
        subprocess.run([*args, "node", "-e", script], check=True, capture_output=True, timeout=30)

    def test_success_and_bounded_output(self):
        self.seed("console.log('x'.repeat(100000)); console.error('stderr witness');")
        result = run_stage(self.image, self.job, "build", cancel=threading.Event())
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(result["stdout"].encode()), 65536)
        self.assertTrue(result["truncated"]["stdout"])
        self.assertIn("stderr witness", result["stderr"])

    def test_nonzero_build_is_not_success(self):
        self.seed("console.error('deliberate fixture failure'); process.exit(7);")
        result = run_stage(self.image, self.job, "build", cancel=threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exitCode"], 7)

    def test_timeout_stops_container(self):
        self.seed("setInterval(()=>{},1000);")
        result = run_stage(self.image, self.job, "build", cancel=threading.Event(), timeout=2)
        self.assertEqual(result["status"], "timed_out")
        container = json.loads(subprocess.check_output(["docker", "inspect", self.job + "-build"]))[0]
        self.assertFalse(container["State"]["Running"])

    def test_cancel_running_stage_stops_container(self):
        self.seed("setInterval(()=>{},1000);")
        cancel = threading.Event()
        timer = threading.Timer(2, cancel.set)
        timer.start()
        try:
            result = run_stage(self.image, self.job, "build", cancel=cancel)
        finally:
            timer.cancel()
        self.assertEqual(result["status"], "cancelled")
        container = json.loads(subprocess.check_output(["docker", "inspect", self.job + "-build"]))[0]
        self.assertFalse(container["State"]["Running"])

    def test_cancel_stop_failure_reconciles_exact_exited_container(self):
        self.seed("console.log('completed before stop acknowledgement');")
        cancel = threading.Event()
        original_run = subprocess.run

        def stop_race(args, **kwargs):
            if args[:2] == ["docker", "stop"]:
                return subprocess.CompletedProcess(args, 1, b"", b"stop unavailable")
            return original_run(args, **kwargs)

        def request_cancel(_timeout):
            cancel.set()
            return True

        with patch.object(cancel, "wait", side_effect=request_cancel), \
                patch("project_runtime.subprocess.run", side_effect=stop_race):
            result = run_stage(self.image, self.job, "build", cancel=cancel)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["exitCode"], 0)
        evidence = observe_stage(self.image, self.job, "build")
        self.assertEqual(evidence["status"], "succeeded")

    def test_snapshot_transport_and_offline_build_leave_source_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "project"
            source.mkdir()
            package = {"private": True, "scripts": {"build": "node build.cjs"}}
            (source / "package.json").write_text(json.dumps(package))
            (source / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": package}}))
            (source / "build.cjs").write_text("const f=require('fs');if(f.existsSync('.env'))throw Error('secret copied');f.mkdirSync('out');f.writeFileSync('out/index.html','<h1>actual output</h1>');")
            (source / ".env").write_text("PRIVATE")
            snapshot = snapshot_project(root, "project")
            seed_project(self.image, self.job, snapshot, manifests_only=True)
            # The acquisition environment receives no source or secret files.
            args = stage_arguments(self.image, self.job, "build")
            args[args.index("--name") + 1] = self.job + "-init"
            args = args[:args.index(self.image) + 1]
            subprocess.run([*args, "node", "-e", "const f=require('fs');if(f.existsSync('build.cjs')||f.existsSync('.env'))process.exit(1)"],
                           check=True, capture_output=True, timeout=30)
            acquired = run_stage(self.image, self.job, "acquire", cancel=threading.Event())
            self.assertEqual(acquired["status"], "succeeded", acquired)
            seed_project(self.image, self.job, snapshot, manifests_only=False)
            result = run_stage(self.image, self.job, "build", cancel=threading.Event())
            self.assertEqual(result["status"], "succeeded", result)
            artifacts = collect_artifacts(self.image, self.job, "out")
            self.assertEqual(artifacts["files"]["index.html"], b"<h1>actual output</h1>")
            self.assertFalse((source / "out").exists())
            imported = import_artifacts(root, "project", self.job, artifacts)
            self.assertEqual((Path(root) / imported / "index.html").read_bytes(), b"<h1>actual output</h1>")
            with self.assertRaises(FileExistsError):
                import_artifacts(root, "project", self.job, artifacts)
            self.assertEqual(snapshot_project(root, "project")["sha256"], snapshot["sha256"])

    def test_observation_recovers_real_exit_without_rerunning(self):
        self.seed("setTimeout(()=>console.log('finished once'),2000);")
        args = stage_arguments(self.image, self.job, "build")
        subprocess.run([*args[:2], "-d", *args[2:]], check=True, capture_output=True, timeout=30)
        first = observe_stage(self.image, self.job, "build")
        self.assertEqual(first["status"], "running", first)
        subprocess.run(["docker", "wait", self.job + "-build"], check=True, capture_output=True, timeout=30)
        result = observe_stage(self.image, self.job, "build")
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["exitCode"], 0)
        self.assertTrue(result["outputUnavailable"])
        self.assertEqual(observe_stage(self.image, self.job, "build"), result)

    def test_missing_container_is_unknown_not_failure_or_success(self):
        self.assertEqual(observe_stage(self.image, self.job, "build")["status"], "unconfirmed")

    def test_same_name_wrong_command_cannot_supply_completion_evidence(self):
        args = stage_arguments(self.image, self.job, "build")
        args = args[:args.index(self.image) + 1] + ["node", "--version"]
        subprocess.run(args, check=True, capture_output=True, timeout=30)
        self.assertEqual(observe_stage(self.image, self.job, "build")["evidence"], "identity-mismatch")


if __name__ == "__main__":
    unittest.main()
