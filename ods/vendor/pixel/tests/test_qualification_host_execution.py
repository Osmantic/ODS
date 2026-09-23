import argparse
import functools
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import unittest
from unittest import mock

from tests.test_release_update import QualificationExecutionTests, ROOT

HARNESS = ROOT / "scripts" / "qualification-host-execution.py"
DOCKER = "/usr/bin/docker"
RELEASE_CONSTANTS = ROOT / "scripts" / "generated" / "release-constants.json"
SCRUBBED_ENV = {"PATH": "/usr/bin:/bin"}


def pinned_sandbox_image():
    """Derive the exact pinned qualification sandbox image from the generated
    release constants (the single source of truth), never a copied digest."""
    data = json.loads(RELEASE_CONSTANTS.read_text(encoding="utf-8"))
    base_image = data.get("baseImage")
    if not isinstance(base_image, str):
        raise AssertionError("generated release constants baseImage is invalid")
    match = re.fullmatch(r"[^@\s]+@(sha256:[0-9a-f]{64})", base_image)
    if match is None:
        raise AssertionError("generated release constants baseImage is not digest pinned")
    return base_image, match.group(1)


@functools.lru_cache(maxsize=1)
def docker_capable():
    """True only when usable Docker AND the exact pinned image are present
    locally, so real-Docker integration tests run without any network pull."""
    if not (os.path.isfile(DOCKER) and os.access(DOCKER, os.X_OK)):
        return False
    try:
        image, digest = pinned_sandbox_image()
    except (OSError, ValueError, AssertionError):
        return False
    try:
        result = subprocess.run(
            [DOCKER, "image", "inspect", image],
            capture_output=True, text=True, env=SCRUBBED_ENV, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        inspected = json.loads(result.stdout)
    except ValueError:
        return False
    if not inspected:
        return False
    record = inspected[0]
    identity = record.get("Id") or ""
    repo_suffixes = []
    for item in record.get("RepoDigests") or []:
        if isinstance(item, str) and "@" in item:
            repo_suffixes.append(item.split("@", 1)[1])
    return identity == digest or digest in repo_suffixes


def load_harness():
    specification = importlib.util.spec_from_file_location(
        "qualification_host_execution", HARNESS,
    )
    harness = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(harness)
    return harness


class QualificationHostExecutionTests(QualificationExecutionTests):
    def run_harness(self, candidate_id, confirm=True):
        argv = [
            sys.executable, str(HARNESS),
            "--candidate-id", candidate_id,
            "--allowed-signers", str(self.candidate.allowed_signers),
            "--identity", "pixel-release",
            "--qualification-root", str(self.qualification_root),
            "--baseline-version", self.baseline,
            "--production-install-root", str(self.production_root),
        ]
        if confirm:
            argv.append("--confirm")
        return subprocess.run(argv, capture_output=True, text=True, timeout=300)

    def claimed_run(self, probe="scripts/safe.sh", probe_timeout=30.0):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id, probe=probe, probe_timeout=probe_timeout)
        return candidate_id

    def durable_observation(self, candidate_id):
        run_dir = self.qualification_root / "runs" / candidate_id
        path = run_dir / self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE
        self.assertTrue(path.is_file())
        return json.loads(path.read_text(encoding="utf-8"))

    def run_dir(self, candidate_id):
        return self.qualification_root / "runs" / candidate_id

    def test_harness_requires_execution_claim(self):
        candidate_id, _ = self.acquire_run()
        result = self.run_harness(candidate_id)
        self.assertEqual(result.returncode, 1)
        self.assertIn("rejected", result.stderr)
        run_dir = self.run_dir(candidate_id)
        self.assertFalse(
            (run_dir / self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE).exists()
        )
        self.assertFalse(
            (run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE).exists()
        )

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_harness_runs_bounded_probe_and_records_failure(self):
        candidate_id = self.claimed_run()
        result = self.run_harness(candidate_id)
        self.assertEqual(result.returncode, 0)
        observation = self.durable_observation(candidate_id)
        self.assertEqual(observation["candidateId"], candidate_id)
        self.assertEqual(observation["outcome"], "failure")
        self.assertEqual(observation["phase"], "probe")
        self.assertEqual(observation["reason"], "non-zero-exit")
        self.assertEqual(observation["exitCode"], 91)
        self.assertTrue(observation["candidateCodeExecuted"])
        self.assertTrue(observation["executionObserved"])
        self.assertFalse(observation["terminalPromotionEvidence"])
        self.assert_qualification_authority(observation)
        self.assertTrue(observation["containerRemoved"])
        self.assertTrue(observation["absenceProven"])
        self.assertFalse(observation["networkUsed"])
        self.assertEqual(observation["sandbox"]["network"], "none")
        self.assertRegex(observation["executionSpecSha256"], r"^[0-9a-f]{64}$")
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        self.assertEqual(
            observation["executionSpecSha256"],
            self.release_update.sha256(spec_path.read_bytes()),
        )

    def test_spec_and_start_schema_shape(self):
        candidate_id = self.claimed_run()
        spec = self.read_json(
            self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE,
        )
        self.assert_schema_shape(
            spec, "release-update-qualification-execution-spec-v1.schema.json",
        )
        self.write_start_marker(candidate_id)
        start = self.read_json(
            self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_START_FILE,
        )
        self.assert_schema_shape(
            start, "release-update-qualification-execution-start-v1.schema.json",
        )

    def test_harness_never_fabricates_install_success(self):
        harness = load_harness()
        zero = harness.observation_for_probe_result(
            {"outcome": "failure", "exitCode": 0, "phase": "probe",
             "reason": "non-zero-exit", "signal": None, "timedOut": False},
        )
        self.assertEqual(zero["outcome"], "deferred")
        self.assertEqual(zero["phase"], "install")
        self.assertEqual(zero["reason"], "install-contract-deferred")
        self.assertIsNone(zero["exitCode"])

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_harness_never_touches_production_install_root(self):
        candidate_id = self.claimed_run()
        before = set()
        if self.production_root.exists():
            before = {p.name for p in self.production_root.iterdir()}
        result = self.run_harness(candidate_id)
        self.assertEqual(result.returncode, 0)
        self.assertEqual({p.name for p in self.production_root.iterdir()}, before)

    def test_harness_rejects_a_tampered_claim_before_execution(self):
        candidate_id = self.claimed_run()
        claim_path = self.claim_path(candidate_id)
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["version"] = "9.9.9"
        claim_path.write_text(self.release_update.canonical_json(claim).decode("utf-8"), encoding="utf-8")
        result = self.run_harness(candidate_id)
        self.assertEqual(result.returncode, 1)
        self.assertIn("rejected", result.stderr)
        self.assertFalse(
            (self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_START_FILE).exists()
        )

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_harness_never_re_executes_after_start_marker(self):
        candidate_id = self.claimed_run()
        first = self.run_harness(candidate_id)
        self.assertEqual(first.returncode, 0)
        run_dir = self.run_dir(candidate_id)
        observation_file = run_dir / self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE
        before = observation_file.read_bytes()
        second = self.run_harness(candidate_id)
        self.assertEqual(second.returncode, 1)
        self.assertIn("rejected", second.stderr)
        self.assertEqual(observation_file.read_bytes(), before)
        self.assertEqual(
            {p.name for p in run_dir.iterdir()},
            {
                "HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json",
                self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE,
                self.release_update.QUALIFICATION_EXECUTION_START_FILE,
                self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE,
            },
        )

    def test_harness_rejects_alternate_probe_rejected_at_claim_time(self):
        candidate_id, _ = self.acquire_run()
        with self.assertRaisesRegex(self.release_update.UpdateError, "probe"):
            self.claim_execution(candidate_id, probe="nonexistent/not-there.sh")
        self.assertFalse(self.claim_path(candidate_id).exists())

    def test_harness_claim_rejects_alternate_timeout(self):
        candidate_id, _ = self.acquire_run()
        with self.assertRaisesRegex(self.release_update.UpdateError, "probe timeout is out of range"):
            self.claim_execution(candidate_id, probe_timeout=0.1)

    def test_docker_contract_binds_exact_sandbox_and_no_host_env(self):
        harness = load_harness()
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        argv = harness.docker_run_argv(spec, "/nonexistent/activation/source", self._labels())
        joined = " ".join(argv)
        for required in [
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "no-new-privileges:true", "--pids-limit", "--memory", "--memory-swap",
            "--cpus", "--ipc", "none", "--user", "--tmpfs", "--workdir", "/scratch",
            "--pull=never", "--detach", "--log-driver", "none",
            "--entrypoint", "/usr/bin/env",
        ]:
            self.assertIn(required, joined)
        self.assertIn("/candidate:ro", joined)
        self.assertEqual(spec["logDriver"], "none")
        self.assertEqual(spec["entrypoint"], "/usr/bin/env")
        self.assertEqual(spec["argv"][0], "-i")
        self.assertEqual(argv[-1], spec["probe"])
        self.assertEqual(argv[-2], "/bin/bash")
        self.assertNotIn(" -e ", " " + joined + " ")
        self.assertEqual(
            joined.count("/usr/bin/env"),
            1,  # no duplicated /usr/bin/env
        )
        for forbidden in [
            "--privileged", "docker.sock", "SSH_AUTH_SOCK", "DOCKER_HOST",
            "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
        ]:
            self.assertNotIn(forbidden, joined.upper())
        self.assertIn("HOME=/nonexistent", joined)
        self.assertIn("PIXEL_QUALIFICATION_CUSTODY=1", joined)
        self.assertEqual(spec["network"], "none")

    def test_docker_contract_derives_command_from_spec_not_unbound_input(self):
        harness = load_harness()
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        argv = harness.docker_run_argv(spec, "/x", self._labels())
        self.assertEqual(argv[-1], spec["probe"])
        self.assertEqual(spec["entrypoint"], "/usr/bin/env")
        self.assertEqual(spec["argv"], [
            "-i",
            "PATH=/usr/bin:/bin", "HOME=/nonexistent", "LANG=C.UTF-8",
            "PIXEL_QUALIFICATION_CUSTODY=1",
            "/bin/bash", spec["probe"],
        ])
        self.assertEqual(argv[argv.index("--entrypoint") + 1], "/usr/bin/env")

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_docker_sandbox_timeout_kills_container_and_proves_absence(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        start_bytes = self._start_bytes(candidate_id)
        result = harness.run_docker_sandbox(spec, str(source_root), start_bytes)
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["exitCode"], 91)
        harness.cleanup_docker(
            spec["runtime"]["containerName"], self._expected(spec["runtime"]["containerName"]),
        )
        self.assertTrue(harness.container_absent(spec["runtime"]["containerName"]))

    def test_docker_cleanup_ambiguity_raises_when_absence_unproven(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ef" * 10)
        cid = "ab" * 32
        owned = json.loads(self._owned_record(name, container_id=cid))[0]
        with mock.patch.object(harness, "inspect_container_by_id", return_value=owned):
            with mock.patch.object(harness, "kill_container"):
                with mock.patch.object(harness, "container_absent", return_value=False):
                    with self.assertRaises(harness.SandboxError):
                        harness.cleanup_docker(name, self._expected(name), container_id=cid)

    def _fake_inspect(self, *, returncode, stdout, stderr):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)

    def _inject_inspect(self, harness, result):
        return mock.patch.object(harness.subprocess, "run", return_value=result)

    def test_container_absent_proves_only_exact_no_such_contract(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cases = [
            (0, "[{}]", "", False),                  # container exists
            (1, "[]", f"Error response from daemon: No such container: {name}", True),  # exact absence
        ]
        for returncode, stdout, stderr, expected in cases:
            with self._inject_inspect(harness, self._fake_inspect(returncode=returncode, stdout=stdout, stderr=stderr)):
                self.assertIs(harness.container_absent(name), expected)

    def test_container_absent_daemon_unavailable_fails_closed(self):
        harness = load_harness()
        with mock.patch.object(harness.subprocess, "run", side_effect=OSError("daemon down")):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent("pixel-qual-exec-" + ("ab" * 10))

    def test_container_absent_permission_denied_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        with self._inject_inspect(harness, self._fake_inspect(returncode=1, stdout="[]", stderr="permission denied")):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent(name)

    def test_container_absent_malformed_output_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=1, stdout="garbage", stderr=f"No such container: {name}",
        )):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent(name)
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=1, stdout="[]", stderr=f"No such container: {name}\nextra",
        )):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent(name)  # stderr must be exact, no substring match
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=2, stdout="[]", stderr=f"Error response from daemon: No such container: {name}",
        )):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent(name)  # arbitrary nonzero is ambiguity

    def test_container_absent_timeout_fails_closed(self):
        harness = load_harness()
        with mock.patch.object(harness.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 30)):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent("pixel-qual-exec-" + ("ab" * 10))

    def test_container_absent_wrong_name_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=1, stdout="[]", stderr="Error response from daemon: No such container: other-name",
        )):
            with self.assertRaises(harness.SandboxError):
                harness.container_absent(name)

    def test_validate_ownership_exact_labels_returns_id(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        expected = self._expected(name)
        record = json.loads(self._owned_record(name))[0]
        self.assertEqual(harness.validate_ownership(record, name, expected), "ab" * 32)

    def test_validate_ownership_foreign_labels_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        foreign = dict(self._labels())
        foreign["pixel.qualification.attemptId"] = "ff" * 16
        record = json.loads(self._owned_record(name, labels=foreign))[0]
        with self.assertRaises(harness.SandboxError):
            harness.validate_ownership(record, name, self._expected(name))

    def test_validate_ownership_missing_labels_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        record = json.loads(self._owned_record(name, labels={}))[0]
        with self.assertRaises(harness.SandboxError):
            harness.validate_ownership(record, name, self._expected(name))

    def test_validate_ownership_wrong_image_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        record = json.loads(self._owned_record(
            name, image="debian:other", image_digest="sha256:" + "00" * 32,
        ))[0]
        with self.assertRaises(harness.SandboxError):
            harness.validate_ownership(record, name, self._expected(name))

    def test_validate_ownership_wrong_spec_start_candidate_label_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        expected = self._expected(name)
        for key in ("pixel.qualification.executionSpecSha256",
                    "pixel.qualification.executionStartSha256",
                    "pixel.qualification.candidateId"):
            bad = dict(self._labels())
            bad[key] = "00" * 32 if "Sha" in key or "Id" in key else "zz"
            record = json.loads(self._owned_record(name, labels=bad))[0]
            with self.assertRaises(harness.SandboxError):
                harness.validate_ownership(record, name, expected)

    def test_validate_ownership_bad_id_grammar_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        record = json.loads(self._owned_record(name, container_id="zz"))[0]
        with self.assertRaises(harness.SandboxError):
            harness.validate_ownership(record, name, self._expected(name))

    def test_inspect_container_by_id_returns_owned_record(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cid = "ab" * 32
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=0, stdout=self._owned_record(name, container_id=cid), stderr="",
        )):
            record = harness.inspect_container_by_id(cid)
            self.assertEqual(record["Id"], cid)

    def test_inspect_container_by_id_mismatched_id_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cid = "ab" * 32
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=0, stdout=self._owned_record(name, container_id="cd" * 32), stderr="",
        )):
            with self.assertRaises(harness.SandboxError):
                harness.inspect_container_by_id(cid)

    def test_inspect_container_by_id_bad_grammar_fails_closed(self):
        harness = load_harness()
        with self.assertRaises(harness.SandboxError):
            harness.inspect_container_by_id("not-a-64-hex-id")

    def test_inspect_container_by_id_absent_returns_none(self):
        harness = load_harness()
        cid = "ab" * 32
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=1, stdout="[]",
            stderr=f"Error response from daemon: No such container: {cid}",
        )):
            self.assertIsNone(harness.inspect_container_by_id(cid))

    def test_inspect_container_by_name_absent_returns_none(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=1, stdout="[]",
            stderr=f"Error response from daemon: No such container: {name}",
        )):
            self.assertIsNone(harness.inspect_container_by_name(name))

    def test_parse_detach_id_exact(self):
        harness = load_harness()
        cid = "ab" * 32
        self.assertEqual(harness.parse_detach_id(cid + "\n"), cid)

    def test_parse_detach_id_malformed_fails_closed(self):
        harness = load_harness()
        cid = "ab" * 32
        for bad in ("", "zz" * 32, "abc", cid.upper(), cid[:32]):
            with self.assertRaises(harness.SandboxError):
                harness.parse_detach_id(bad)

    def test_parse_detach_id_two_ids_or_extra_fails_closed(self):
        harness = load_harness()
        cid = "ab" * 32
        for bad in (cid + "\n" + cid, cid + " extra", "prefix " + cid):
            with self.assertRaises(harness.SandboxError):
                harness.parse_detach_id(bad)

    def test_cleanup_refuses_foreign_container_and_leaves_untouched(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        with mock.patch.object(harness, "inspect_container_by_name", side_effect=harness.SandboxError("foreign")):
            with mock.patch.object(harness, "kill_container") as kill:
                with self.assertRaises(harness.SandboxError):
                    harness.cleanup_docker(name, self._expected(name))
                kill.assert_not_called()

    def test_cleanup_accepts_rm_error_when_absence_proven(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cid = "ab" * 32
        owned = json.loads(self._owned_record(name, container_id=cid))[0]
        with mock.patch.object(harness, "inspect_container_by_id", return_value=owned):
            with mock.patch.object(harness, "container_absent", return_value=True):
                with mock.patch.object(harness, "kill_container"):
                    with mock.patch.object(
                        harness.subprocess, "run",
                        return_value=self._fake_inspect(returncode=1, stdout="", stderr="rm failed"),
                    ):
                        harness.cleanup_docker(name, self._expected(name), container_id=cid)  # must not raise

    def test_preflight_existing_container_writes_no_start_marker(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        args = argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
        )
        ru = harness.load_release_update()
        run_dir = self.run_dir(candidate_id)
        with mock.patch.object(harness, "verify_docker_image", return_value=None):
            with mock.patch.object(harness, "container_absent", return_value=False):
                with self.assertRaisesRegex(ru.UpdateError, "already occupied"):
                    harness._run(ru, args)
        self.assertFalse(
            (run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE).exists(),
        )
        self.assertFalse(
            (run_dir / self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE).exists(),
        )

    def test_run_docker_sandbox_collision_after_absence_fails_closed(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        name = spec["runtime"]["containerName"]
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        collision = self._fake_inspect(returncode=125, stdout="", stderr="Conflict. name in use")
        foreign_record = self._fake_inspect(
            returncode=0, stdout=self._owned_record(name, labels={}), stderr="",
        )
        with mock.patch.object(harness, "kill_container") as kill:
            with mock.patch.object(
                harness.subprocess, "run",
                side_effect=[collision, foreign_record, foreign_record],
            ):
                with self.assertRaises(harness.SandboxError):
                    harness.run_docker_sandbox(
                        spec, str(source_root), self._start_bytes(candidate_id),
                    )
            kill.assert_not_called()  # foreign container must never be mutated

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_run_docker_sandbox_cleanup_owned_after_start_attempt(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        start_bytes = self._start_bytes(candidate_id)
        with mock.patch.object(harness, "cleanup_docker", wraps=harness.cleanup_docker) as cleanup:
            harness.run_docker_sandbox(spec, str(source_root), start_bytes)
            cleanup.assert_called_once()

    def test_cleanup_mutates_by_id_not_name(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cid = "ab" * 32
        owned = json.loads(self._owned_record(name, container_id=cid))[0]
        with mock.patch.object(harness, "inspect_container_by_id", return_value=owned):
            with mock.patch.object(harness, "container_absent", return_value=True):
                with mock.patch.object(harness, "kill_container") as kill:
                    with mock.patch.object(harness.subprocess, "run") as run:
                        harness.cleanup_docker(name, self._expected(name), container_id=cid)
        kill.assert_called_once_with(cid)
        rm_argv = next(c.args[0] for c in run.call_args_list if "rm" in c.args[0])
        self.assertEqual(rm_argv, [harness.DOCKER, "rm", "-f", cid])
        self.assertNotIn(name, rm_argv)

    def test_cleanup_foreign_replacement_after_owned_removal_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cid = "ab" * 32
        owned = json.loads(self._owned_record(name, container_id=cid))[0]
        with mock.patch.object(harness, "inspect_container_by_id", return_value=owned):
            with mock.patch.object(harness, "kill_container") as kill:
                with mock.patch.object(harness, "container_absent", return_value=False):
                    with mock.patch.object(
                        harness.subprocess, "run",
                        return_value=self._fake_inspect(returncode=0, stdout="", stderr=""),
                    ):
                        with self.assertRaises(harness.SandboxError):
                            harness.cleanup_docker(name, self._expected(name), container_id=cid)
                kill.assert_called_once_with(cid)

    def test_cleanup_inspect_mismatched_id_fails_closed(self):
        harness = load_harness()
        name = "pixel-qual-exec-" + ("ab" * 10)
        cid = "ab" * 32
        with self._inject_inspect(harness, self._fake_inspect(
            returncode=0, stdout=self._owned_record(name, container_id="cd" * 32), stderr="",
        )):
            with mock.patch.object(harness, "kill_container") as kill:
                with self.assertRaises(harness.SandboxError):
                    harness.cleanup_docker(name, self._expected(name), container_id=cid)
            kill.assert_not_called()

    def test_run_uses_id_for_wait_kill_rm(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        name = spec["runtime"]["containerName"]
        cid = "ab" * 32
        start_bytes = self._start_bytes(candidate_id)
        owned = json.loads(self._owned_for_start(name, start_bytes, container_id=cid))[0]
        run_result = self._fake_inspect(returncode=0, stdout=cid + "\n", stderr="")
        wait_result = self._fake_inspect(returncode=0, stdout="91\n", stderr="")
        rm_result = self._fake_inspect(returncode=0, stdout="", stderr="")
        with mock.patch.object(harness, "inspect_container_by_id", return_value=owned):
            with mock.patch.object(harness, "container_absent", return_value=True):
                with mock.patch.object(harness, "kill_container") as kill:
                    with mock.patch.object(
                        harness.subprocess, "run",
                        side_effect=[run_result, wait_result, rm_result],
                    ) as run:
                        result = harness.run_docker_sandbox(
                            spec, str(source_root), start_bytes,
                        )
        kill.assert_called_once_with(cid)
        wait_argv = next(c.args[0] for c in run.call_args_list if c.args[0][1] == "wait")
        self.assertEqual(wait_argv, [harness.DOCKER, "wait", cid])
        rm_argv = next(c.args[0] for c in run.call_args_list if "rm" in c.args[0])
        self.assertEqual(rm_argv, [harness.DOCKER, "rm", "-f", cid])
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["exitCode"], 91)

    def test_run_timeout_recovers_owned_container_by_id(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        name = spec["runtime"]["containerName"]
        cid = "ab" * 32
        start_bytes = self._start_bytes(candidate_id)
        owned = json.loads(self._owned_for_start(name, start_bytes, container_id=cid))[0]
        rm_result = self._fake_inspect(returncode=0, stdout="", stderr="")
        with mock.patch.object(harness, "inspect_container_by_name", return_value=owned):
            with mock.patch.object(harness, "container_absent", return_value=True):
                with mock.patch.object(harness, "kill_container") as kill:
                    with mock.patch.object(
                        harness.subprocess, "run",
                        side_effect=[subprocess.TimeoutExpired("docker", 90), rm_result],
                    ):
                        with self.assertRaises(harness.SandboxError):
                            harness.run_docker_sandbox(
                                spec, str(source_root), start_bytes,
                            )
                kill.assert_called_once_with(cid)

    def test_run_timeout_foreign_collision_fails_closed(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        name = spec["runtime"]["containerName"]
        foreign = json.loads(self._owned_record(name, labels={}, container_id="cd" * 32))[0]
        with mock.patch.object(harness, "inspect_container_by_name", return_value=foreign):
            with mock.patch.object(harness, "kill_container") as kill:
                with mock.patch.object(
                    harness.subprocess, "run",
                    side_effect=[subprocess.TimeoutExpired("docker", 90)],
                ):
                    with self.assertRaises(harness.SandboxError):
                        harness.run_docker_sandbox(
                            spec, str(source_root), self._start_bytes(candidate_id),
                        )
                kill.assert_not_called()

    def test_run_oserror_before_invocation_skips_cleanup_and_still_raises(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        start_bytes = self._start_bytes(candidate_id)
        with mock.patch.object(harness, "cleanup_docker") as cleanup:
            with mock.patch.object(harness, "inspect_container_by_name") as inspect_name:
                with mock.patch.object(harness, "kill_container") as kill:
                    with mock.patch.object(
                        harness.subprocess, "run", side_effect=OSError("docker absent"),
                    ):
                        with self.assertRaises(harness.SandboxError):
                            harness.run_docker_sandbox(spec, str(source_root), start_bytes)
                    kill.assert_not_called()
                inspect_name.assert_not_called()
            cleanup.assert_not_called()

    def test_run_malformed_detach_id_fails_closed(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self.qualification_root / "activations" / candidate_id / "source"
        name = spec["runtime"]["containerName"]
        cid = "ab" * 32
        start_bytes = self._start_bytes(candidate_id)
        owned = json.loads(self._owned_for_start(name, start_bytes, container_id=cid))[0]
        run_result = self._fake_inspect(returncode=0, stdout=cid + "\nextra", stderr="")
        rm_result = self._fake_inspect(returncode=0, stdout="", stderr="")
        with mock.patch.object(harness, "inspect_container_by_name", return_value=owned):
            with mock.patch.object(harness, "container_absent", return_value=True):
                with mock.patch.object(harness, "kill_container") as kill:
                    with mock.patch.object(
                        harness.subprocess, "run",
                        side_effect=[run_result, rm_result],
                    ):
                        with self.assertRaises(harness.SandboxError):
                            harness.run_docker_sandbox(
                                spec, str(source_root), start_bytes,
                            )
                kill.assert_called_once_with(cid)


    def _labels(self):
        return {
            "pixel.qualification.attemptId": "ab" * 16,
            "pixel.qualification.candidateId": "pixel-1.2.3-" + ("ab" * 32),
            "pixel.qualification.executionSpecSha256": "cd" * 32,
            "pixel.qualification.executionStartSha256": "ef" * 32,
        }

    def _expected(self, name, labels=None):
        return {
            "image": "debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
            "imageDigest": "sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
            "labels": labels if labels is not None else self._labels(),
        }

    def _owned_record(self, name, labels=None, image=None, image_digest=None, container_id=None):
        record = {
            "Id": container_id if container_id is not None else "ab" * 32,
            "Name": "/" + name,
            "Image": image_digest or "sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
            "Config": {
                "Image": image or "debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
                "Labels": labels if labels is not None else self._labels(),
            },
        }
        return json.dumps([record])
    def _owned_for_start(self, name, start_bytes, container_id=None):
        start = json.loads(start_bytes)
        labels = {
            "pixel.qualification.attemptId": start["attemptId"],
            "pixel.qualification.candidateId": start["candidateId"],
            "pixel.qualification.executionSpecSha256": start["executionSpecSha256"],
            "pixel.qualification.executionStartSha256": hashlib.sha256(start_bytes).hexdigest(),
        }
        return self._owned_record(name, labels=labels, container_id=container_id)


    def _start_bytes(self, candidate_id):
        run_dir = self.run_dir(candidate_id)
        spec_path = run_dir / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec_bytes = spec_path.read_bytes()
        spec = json.loads(spec_bytes)
        claim_bytes = self.claim_path(candidate_id).read_bytes()
        start = self.release_update.qualification_execution_start_value(
            {"candidate_id": candidate_id}, claim_bytes, spec_bytes, spec, "ab" * 16,
        )
        return self.release_update.canonical_json(start)

    def test_result_recorder_rejects_observation_not_closing_spec_hash(self):
        candidate_id = self.claimed_run()
        bad = dict(self.observation(candidate_id))
        bad["executionSpecSha256"] = "00" * 32
        with self.assertRaisesRegex(self.release_update.UpdateError, "does not close the execution spec hash"):
            self.record_result(candidate_id, bad)
        self.assertFalse(self.result_path(candidate_id).exists())

    def test_result_recorder_rejects_tampered_spec(self):
        candidate_id = self.claimed_run()
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["timeoutSeconds"] = 999.0
        spec_path.write_text(self.release_update.canonical_json(spec).decode("utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the revalidated host-run"):
            self.record_result(candidate_id, self.observation(candidate_id))

    def test_result_recorder_rejects_deleted_spec_or_start(self):
        candidate_id = self.claimed_run()
        run_dir = self.run_dir(candidate_id)
        run_dir.mkdir(exist_ok=True)
        start_path = run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE
        if not start_path.exists():
            self.write_start_marker(candidate_id)
        (run_dir / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE).unlink()
        with self.assertRaisesRegex(self.release_update.UpdateError, "spec|file set"):
            self.release_update.qualification_execution_result(
                self.result_arguments(candidate_id, None, confirm=True),
            )

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_result_hash_closure_end_to_end(self):
        candidate_id = self.claimed_run()
        result = self.run_harness(candidate_id)
        self.assertEqual(result.returncode, 0)
        recorded = self.release_update.qualification_execution_result(
            self.result_arguments(candidate_id, None, confirm=True),
        )
        self.assertEqual(recorded["status"], "recorded")
        self.assertEqual(recorded["outcome"], "failure")
        result_file = self.read_json(self.result_path(candidate_id))
        run_dir = self.run_dir(candidate_id)
        self.assertEqual(
            result_file["executionSpecSha256"],
            self.release_update.sha256(
                (run_dir / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE).read_bytes(),
            ),
        )
        self.assertEqual(
            result_file["executionStartSha256"],
            self.release_update.sha256(
                (run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE).read_bytes(),
            ),
        )

    def test_validate_execution_spec_rejects_tampering_by_group(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        args = self.run_arguments(candidate_id)
        state = self.release_update.qualification_execution_state(
            args,
            activation_files=set(self.release_update.QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(self.release_update.QUALIFICATION_RUN_CLAIMED_FILES),
        )
        claim_path = state["activation"] / self.release_update.QUALIFICATION_EXECUTION_CLAIM_FILE
        claim = self.release_update.parse_json(claim_path.read_bytes(), "claim")
        spec_path = state["run_dir"] / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        base = json.loads(spec_path.read_bytes())

        def assert_rejected(mutator):
            spec = json.loads(self.release_update.canonical_json(base))
            mutator(spec)
            tampered = self.release_update.canonical_json(spec)
            with self.assertRaisesRegex(
                self.release_update.UpdateError, "differs from the revalidated host-run",
            ):
                self.release_update.qualification_validate_execution_spec(
                    state, tampered, claim,
                )

        assert_rejected(lambda s: s["runtime"].update({"image": "debian:other"}))
        assert_rejected(lambda s: s["runtime"].update({"imageDigest": "sha256:" + "00" * 64}))
        assert_rejected(lambda s: s["environment"].update({"EXTRA": "x"}))
        assert_rejected(lambda s: s["environment"].pop("HOME"))
        assert_rejected(lambda s: s.update({"network": "host"}))
        assert_rejected(lambda s: s.update({"readOnlyRoot": False}))
        assert_rejected(lambda s: s.update({"capDropAll": False}))
        assert_rejected(lambda s: s.update({"noNewPrivileges": False}))
        assert_rejected(lambda s: s.update({"logDriver": "json-file"}))
        assert_rejected(lambda s: s.update({"pidsLimit": 999}))
        assert_rejected(lambda s: s.update({"memory": "1g"}))
        assert_rejected(lambda s: s.update({"cpus": "2"}))
        assert_rejected(lambda s: s.update({"scratch": "/scratch:rw,size=64m,mode=0777,uid=1,gid=1"}))
        assert_rejected(lambda s: s.update({"boundary": "tampered"}))
        assert_rejected(lambda s: s.update({"argv": ["/bin/bash", s["probe"]]}))
        assert_rejected(lambda s: s.update({"sourceTreeSha256": "00" * 32}))
        assert_rejected(lambda s: s.update({"probeSha256": "11" * 32}))
        assert_rejected(lambda s: s.update({"timeoutSeconds": 999.0}))
        assert_rejected(lambda s: s.update({"probeRelativePath": "scripts/other.sh"}))
        assert_rejected(lambda s: s.update({"probe": "/candidate/scripts/other.sh"}))

    def test_schema_image_pattern_cannot_widen_sandbox_authority(self):
        spec_schema = json.loads(
            (ROOT / "schemas" / "release-update-qualification-execution-spec-v1.schema.json").read_text(encoding="utf-8"),
        )
        image_pattern = spec_schema["properties"]["runtime"]["properties"]["image"]["pattern"]
        digest_pattern = spec_schema["properties"]["runtime"]["properties"]["imageDigest"]["pattern"]
        obs_schema = json.loads(
            (ROOT / "schemas" / "release-update-qualification-execution-observation-v1.schema.json").read_text(encoding="utf-8"),
        )
        obs_image_pattern = obs_schema["properties"]["sandbox"]["properties"]["image"]["pattern"]
        # The schema grammar admits any digest-pinned image, not only the pinned
        # base image, so a different-but-grammar-valid image still passes shape.
        alternate_image = "debian:bookworm-slim@sha256:" + "11" * 32
        alternate_digest = "sha256:" + "11" * 32
        self.assertIsNotNone(re.fullmatch(image_pattern, alternate_image))
        self.assertIsNotNone(re.fullmatch(digest_pattern, alternate_digest))
        self.assertIsNotNone(re.fullmatch(obs_image_pattern, alternate_image))
        # Runtime exact-identity enforcement still fails closed on a schema-valid
        # but different image, so the schema shape cannot widen sandbox authority.
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        args = self.run_arguments(candidate_id)
        state = self.release_update.qualification_execution_state(
            args,
            activation_files=set(self.release_update.QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(self.release_update.QUALIFICATION_RUN_CLAIMED_FILES),
        )
        claim_path = state["activation"] / self.release_update.QUALIFICATION_EXECUTION_CLAIM_FILE
        claim = self.release_update.parse_json(claim_path.read_bytes(), "claim")
        spec_path = state["run_dir"] / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_bytes())
        spec["runtime"]["image"] = alternate_image
        spec["runtime"]["imageDigest"] = alternate_digest
        tampered = self.release_update.canonical_json(spec)
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "differs from the revalidated host-run",
        ):
            self.release_update.qualification_validate_execution_spec(state, tampered, claim)

    def test_missing_image_or_daemon_writes_no_start_marker(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        args = argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
        )
        ru = harness.load_release_update()
        with mock.patch.object(
            harness, "verify_docker_image", side_effect=harness.SandboxError("image unavailable"),
        ):
            with self.assertRaises(harness.SandboxError):
                harness._run(ru, args)
        run_dir = self.run_dir(candidate_id)
        self.assertFalse(
            (run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE).exists(),
        )
        self.assertFalse(
            (run_dir / self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE).exists(),
        )

    def test_harness_holds_lock_during_docker_execution_and_observation(self):
        harness = load_harness()
        candidate_id = self.claimed_run()
        args = argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
        )
        ru = harness.load_release_update()
        real_write_private = ru.write_private
        held = {"during": False, "observation": False}
        observation_path = self.run_dir(candidate_id) / ru.QUALIFICATION_EXECUTION_OBSERVATION_FILE

        def fake_run_docker_sandbox(spec, source_root, start_bytes):
            # The exclusive qualification-root lock must be held while the
            # candidate Docker probe executes.
            try:
                with ru.exclusive_stage_lock(self.qualification_root):
                    held["during"] = False
            except ru.UpdateError:
                held["during"] = True
            return {"outcome": "failure", "phase": "probe", "reason": "non-zero-exit",
                    "exitCode": 1, "signal": None, "timedOut": False}

        def fake_write_private(path, payload):
            # The exclusive qualification-root lock must still be held while the
            # observation is published right after; a nested acquisition must be
            # rejected.
            if path == observation_path:
                try:
                    with ru.exclusive_stage_lock(self.qualification_root):
                        held["observation"] = False
                except ru.UpdateError:
                    held["observation"] = True
            real_write_private(path, payload)

        with mock.patch.object(harness, "verify_spec_image", return_value="sha256:" + "ab" * 32), \
             mock.patch.object(harness, "verify_docker_image", return_value=None), \
             mock.patch.object(harness, "container_absent", return_value=True), \
             mock.patch.object(harness, "run_docker_sandbox", side_effect=fake_run_docker_sandbox), \
             mock.patch.object(ru, "write_private", side_effect=fake_write_private):
            result = harness._run(ru, args)
        self.assertTrue(held["during"], "lock must be held during Docker execution")
        self.assertTrue(held["observation"], "lock must be held during observation publication")
        self.assertTrue(observation_path.is_file())
        self.assertEqual(result["candidateCodeExecuted"], True)

    ENV_PROBE = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "# The candidate must see exactly the claim-bound environment; fail\n"
        "# if any unexpected variable leaks in from the host or image.\n"
        '[ "${PATH:-}" = "/usr/bin:/bin" ] || exit 92\n'
        '[ "${HOME:-}" = "/nonexistent" ] || exit 93\n'
        '[ "${LANG:-}" = "C.UTF-8" ] || exit 94\n'
        '[ "${PIXEL_QUALIFICATION_CUSTODY:-}" = "1" ] || exit 95\n'
        "while IFS='=' read -r key _value; do\n"
        '    case "$key" in\n'
        "        PATH|HOME|LANG|PIXEL_QUALIFICATION_CUSTODY|PWD|SHLVL|_) ;;\n"
        "        *) exit 96 ;;\n"
        "    esac\n"
        "done < <(env)\n"
        "exit 0\n"
    )

    def _env_probe_source(self):
        source_root = Path(self.temporary.name) / "env-probe-source"
        (source_root / "scripts").mkdir(parents=True, exist_ok=True)
        probe = source_root / "scripts" / "safe.sh"
        probe.write_text(self.ENV_PROBE, encoding="utf-8")
        return source_root

    @unittest.skipUnless(docker_capable(), "requires real Docker integration")
    def test_env_wrapper_real_probe_rejects_unexpected_variables(self):
        harness = load_harness()
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        spec_path = self.run_dir(candidate_id) / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        source_root = self._env_probe_source()
        name = spec["runtime"]["containerName"]
        argv = harness.docker_run_argv(spec, str(source_root), self._labels())
        self.assertEqual(argv[-1], "/candidate/scripts/safe.sh")
        started = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        self.assertEqual(started.returncode, 0, started.stderr)
        try:
            wait = subprocess.run(
                [harness.DOCKER, "wait", name], capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(wait.returncode, 0, wait.stderr)
            self.assertEqual(int(wait.stdout.strip()), 0)
        finally:
            harness.cleanup_docker(name, self._expected(name))

    def test_env_probe_fails_when_unexpected_variable_injected(self):
        harness = load_harness()
        source_root = self._env_probe_source()
        probe = source_root / "scripts" / "safe.sh"
        base = {
            "PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
            "LANG": "C.UTF-8", "PIXEL_QUALIFICATION_CUSTODY": "1",
        }
        clean = subprocess.run(["bash", str(probe)], env=base, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0)
        leaked = dict(base)
        leaked["HOSTNAME"] = "leak"
        dirty = subprocess.run(["bash", str(probe)], env=leaked, capture_output=True, text=True)
        self.assertNotEqual(dirty.returncode, 0)

    def test_docker_capable_requires_local_pinned_image(self):
        module = sys.modules[type(self).__module__]
        image, digest = module.pinned_sandbox_image()
        repo = image.split("@", 1)[0]
        docker_capable.cache_clear()
        try:
            with mock.patch.object(module.os.path, "isfile", return_value=True), \
                 mock.patch.object(module.os, "access", return_value=True):
                present = mock.Mock(
                    returncode=0,
                    stdout=json.dumps([{
                        "Id": "sha256:" + "ab" * 32,
                        "RepoDigests": [f"{repo}@{digest}"],
                    }]),
                )
                with mock.patch.object(module.subprocess, "run", return_value=present):
                    self.assertTrue(docker_capable())

                docker_capable.cache_clear()
                absent = mock.Mock(
                    returncode=1, stdout="", stderr="Error response from daemon: No such image",
                )
                with mock.patch.object(module.subprocess, "run", return_value=absent):
                    self.assertFalse(docker_capable())

                docker_capable.cache_clear()
                wrong = mock.Mock(
                    returncode=0,
                    stdout=json.dumps([{"Id": "sha256:" + "11" * 32, "RepoDigests": []}]),
                )
                with mock.patch.object(module.subprocess, "run", return_value=wrong):
                    self.assertFalse(docker_capable())
        finally:
            docker_capable.cache_clear()
        self.assertEqual(image, f"{repo}@{digest}")

    def test_docker_capable_missing_or_non_executable_binary_returns_false(self):
        module = sys.modules[type(self).__module__]
        for isfile, access in ((False, False), (True, False)):
            with self.subTest(isfile=isfile, access=access):
                docker_capable.cache_clear()
                try:
                    with mock.patch.object(module.os.path, "isfile", return_value=isfile), \
                         mock.patch.object(module.os, "access", return_value=access), \
                         mock.patch.object(module.subprocess, "run") as run:
                        self.assertFalse(docker_capable())
                        run.assert_not_called()
                finally:
                    docker_capable.cache_clear()


if __name__ == "__main__":
    unittest.main()
