import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_supported_host_probe", ROOT / "scripts/supported-host-systemd-probe.py",
)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


class SupportedHostSystemdTests(unittest.TestCase):
    def test_runner_is_clean_exact_pinned_bounded_and_disposable(self):
        runner = (ROOT / "scripts/run-supported-host-systemd-matrix.sh").read_text(encoding="utf-8")
        for required in (
            "status --porcelain=v1 --untracked-files=all",
            "rev-parse 'HEAD^{tree}'",
            ".qualificationImages[$key].fingerprint",
            'incus image copy "images:$fingerprint"',
            'timeout 240 incus launch "$image" "$instance" --vm -c limits.cpu=4 -c limits.memory=8GiB </dev/null',
            'timeout 60 incus delete --force "$instance" </dev/null',
            'incus image copy "images:$fingerprint" local: </dev/null',
            'incus exec "$instance" -- systemctl is-system-running --wait </dev/null',
            '--expected-os-version "$os_version" </dev/null || status=$?',
            'incus exec "$instance" -- tar -C /home/pixelqual/evidence -cf - . </dev/null',
            'tar -C /opt/pixel-qualification-source -xf - < "$stage/source.tar"',
            "--immutable-source /opt/pixel-qualification-source",
            "ubuntu2404Vm",
            "debian12Vm",
            "gcc",
            "libc6-dev",
            "env -i",
            "providerCalls == 0",
            "credentialInputs == 0",
            "/tmp/dream-fleet-heavy.lock",
        ):
            self.assertIn(required, runner)
        self.assertNotIn("images:ubuntu/24.04", runner)
        self.assertNotIn("images:debian/12", runner)
        self.assertNotIn("--no-host-lock", runner)

    def test_probe_rejects_inherited_credential_like_environment(self):
        with mock.patch.dict(os.environ, {"HOME": "/tmp/owner", "PROVIDER_API_KEY": "secret"}, clear=True):
            with self.assertRaisesRegex(probe.QualificationError, "credential-like"):
                probe.assert_credential_free_environment()
        with mock.patch.dict(os.environ, {"HOME": "/tmp/owner", "SUDO_COMMAND": "safe"}, clear=True):
            probe.assert_credential_free_environment()

    def test_evidence_must_be_new_external_and_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            with self.assertRaisesRegex(probe.QualificationError, "outside"):
                probe.require_private_directory(source / "evidence", source)
            evidence = root / "private-evidence"
            self.assertEqual(probe.require_private_directory(evidence, source), evidence)
            if os.name != "nt":
                self.assertEqual(evidence.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                probe.require_private_directory(evidence, source)

    def test_recorder_retroactively_scrubs_a_late_discovered_runtime_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            recorder = probe.Recorder(evidence, {"lane": "test"})
            secret = "late-runtime-secret-value"
            recorder.run("before-discovery", [sys.executable, "-c", f"print('{secret}')"])
            self.assertIn(secret, next(evidence.glob("*.log")).read_text(encoding="utf-8"))
            recorder.add_secret(secret)
            retained = next(evidence.glob("*.log")).read_text(encoding="utf-8")
            self.assertNotIn(secret, retained)
            self.assertIn("[REDACTED-RUNTIME-CREDENTIAL]", retained)

    def test_loopback_fixtures_have_no_external_route_and_fail_degraded(self):
        services = probe.SyntheticLocalServices()
        services.start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:19999/v1/models", timeout=2) as response:
                models = json.loads(response.read())
            self.assertEqual(models["data"][0]["id"], "pixel-qualification-local")
            with urllib.request.urlopen("http://127.0.0.1:19999/search?q=test&format=json", timeout=2) as response:
                self.assertEqual(json.loads(response.read()), {"results": []})
            services.available = False
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen("http://127.0.0.1:19999/v1/models", timeout=2)
            self.assertEqual(caught.exception.code, 503)
            caught.exception.close()
        finally:
            services.stop()

    def test_agent_probe_uses_new_private_state_and_tests_both_token_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            state = probe.create_isolated_client_state(home)
            self.assertEqual(state, home / ".local/state/pixel-host-qualification/isolated-openclaw")
            if os.name != "nt":
                self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            with self.assertRaisesRegex(probe.QualificationError, "must be new"):
                probe.create_isolated_client_state(home)
        source = (ROOT / "scripts/supported-host-systemd-probe.py").read_text(encoding="utf-8")
        self.assertIn('"agent-gateway-token-denial"', source)
        self.assertIn('invalid_command[invalid_command.index("--token") + 1] = invalid_token', source)
        self.assertIn('turn_env = {**env, "OPENCLAW_STATE_DIR": str(isolated_state)}', source)
        self.assertIn('"sandboxed-agent-turn", command, env=turn_env', source)
        self.assertIn('"deep-work-real-crash-endurance"', source)
        self.assertIn('"deep-work-supervised-service"', source)
        self.assertIn('wait_for_systemd_service_success(service, after=initial_start)', source)
        self.assertIn('"deep-work-service-install-inactive"', source)
        self.assertIn('"deep-work-service-watchdog-isolate-path"', source)
        self.assertIn('"deep-work-service-cancel-apply"', source)
        self.assertIn('"deep-work-service-private-state-retained"', source)
        self.assertIn('"capability-pack-live-runtime"', source)
        self.assertIn('"knowledge-vault-backup-recovery"', source)
        self.assertIn('"knowledge-vault-systemd-credential"', source)
        self.assertIn('"--property=LoadCredential=pixel-knowledge-vault-key:', source)
        self.assertIn('"--property=CapabilityBoundingSet="', source)
        self.assertIn('"--restored-knowledge-key"', source)
        self.assertIn('"PIXEL_LIVE_DOCKER": "1"', source)
        self.assertIn('"# skipped 0"', source)
        self.assertIn('"--restart-delay-ms", "25"', source)
        self.assertIn("validate_completed_sandbox_write(turn.stdout)", source)
        self.assertNotIn("approveDevicePairing", source)
        capability_fixture = (ROOT / "tests/work-capability-image-live.mjs").read_text(encoding="utf-8")
        self.assertIn('"image", "import"', capability_fixture)
        self.assertIn("const imageRef = imageId", capability_fixture)
        self.assertIn('network: "none"', capability_fixture)
        self.assertNotIn("DOCKER_BUILDKIT", capability_fixture)

    def test_sandbox_turn_rejects_failed_or_ambiguous_write_results(self):
        success = {
            "result": {
                "payloads": [{"text": "PIXEL_HOST_QUALIFICATION_COMPLETE"}],
                "meta": {"toolSummary": {"calls": 1, "failures": 0, "tools": ["write"]}},
            },
        }
        probe.validate_completed_sandbox_write(json.dumps(success))
        failed = json.loads(json.dumps(success))
        failed["result"]["meta"]["toolSummary"]["failures"] = 1
        with self.assertRaisesRegex(probe.QualificationError, "write tool"):
            probe.validate_completed_sandbox_write(json.dumps(failed))
        ambiguous = json.loads(json.dumps(success))
        ambiguous["result"]["payloads"] = [{"text": "not the completion marker"}]
        with self.assertRaisesRegex(probe.QualificationError, "did not complete"):
            probe.validate_completed_sandbox_write(json.dumps(ambiguous))

    def test_synthetic_model_context_is_large_enough_for_real_agent_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            answers = probe.write_answers(Path(temporary))
            self.assertGreaterEqual(json.loads(answers.read_text(encoding="utf-8"))["modelContextWindow"], 32768)

    def test_capability_pack_runtime_must_execute_and_cannot_pass_as_skipped(self):
        class Recorder:
            def __init__(self, output: str):
                self.output = output
                self.observed = None

            def run(self, label, command, **options):
                self.observed = (label, command, options)
                return subprocess.CompletedProcess(command, 0, self.output, "")

        passed = Recorder(
            "a real local OCI image completes the disabled admission and exact revocation lifecycle\n"
            "# pass 1\n# fail 0\n# skipped 0\n"
        )
        probe.exercise_capability_pack_runtime(passed, ROOT, {"PATH": "/usr/bin"})
        self.assertEqual(passed.observed[0], "capability-pack-live-runtime")
        self.assertEqual(passed.observed[1], ["node", "--test", "tests/work-capability-image-live.mjs"])
        self.assertEqual(passed.observed[2]["env"]["PIXEL_LIVE_DOCKER"], "1")
        skipped = Recorder(
            "a real local OCI image completes the disabled admission and exact revocation lifecycle\n"
            "# pass 0\n# fail 0\n# skipped 1\n"
        )
        with self.assertRaisesRegex(probe.QualificationError, "incomplete or skipped"):
            probe.exercise_capability_pack_runtime(skipped, ROOT, {"PATH": "/usr/bin"})

    def test_manifest_pins_both_supported_systemd_vms(self):
        manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        images = manifest["qualificationImages"]
        self.assertEqual(images["ubuntu2404Vm"]["type"], "virtual-machine")
        self.assertEqual(images["debian12Vm"]["type"], "virtual-machine")
        self.assertRegex(images["ubuntu2404Vm"]["fingerprint"], r"^[a-f0-9]{64}$")
        self.assertRegex(images["debian12Vm"]["fingerprint"], r"^[a-f0-9]{64}$")
        self.assertEqual(len({value["fingerprint"] for value in images.values()}), 4)

    def test_private_runner_workflow_is_manual_read_only_and_non_credentialed(self):
        workflow = (ROOT / ".github/workflows/product-qualification.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("runs-on: [self-hosted, linux, x64, pixel-systemd-vm]", workflow)
        self.assertIn("./pixel qualify-hosts", workflow)

    def test_probe_bootstraps_the_pinned_runtime_before_configuration(self):
        source = (ROOT / "scripts/supported-host-systemd-probe.py").read_text(encoding="utf-8")
        bootstrap = source.index('recorder.run("bootstrap-pinned-runtime"')
        installed_binary_check = source.index('if not binary.is_file():', bootstrap)
        configure = source.index('recorder.run("configure-minimal"', installed_binary_check)
        self.assertLess(bootstrap, installed_binary_check)
        self.assertLess(installed_binary_check, configure)

    def test_probe_labels_identity_refusal_and_disposable_removal_truthfully(self):
        source = (ROOT / "scripts/supported-host-systemd-probe.py").read_text(encoding="utf-8")
        self.assertIn('"release-identity-refusal-apply"', source)
        self.assertIn("Current release directory does not match its version identity", source)
        self.assertIn('checks["release-identity-refusal"] = "pass"', source)
        self.assertIn('checks["disposable-gateway-removal"] = "pass"', source)
        self.assertNotIn('checks["rollback-removal"]', source)

    @unittest.skipIf(os.name == "nt", "release identity rehearsal requires symlinks")
    def test_release_identity_refusal_repairs_disposable_state_on_success_and_plan_failure(self):
        class Recorder:
            def __init__(self, fail_plan=False):
                self.fail_plan = fail_plan
                self.labels = []

            def run(self, label, command, **options):
                self.labels.append(label)
                if label == "release-identity-refusal-plan" and self.fail_plan:
                    raise probe.QualificationError("prepared state failed")
                if label == "release-identity-refusal-apply":
                    return subprocess.CompletedProcess(
                        command, 1, "", "Current release directory does not match its version identity",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

        for fail_plan in (False, True):
            with self.subTest(fail_plan=fail_plan), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                source.mkdir()
                (source / "VERSION").write_text("4.3.27\n", encoding="ascii")
                install_dir = root / "install"
                release = install_dir / "releases/4.3.27"
                release.mkdir(parents=True)
                current = install_dir / "current"
                current.symlink_to(release)
                recorder = Recorder(fail_plan=fail_plan)
                if fail_plan:
                    with self.assertRaisesRegex(probe.QualificationError, "prepared state failed"):
                        probe.exercise_release_identity_refusal(recorder, source, {}, install_dir)
                else:
                    probe.exercise_release_identity_refusal(recorder, source, {}, install_dir)
                    self.assertIn("post-identity-refusal-verify", recorder.labels)
                self.assertEqual(current.resolve(), release.resolve())
                self.assertTrue(release.is_dir())
                self.assertFalse((install_dir / "releases/4.3.27-qualification-invalid").exists())

    def test_bootstrap_downloads_only_the_fully_locked_wheel_set(self):
        source = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
        wheel_download = next(
            line for line in source.splitlines()
            if ' -m pip download ' in line and '--dest "$wheel_stage"' in line
        )
        self.assertIn("--require-hashes", wheel_download)
        self.assertIn("--no-deps", wheel_download)
        self.assertIn("--only-binary=:all:", wheel_download)


if __name__ == "__main__":
    unittest.main()
