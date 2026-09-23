import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECEIVER = ROOT / "deploy/ops-runner/receive-artifact.py"
ACTION = ROOT / "deploy/ops-runner/action.py"
MANAGED = ROOT / "deploy/ops-runner/managed.py"
DISPATCH = ROOT / "deploy/ops-runner/dispatch.py"
JOB_ID = "ops-1780000000000-abcdef123456"


@unittest.skipUnless(os.name == "posix", "runner receiver uses POSIX no-follow directory handles")
class OpsRunnerTests(unittest.TestCase):
    def run_receiver(self, root, payload, expected=None, maximum=None):
        expected = expected or hashlib.sha256(payload).hexdigest()
        environment = {**os.environ, "PIXEL_RUNNER_ARTIFACT_ROOT": str(root)}
        if maximum is not None:
            environment["PIXEL_RUNNER_ARTIFACT_MAX"] = str(maximum)
        return subprocess.run(
            [sys.executable, str(RECEIVER), JOB_ID, "artifact.bin", expected],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=False,
        )

    def test_receiver_writes_exact_non_executable_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"
            payload = b"pixel-artifact\n"
            completed = self.run_receiver(root, payload)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            evidence = json.loads(completed.stdout)
            destination = root / JOB_ID / "artifact.bin"
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(evidence["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertFalse(evidence["executable"])
            self.assertEqual(destination.stat().st_mode & 0o111, 0)

    def test_receiver_refuses_overwrite_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"
            self.assertEqual(self.run_receiver(root, b"first").returncode, 0)
            completed = self.run_receiver(root, b"second")
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual((root / JOB_ID / "artifact.bin").read_bytes(), b"first")

    def test_receiver_removes_hash_mismatch_and_oversize_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "hash"
            completed = self.run_receiver(root, b"payload", expected="0" * 64)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / JOB_ID / "artifact.bin").exists())
            root = Path(directory) / "size"
            completed = self.run_receiver(root, b"12345", maximum=4)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / JOB_ID / "artifact.bin").exists())

    def test_receiver_refuses_symlinked_job_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"; root.mkdir()
            outside = Path(directory) / "outside"; outside.mkdir()
            (root / JOB_ID).symlink_to(outside, target_is_directory=True)
            completed = self.run_receiver(root, b"payload")
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((outside / "artifact.bin").exists())


@unittest.skipUnless(os.name == "posix", "forced SSH dispatcher uses POSIX paths")
class OpsDispatchTests(unittest.TestCase):
    def run_dispatch(self, root: Path, command: str):
        environment = {
            **os.environ,
            "SSH_ORIGINAL_COMMAND": command,
            "PIXEL_OPS_DISPATCH_TESTING": "1",
        }
        return subprocess.run(
            [sys.executable, str(DISPATCH), "--job-root", str(root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=False,
        )

    def run_workload_dispatch(self, root: Path, cwd: Path, *command: str):
        environment = {**os.environ, "PIXEL_OPS_DISPATCH_TESTING": "1"}
        return subprocess.run(
            [
                sys.executable, str(DISPATCH), "--workload", "--job-root", str(root),
                "--workload-user", "pixel-runner", "--cwd", str(cwd), "--", *command,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=False,
        )

    def test_forced_dispatch_allows_only_typed_commands_without_a_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "jobs"; root.mkdir()
            child = root / "work"; child.mkdir()
            commands = [
                "hostname",
                f"cd -- {shlex.quote(str(child))} && exec /bin/hostname",
                f"cd -- {shlex.quote(str(child))} && exec /usr/bin/uptime",
                f"cd -- {shlex.quote(str(child))} && exec /usr/local/libexec/pixel-ops-run-test health",
                f"cd -- {shlex.quote(str(child))} && exec /usr/local/libexec/pixel-ops-action repo status client-app",
                f"cd -- {shlex.quote(str(child))} && exec /usr/bin/sudo --non-interactive /usr/local/libexec/pixel-ops-managed service verify pixel-fixture",
                f"/usr/local/libexec/pixel-ops-receive-artifact {JOB_ID} artifact.bin {'0' * 64}",
            ]
            for command in commands:
                completed = self.run_dispatch(root, command)
                self.assertEqual(completed.returncode, 0, (command, completed.stderr.decode()))
                evidence = json.loads(completed.stdout)
                self.assertFalse(evidence["shell"])
                self.assertTrue(evidence["transportIdentitySeparated"])
                managed = evidence["argv"][0] == "/usr/bin/sudo"
                self.assertEqual(evidence["executionIdentity"], "root" if managed else "pixel-runner")
                self.assertEqual(evidence["directoryHandle"], not managed)
                self.assertTrue(Path(evidence["argv"][0]).is_absolute())
            workload = self.run_workload_dispatch(root, child, "/bin/hostname")
            self.assertEqual(workload.returncode, 0, workload.stderr.decode())
            workload_evidence = json.loads(workload.stdout)
            self.assertTrue(workload_evidence["directoryHandle"])
            self.assertEqual(workload_evidence["executionIdentity"], "pixel-runner")
            uptime = self.run_workload_dispatch(root, child, "/usr/bin/uptime")
            self.assertEqual(uptime.returncode, 0, uptime.stderr.decode())
            self.assertEqual(json.loads(uptime.stdout)["argv"], ["/usr/bin/uptime"])

    def test_forced_dispatch_rejects_shells_options_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base / "jobs"; root.mkdir()
            child = root / "work"; child.mkdir()
            outside = base / "outside"; outside.mkdir()
            link = root / "escape"; link.symlink_to(outside, target_is_directory=True)
            commands = [
                "/bin/bash -lc id",
                f"cd -- {shlex.quote(str(child))} && exec /bin/sh -c id",
                f"cd -- {shlex.quote(str(child))} && exec /bin/hostname ; /usr/bin/id",
                f"cd -- {shlex.quote(str(child))} && exec /usr/bin/uptime --pretty",
                f"cd -- {shlex.quote(str(child))} && exec /usr/bin/uptime -p",
                f"cd -- {shlex.quote(str(child))} && exec /bin/uptime",
                f"cd -- {shlex.quote(str(link))} && exec /bin/hostname",
                f"cd -- {shlex.quote(str(child))} && exec /usr/bin/sudo --preserve-env /usr/local/libexec/pixel-ops-managed service verify pixel-fixture",
                f"/usr/local/libexec/pixel-ops-receive-artifact {JOB_ID} ../escape {'0' * 64}",
            ]
            for command in commands:
                completed = self.run_dispatch(root, command)
                self.assertNotEqual(completed.returncode, 0, command)


@unittest.skipUnless(os.name == "posix", "action packs use Linux runner contracts")
class OpsActionPackTests(unittest.TestCase):
    def run_action(self, configuration, *arguments):
        environment = {**os.environ, "PIXEL_OPS_ACTION_CONFIG": str(configuration), "PIXEL_OPS_ACTION_TESTING": "1"}
        return subprocess.run(
            [sys.executable, str(ACTION), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    def run_managed(self, configuration, *arguments):
        environment = {**os.environ, "PIXEL_OPS_MANAGED_CONFIG": str(configuration), "PIXEL_OPS_MANAGED_TESTING": "1"}
        return subprocess.run(
            [sys.executable, str(MANAGED), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    def test_read_repository_and_artifact_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"; jobs.mkdir()
            repository = jobs / "client-app"; repository.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "pixel@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Pixel Test"], check=True)
            (repository / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
            source = root / "fixture.log"; source.write_text("evidence\n", encoding="utf-8")
            data = jobs / "data"; data.mkdir(); (data / "payload.txt").write_text("payload\n", encoding="utf-8")
            configuration = root / "actions.json"
            configuration.write_text(json.dumps({
                "schemaVersion": 1, "jobRoot": str(jobs), "maxArtifactBytes": 1024 * 1024,
                "repositories": {"client-app": {"path": str(repository), "allowedRefs": ["main"], "recipes": {
                    "test": {"argv": ["/bin/printf", "recipe-ok\\n"], "timeoutSeconds": 10},
                }}},
                "collectSources": {"fixture-log": str(source)}, "services": {},
            }), encoding="utf-8")
            for arguments in (("host", "summary"), ("repo", "status", "client-app"), ("repo", "recipe", "client-app", "test")):
                completed = self.run_action(configuration, *arguments)
                self.assertEqual(completed.returncode, 0, completed.stderr.decode())
                self.assertEqual(json.loads(completed.stdout)["schemaVersion"], 1)
            checksum = self.run_action(configuration, "artifact", "checksum", "data/payload.txt")
            self.assertEqual(checksum.returncode, 0, checksum.stderr.decode())
            self.assertEqual(json.loads(checksum.stdout)["sha256"], hashlib.sha256(b"payload\n").hexdigest())
            collected = self.run_action(configuration, "artifact", "collect", "fixture-log", "fixture.txt")
            self.assertEqual(collected.returncode, 0, collected.stderr.decode())
            self.assertEqual((jobs / "collections/fixture.txt").read_text(encoding="utf-8"), "evidence\n")
            archived = self.run_action(configuration, "artifact", "archive", "data", "data.tar.gz")
            self.assertEqual(archived.returncode, 0, archived.stderr.decode())
            self.assertTrue((jobs / "archives/data.tar.gz").is_file())
            refused = self.run_action(configuration, "repo", "checkout", "client-app", "forbidden")
            self.assertNotEqual(refused.returncode, 0)

    def test_archive_refuses_symlinks_and_cleanup_stays_below_job_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jobs = root / "jobs"; jobs.mkdir()
            source = jobs / "source"; source.mkdir(); (source / "link").symlink_to("/etc/passwd")
            configuration = root / "actions.json"
            configuration.write_text(json.dumps({"schemaVersion": 1, "jobRoot": str(jobs)}), encoding="utf-8")
            completed = self.run_action(configuration, "artifact", "archive", "source", "archive.tar.gz")
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((jobs / "archive.tar.gz").exists())
            trash = jobs / "trash"; trash.mkdir(); (trash / "file").write_text("x", encoding="utf-8")
            completed = self.run_action(configuration, "artifact", "cleanup", "trash")
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertFalse(trash.exists())
            completed = self.run_action(configuration, "artifact", "cleanup", "../")
            self.assertNotEqual(completed.returncode, 0)

    def test_transactional_deployment_rolls_back_failed_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); releases = root / "releases"; releases.mkdir()
            for name, healthy in (("v1", True), ("v2", True), ("bad", False)):
                release = releases / name; release.mkdir()
                if healthy: (release / "healthy").write_text("ok", encoding="utf-8")
            current, previous = root / "current", root / "previous"
            current.symlink_to(releases / "v1")
            configuration = root / "managed.json"
            configuration.write_text(json.dumps({
                "schemaVersion": 1,
                "deployments": {"app": {
                    "releasesRoot": str(releases), "currentLink": str(current), "previousLink": str(previous),
                    "verifyCommand": ["/usr/bin/test", "-f", "{release}/healthy"],
                }},
            }), encoding="utf-8")
            activated = self.run_managed(configuration, "deploy", "activate", "app", "v2")
            self.assertEqual(activated.returncode, 0, activated.stderr.decode())
            self.assertEqual(current.resolve(), (releases / "v2").resolve())
            self.assertEqual(previous.resolve(), (releases / "v1").resolve())
            failed = self.run_managed(configuration, "deploy", "activate", "app", "bad")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('"rolledBack": true', failed.stderr.decode())
            self.assertEqual(current.resolve(), (releases / "v2").resolve())
            self.assertEqual(previous.resolve(), (releases / "v1").resolve())
            rolled_back = self.run_managed(configuration, "deploy", "rollback", "app")
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr.decode())
            self.assertEqual(current.resolve(), (releases / "v1").resolve())

    def test_activate_crash_between_link_swaps_never_loses_rollback_target(self):
        # A crash between activate's two non-atomic link swaps must never cement a current pointer
        # whose previous-pointer update was lost, which would later make rollback silently restore
        # the wrong (older) release. Driven in-process so the crash can be injected at the exact
        # instant of the previous-link write.
        import importlib.util

        class _Crash(BaseException):
            pass

        prior_env = os.environ.get("PIXEL_OPS_MANAGED_TESTING")
        os.environ["PIXEL_OPS_MANAGED_TESTING"] = "1"
        try:
            spec = importlib.util.spec_from_file_location("pixel_managed_c3", MANAGED)
            managed = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(managed)
        finally:
            if prior_env is None:
                os.environ.pop("PIXEL_OPS_MANAGED_TESTING", None)
            else:
                os.environ["PIXEL_OPS_MANAGED_TESTING"] = prior_env
        if getattr(os, "geteuid", lambda: 0)() == 0:
            self.skipTest("managed TESTING mode requires a non-root euid")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); releases = root / "releases"; releases.mkdir()
            for name in ("v0", "v1", "v2"):
                release = releases / name; release.mkdir(); (release / "healthy").write_text("ok", encoding="utf-8")
            current, previous = root / "current", root / "previous"
            current.symlink_to(releases / "v1")
            previous.symlink_to(releases / "v0")
            configuration = {"schemaVersion": 1, "deployments": {"app": {
                "releasesRoot": str(releases), "currentLink": str(current), "previousLink": str(previous),
                "verifyCommand": ["/usr/bin/test", "-f", "{release}/healthy"],
            }}}
            real_replace = managed.replace_link

            def crashing(link, target):
                if str(link) == str(previous):
                    raise _Crash("simulated process death at the previous-link write")
                return real_replace(link, target)

            managed.replace_link = crashing
            try:
                with self.assertRaises(_Crash):
                    managed.deployment_action(configuration, ["activate", "app", "v2"])
            finally:
                managed.replace_link = real_replace

            # Retrying the activation completes both link swaps rather than short-circuiting on the
            # already-swapped current pointer, so the superseded release is preserved as previous.
            managed.deployment_action(configuration, ["activate", "app", "v2"])
            self.assertEqual(current.resolve(), (releases / "v2").resolve())
            self.assertEqual(previous.resolve(), (releases / "v1").resolve())

            # Therefore a later rollback restores v1 (the release actually superseded), never the
            # two-generations-older v0 that the pre-fix crash window would have silently restored.
            managed.deployment_action(configuration, ["rollback", "app"])
            self.assertEqual(current.resolve(), (releases / "v1").resolve())

    def test_package_hash_verification_and_failure_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); artifacts = root / "artifacts"; artifacts.mkdir()
            package = artifacts / "package.bin"; package.write_bytes(b"package\n")
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            configuration = root / "managed.json"
            base = {
                "schemaVersion": 1, "artifactRoots": [str(artifacts)], "verifiedArtifactRoot": str(root / "verified"),
                "packages": {"fixture": {
                    "signatureCommand": ["/bin/true"], "installCommand": ["/bin/true"],
                    "verifyCommand": ["/bin/true"], "rollbackCommand": ["/bin/true"],
                    "rollbackVerifyCommand": ["/bin/true"],
                }},
            }
            configuration.write_text(json.dumps(base), encoding="utf-8")
            verified = self.run_managed(configuration, "package", "verify", "fixture", str(package), checksum)
            self.assertEqual(verified.returncode, 0, verified.stderr.decode())
            installed = self.run_managed(configuration, "package", "install", "fixture", str(package), checksum)
            self.assertEqual(installed.returncode, 0, installed.stderr.decode())
            install_verified = self.run_managed(configuration, "package", "install-verify", "fixture", str(package), checksum)
            self.assertEqual(install_verified.returncode, 0, install_verified.stderr.decode())
            rolled_back = self.run_managed(configuration, "package", "rollback", "fixture", str(package), checksum)
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr.decode())
            rollback_verified = self.run_managed(configuration, "package", "rollback-verify", "fixture", str(package), checksum)
            self.assertEqual(rollback_verified.returncode, 0, rollback_verified.stderr.decode())
            self.assertEqual(list((root / "verified").iterdir()), [])
            wrong = self.run_managed(configuration, "package", "verify", "fixture", str(package), "0" * 64)
            self.assertNotEqual(wrong.returncode, 0)
            symlink = artifacts / "package-link.bin"; symlink.symlink_to(package)
            linked = self.run_managed(configuration, "package", "verify", "fixture", str(symlink), checksum)
            self.assertNotEqual(linked.returncode, 0)
            base["packages"]["fixture"]["verifyCommand"] = ["/bin/false"]
            configuration.write_text(json.dumps(base), encoding="utf-8")
            failed = self.run_managed(configuration, "package", "install", "fixture", str(package), checksum)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('"rolledBack": true', failed.stderr.decode())
            base["packages"]["fixture"]["rollbackVerifyCommand"] = ["/bin/false"]
            configuration.write_text(json.dumps(base), encoding="utf-8")
            rollback_failed = self.run_managed(configuration, "package", "install", "fixture", str(package), checksum)
            self.assertNotEqual(rollback_failed.returncode, 0)
            self.assertIn('"rolledBack": false', rollback_failed.stderr.decode())

    def test_semantic_configuration_validation_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jobs = root / "jobs"; jobs.mkdir()
            repository = jobs / "repo"; repository.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
            source = root / "source.log"; source.write_text("fixture\n", encoding="utf-8")
            actions = root / "actions.json"
            value = {
                "schemaVersion": 1, "jobRoot": str(jobs),
                "repositories": {"app": {"path": str(repository), "allowedRefs": ["main"], "recipes": {"test": {"argv": ["/bin/true"]}}}},
                "collectSources": {"fixture": str(source)}, "services": {"fixture": {"unit": "fixture.service"}},
            }
            actions.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(self.run_action(actions, "--validate-config").returncode, 0)
            value["repositories"]["app"]["recipes"]["test"]["argv"] = ["sh", "-c", "true"]
            actions.write_text(json.dumps(value), encoding="utf-8")
            self.assertNotEqual(self.run_action(actions, "--validate-config").returncode, 0)
            value["repositories"]["app"]["recipes"]["test"]["argv"] = ["/bin/true"]
            value["repositories"]["app"]["recipes"]["test"]["timeoutSeconds"] = False
            actions.write_text(json.dumps(value), encoding="utf-8")
            self.assertNotEqual(self.run_action(actions, "--validate-config").returncode, 0)
            del value["repositories"]["app"]["recipes"]["test"]["timeoutSeconds"]
            value["maxArtifactBytes"] = True
            actions.write_text(json.dumps(value), encoding="utf-8")
            self.assertNotEqual(self.run_action(actions, "--validate-config").returncode, 0)

            releases = root / "releases"; releases.mkdir()
            current = root / "current"; previous = root / "previous"
            managed = root / "managed.json"
            managed_value = {
                "schemaVersion": 1, "artifactRoots": [str(jobs)], "verifiedArtifactRoot": str(root / "verified"),
                "services": {}, "deployments": {"app": {"releasesRoot": str(releases), "currentLink": str(current), "previousLink": str(previous)}},
                "packages": {"fixture": {"signatureCommand": ["/bin/true"], "installCommand": ["/bin/true"], "verifyCommand": ["/bin/true"], "rollbackCommand": ["/bin/true"], "rollbackVerifyCommand": ["/bin/true"]}},
            }
            managed.write_text(json.dumps(managed_value), encoding="utf-8")
            self.assertEqual(self.run_managed(managed, "--validate-config").returncode, 0)
            managed_value["maxPackageBytes"] = True
            managed.write_text(json.dumps(managed_value), encoding="utf-8")
            self.assertNotEqual(self.run_managed(managed, "--validate-config").returncode, 0)
            del managed_value["maxPackageBytes"]
            del managed_value["packages"]["fixture"]["rollbackVerifyCommand"]
            managed.write_text(json.dumps(managed_value), encoding="utf-8")
            self.assertNotEqual(self.run_managed(managed, "--validate-config").returncode, 0)

    def test_runner_output_is_streamed_bounded_and_grandchildren_are_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jobs = root / "jobs"; jobs.mkdir()
            repository = jobs / "repo"; repository.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
            flood = "import sys; sys.stdout.write('A'*2000000); sys.stderr.write('B'*2000000)"
            orphan = "import subprocess; p=subprocess.Popen(['/bin/sleep','30']); print(p.pid, flush=True)"
            configuration = root / "actions.json"
            value = {
                "schemaVersion": 1, "jobRoot": str(jobs),
                "repositories": {"app": {"path": str(repository), "allowedRefs": ["main"], "recipes": {
                    "flood": {"argv": [sys.executable, "-c", flood], "timeoutSeconds": 10},
                    "orphan": {"argv": [sys.executable, "-c", orphan], "timeoutSeconds": 10},
                }}},
            }
            configuration.write_text(json.dumps(value), encoding="utf-8")
            flooded = self.run_action(configuration, "repo", "recipe", "app", "flood")
            self.assertEqual(flooded.returncode, 0, flooded.stderr.decode())
            evidence = json.loads(flooded.stdout)["evidence"]
            self.assertTrue(evidence["outputTruncated"])
            self.assertLessEqual(len(evidence["stdout"].encode()), 256 * 1024)
            self.assertLessEqual(len(evidence["stderr"].encode()), 256 * 1024)
            orphaned = self.run_action(configuration, "repo", "recipe", "app", "orphan")
            self.assertEqual(orphaned.returncode, 0, orphaned.stderr.decode())
            child_pid = int(json.loads(orphaned.stdout)["evidence"]["stdout"].strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

            artifact = root / "artifact.bin"; artifact.write_bytes(b"fixture")
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            managed = root / "managed.json"
            managed.write_text(json.dumps({
                "schemaVersion": 1, "artifactRoots": [str(root)], "verifiedArtifactRoot": str(root / "verified"),
                "packages": {"fixture": {
                    "signatureCommand": [sys.executable, "-c", flood],
                    "installCommand": ["/bin/true"], "verifyCommand": ["/bin/true"],
                    "rollbackCommand": ["/bin/true"], "rollbackVerifyCommand": ["/bin/true"],
                }},
            }), encoding="utf-8")
            managed_flood = self.run_managed(managed, "package", "verify", "fixture", str(artifact), checksum)
            self.assertEqual(managed_flood.returncode, 0, managed_flood.stderr.decode())
            managed_evidence = json.loads(managed_flood.stdout)["signature"]
            self.assertTrue(managed_evidence["outputTruncated"])
            self.assertLessEqual(len(managed_evidence["stdout"].encode()), 128 * 1024)
            self.assertLessEqual(len(managed_evidence["stderr"].encode()), 128 * 1024)


if __name__ == "__main__":
    unittest.main()
