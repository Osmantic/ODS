"""Focused qualification-execution-interruption slice tests.

These cover the honest post-START infrastructure-failure dead end: a distinct
immutable EXECUTION-INTERRUPTION.json terminal artifact and a private
QUALIFICATION-EXECUTION-INTERRUPTION.json activation marker that model candidate
execution as indeterminate, grant no promotion/result/observation authority,
are structurally impossible to consume as EXECUTION-RESULT.json, and never
retry indeterminate candidate execution.
"""

import argparse
import functools
import json
import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest import mock

from tests.test_release_update import QualificationExecutionTests, ROOT
from tests.test_qualification_host_execution import load_harness

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
    locally, so the real-Docker custody integration test runs without any
    network pull; otherwise it skips without pulling."""
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



class QualificationExecutionInterruptionTests(QualificationExecutionTests):
    def acquire_started_run(self):
        candidate_id, activation_hash = self.acquire_run()
        self.claim_execution(candidate_id)
        self.write_start_marker(candidate_id)
        return candidate_id, activation_hash

    def interruption_args(self, candidate_id, interruption_hash, confirm=True):
        return argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
            interruption_hash=interruption_hash,
            confirm=confirm,
        )

    def interruption_path(self, candidate_id):
        return self.qualification_root / "runs" / candidate_id / self.release_update.QUALIFICATION_EXECUTION_INTERRUPTION_FILE

    def interruption_marker_path(self, candidate_id):
        return self.qualification_root / "activations" / candidate_id / self.release_update.QUALIFICATION_EXECUTION_INTERRUPTION_MARKER

    def preview(self, candidate_id):
        return self.release_update.qualification_execution_interruption_preview(
            self.interruption_args(candidate_id, "ab" * 32),
        )

    def record_interruption(self, candidate_id, interruption_hash, confirm=True):
        # Command-level artifact/marker logic is tested without a live Docker
        # daemon: custody absence is mocked here and the custody logic itself is
        # unit-tested directly with a mocked subprocess runner.
        with mock.patch.object(
            self.release_update, "qualification_execution_interruption_custody",
            return_value={"containerAbsent": True, "containerRemoved": False},
        ):
            return self.release_update.qualification_execution_interruption_record(
                self.interruption_args(candidate_id, interruption_hash, confirm=confirm),
            )

    def test_preview_is_inert_and_confirmed_record_writes_only_two_private_artifacts(self):
        candidate_id, activation_hash = self.acquire_started_run()
        run_dir = self.qualification_root / "runs" / candidate_id
        activation = self.qualification_root / "activations" / candidate_id
        before_run = {path.name for path in run_dir.iterdir()}
        before_activation = {path.name for path in activation.iterdir()}
        preview = self.preview(candidate_id)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["candidateExecutionState"], "indeterminate")
        self.assertEqual(preview["reason"], "post-start-infrastructure-failure")
        self.assertRegex(preview["interruptionHash"], r"^[0-9a-f]{64}$")
        self.assert_qualification_authority(preview)
        self.assertEqual({path.name for path in run_dir.iterdir()}, before_run)
        self.assertEqual({path.name for path in activation.iterdir()}, before_activation)
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.record_interruption(candidate_id, preview["interruptionHash"], confirm=False)
        with self.assertRaisesRegex(self.release_update.UpdateError, "hash differs"):
            self.record_interruption(candidate_id, "ab" * 32)
        recorded = self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertEqual(recorded["status"], "recorded")
        self.assertEqual(recorded["candidateExecutionState"], "indeterminate")
        self.assertEqual(recorded["reason"], "post-start-infrastructure-failure")
        self.assertTrue(recorded["containerAbsent"])
        self.assertFalse(recorded["containerRemoved"])
        self.assert_qualification_authority(recorded)
        self.assertFalse(recorded["terminalPromotionEvidence"])
        artifact_path = self.interruption_path(candidate_id)
        marker_path = self.interruption_marker_path(candidate_id)
        self.assertTrue(artifact_path.is_file())
        self.assertTrue(marker_path.is_file())
        self.assert_private_single_link(artifact_path)
        self.assert_private_single_link(marker_path)
        self.assertEqual(
            {path.name for path in run_dir.iterdir()},
            {
                "HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json",
                self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE,
                self.release_update.QUALIFICATION_EXECUTION_START_FILE,
                self.release_update.QUALIFICATION_EXECUTION_INTERRUPTION_FILE,
            },
        )
        self.assertEqual(
            {path.name for path in activation.iterdir()},
            {
                "source", "QUALIFICATION-ACTIVATION.json", "QUALIFICATION-ACQUISITION.json",
                self.release_update.QUALIFICATION_EXECUTION_CLAIM_FILE,
                self.release_update.QUALIFICATION_EXECUTION_INTERRUPTION_MARKER,
            },
        )
        artifact = self.read_json(artifact_path)
        marker = self.read_json(marker_path)
        self.assert_schema_shape(
            artifact, "release-update-qualification-execution-interruption-v1.schema.json",
        )
        self.assert_schema_shape(
            marker, "release-update-qualification-execution-interruption-marker-v1.schema.json",
        )
        self.assertEqual(artifact["candidateId"], candidate_id)
        self.assertEqual(artifact["activationHash"], activation_hash)
        self.assertEqual(artifact["status"], "interrupted")
        self.assertEqual(artifact["operation"], "pixel-release-qualification-execution-interruption")
        self.assertEqual(marker["status"], "terminal")
        self.assertEqual(
            marker["executionInterruptionSha256"],
            self.release_update.sha256(self.release_update.canonical_json(artifact)),
        )
        self.assertFalse(artifact["terminalPromotionEvidence"])
        self.assert_qualification_authority(artifact)
        self.assert_qualification_authority(marker)

    def test_candidate_execution_state_is_indeterminate_not_false_success_or_observed(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        artifact = self.read_json(self.interruption_path(candidate_id))
        marker = self.read_json(self.interruption_marker_path(candidate_id))
        self.assertEqual(artifact["candidateExecutionState"], "indeterminate")
        self.assertEqual(marker["candidateExecutionState"], "indeterminate")
        for field in ("candidateCodeExecuted", "executionObserved", "success", "outcome",
                      "exitCode", "signal", "timedOut", "observedVersion", "phase"):
            self.assertNotIn(field, artifact, field)
            self.assertNotIn(field, marker, field)
        self.assertNotIn("stdout", artifact)
        self.assertNotIn("stderr", artifact)

    def test_replay_and_re_execution_are_forbidden_after_interruption(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        with self.assertRaisesRegex(self.release_update.UpdateError, "replay is forbidden"):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        with self.assertRaisesRegex(self.release_update.UpdateError, "automatic re-execution is forbidden"):
            self.claim_execution(candidate_id)
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been acquired"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_untampered_replay_validates_then_rejects(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        artifact_path = self.interruption_path(candidate_id)
        marker_path = self.interruption_marker_path(candidate_id)
        artifact_before = artifact_path.read_bytes()
        marker_before = marker_path.read_bytes()
        with self.assertRaisesRegex(self.release_update.UpdateError, "replay is forbidden"):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertEqual(artifact_path.read_bytes(), artifact_before)
        self.assertEqual(marker_path.read_bytes(), marker_before)

    def test_replay_rejects_tampered_artifact(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        artifact_path = self.interruption_path(candidate_id)
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["status"] = "completed"
        artifact_path.write_text(
            self.release_update.canonical_json(artifact).decode("utf-8"), encoding="utf-8",
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "artifact does not match"):
            self.record_interruption(candidate_id, preview["interruptionHash"])

    def test_replay_rejects_tampered_marker(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        marker_path = self.interruption_marker_path(candidate_id)
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["candidateExecutionState"] = "success"
        marker_path.write_text(
            self.release_update.canonical_json(marker).decode("utf-8"), encoding="utf-8",
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "marker does not match"):
            self.record_interruption(candidate_id, preview["interruptionHash"])

    def test_replay_rejects_extra_terminal_file(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        (self.qualification_root / "activations" / candidate_id / "EXTRA.json").write_text(
            "x\n", encoding="ascii",
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "activation file set is invalid"):
            self.record_interruption(candidate_id, preview["interruptionHash"])


    def test_crash_after_artifact_before_marker_repairs_only_marker_idempotently(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        state = self.release_update.qualification_execution_state(
            self.interruption_args(candidate_id, preview["interruptionHash"]),
            activation_files=set(self.release_update.QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(self.release_update.QUALIFICATION_RUN_STARTED_FILES),
        )
        claim_bytes = self.release_update.read_regular(
            state["activation"] / self.release_update.QUALIFICATION_EXECUTION_CLAIM_FILE,
            self.release_update.MAX_STAGE_RECEIPT, "qualification execution claim",
        )
        claim, spec_bytes, spec = self.release_update.qualification_validate_execution_claim(state, claim_bytes)
        start_bytes = self.release_update.read_regular(
            state["run_dir"] / self.release_update.QUALIFICATION_EXECUTION_START_FILE,
            self.release_update.MAX_STAGE_RECEIPT, "qualification execution start",
        )
        start = self.release_update.qualification_validate_execution_start(
            state, start_bytes, claim, spec,
        )
        recorded_at = "2026-08-24T00:00:00Z"
        artifact = self.release_update.qualification_execution_interruption_value(
            state["harness"], claim_bytes, spec_bytes, start_bytes, spec, start, recorded_at,
        )
        self.release_update.write_private(
            self.interruption_path(candidate_id), self.release_update.canonical_json(artifact),
        )
        self.release_update.fsync_directory(state["run_dir"])
        self.assertTrue(self.interruption_path(candidate_id).is_file())
        self.assertFalse(self.interruption_marker_path(candidate_id).exists())
        recovered = self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertEqual(recovered["status"], "recovered")
        self.assertTrue(self.interruption_marker_path(candidate_id).is_file())
        self.assertEqual(
            self.read_json(self.interruption_marker_path(candidate_id))["executionInterruptionSha256"],
            self.release_update.sha256(self.release_update.canonical_json(artifact)),
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "replay is forbidden"):
            self.record_interruption(candidate_id, preview["interruptionHash"])

    def test_marker_without_artifact_fails_closed(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        harness = json.loads(
            (self.qualification_root / "runs" / candidate_id / "HARNESS-RUN.json").read_bytes(),
        )
        marker = self.release_update.qualification_execution_interruption_marker_value(
            harness, b"claim", b"artifact", "2026-08-24T00:00:00Z",
        )
        self.release_update.write_private(
            self.interruption_marker_path(candidate_id), self.release_update.canonical_json(marker),
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "marker exists without its artifact"):
            self.record_interruption(candidate_id, preview["interruptionHash"])

    def test_interruption_refuses_missing_start(self):
        missing_start, _ = self.acquire_run()
        self.claim_execution(missing_start)
        with self.assertRaisesRegex(self.release_update.UpdateError, "run file set is invalid"):
            self.preview(missing_start)

    def test_interruption_refuses_observation_present(self):
        observed, _ = self.acquire_started_run()
        self.write_observation(self.observation(observed), observed)
        with self.assertRaisesRegex(self.release_update.UpdateError, "run file set is invalid"):
            self.preview(observed)

    def test_interruption_refuses_result_present(self):
        resulted, _ = self.acquire_started_run()
        self.record_result(resulted, self.observation(resulted))
        with self.assertRaisesRegex(self.release_update.UpdateError, "activation file set is invalid"):
            self.preview(resulted)

    def test_interruption_refuses_extra_file(self):
        extra, _ = self.acquire_started_run()
        (self.qualification_root / "runs" / extra / "unexpected").write_text("x\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "run file set is invalid"):
            self.preview(extra)

    def test_interruption_rejects_tampered_claim(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        claim_path = self.claim_path(candidate_id)
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["version"] = "9.9.9"
        claim_path.write_text(self.release_update.canonical_json(claim).decode("utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs"):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertFalse(self.interruption_path(candidate_id).exists())

    def test_interruption_rejects_wrong_hash(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        with self.assertRaisesRegex(self.release_update.UpdateError, "hash differs"):
            self.record_interruption(candidate_id, "ab" * 32)
        self.assertFalse(self.interruption_path(candidate_id).exists())
        self.assertFalse(self.interruption_marker_path(candidate_id).exists())

    def test_interruption_rejects_tampered_spec(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        spec_path = self.qualification_root / "runs" / candidate_id / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["containerName"] = "pixel-qual-exec-" + ("ee" * 10)
        spec_path.write_text(self.release_update.canonical_json(spec).decode("utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs|does not match"):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertFalse(self.interruption_path(candidate_id).exists())

    def test_interruption_rejects_tampered_source(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        activation = self.qualification_root / "activations" / candidate_id
        target = next(
            path for path in (activation / "source").rglob("*")
            if path.is_file() and path.suffix == ".py"
        )
        target.write_bytes(target.read_bytes() + b"\n\n")
        with self.assertRaisesRegex(self.release_update.UpdateError, "source tree differs"):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertFalse(self.interruption_path(candidate_id).exists())

    def test_interruption_rejects_tampered_host(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        harness_path = self.qualification_root / "runs" / candidate_id / "HARNESS-RUN.json"
        harness = json.loads(harness_path.read_text(encoding="utf-8"))
        original_host = harness["host"]
        wrong_host = dict(original_host)
        if original_host.get("id") == "debian":
            wrong_host.update({"id": "ubuntu", "versionId": "24.04", "label": "Ubuntu 24.04 LTS"})
        else:
            wrong_host.update({"id": "debian", "versionId": "12", "label": "Debian 12"})
        harness["host"] = wrong_host
        harness_path.write_text(self.release_update.canonical_json(harness).decode("utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs|does not match"):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        self.assertFalse(self.interruption_path(candidate_id).exists())


    def test_interruption_rejects_permissive_symlink_and_hardlink_artifacts(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        artifact_path = self.interruption_path(candidate_id)
        os.symlink("/etc/hostname", artifact_path)
        with self.assertRaises(self.release_update.UpdateError):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        artifact_path.unlink()
        real = self.qualification_root / "runs" / candidate_id / "HARNESS-RUN.json"
        os.link(real, artifact_path)
        with self.assertRaises(self.release_update.UpdateError):
            self.record_interruption(candidate_id, preview["interruptionHash"])
        artifact_path.unlink()

    def test_interruption_blocks_concurrent_operation(self):
        candidate_id, _ = self.acquire_started_run()
        with self.release_update.exclusive_stage_lock(self.qualification_root):
            with self.assertRaisesRegex(self.release_update.UpdateError, "another release staging operation"):
                self.preview(candidate_id)
            with self.assertRaisesRegex(self.release_update.UpdateError, "another release staging operation"):
                self.record_interruption(candidate_id, "ab" * 32)

    def test_interruption_evidence_is_content_free_and_authority_free(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        recorded = self.record_interruption(candidate_id, preview["interruptionHash"])
        for key in ("containerId", "stdout", "stderr", "path", "containerName"):
            self.assertNotIn(key, recorded, key)
        self.assertNotIn("containerId", self.read_json(self.interruption_path(candidate_id)))
        self.assertNotIn("containerId", self.read_json(self.interruption_marker_path(candidate_id)))
        self.assert_qualification_authority(recorded)
        self.assertFalse(recorded["terminalPromotionEvidence"])

    def _interruption_spec_and_start(self, candidate_id):
        run_dir = self.qualification_root / "runs" / candidate_id
        spec = self.read_json(run_dir / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE)
        start_bytes = (run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE).read_bytes()
        start = json.loads(start_bytes)
        return spec, start_bytes, start

    def _owned_record(self, spec, start_bytes, start, name=None):
        name = name or spec["runtime"]["containerName"]
        return {
            "Id": "ab" * 32,
            "Name": "/" + name,
            "Config": {"Image": spec["runtime"]["image"], "Labels": {
                "pixel.qualification.attemptId": start["attemptId"],
                "pixel.qualification.candidateId": start["candidateId"],
                "pixel.qualification.executionSpecSha256": start["executionSpecSha256"],
                "pixel.qualification.executionStartSha256": self.release_update.sha256(start_bytes),
            }},
            "Image": spec["runtime"]["imageDigest"],
        }

    def test_exact_owned_orphan_container_removed_by_immutable_id_and_absence_proven(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        name = spec["runtime"]["containerName"]
        owned = self._owned_record(spec, start_bytes, start)
        container_id = owned["Id"]
        calls = {"inspect": 0}

        def fake_docker(argv):
            if argv[1] == "container" and argv[2] == "inspect":
                calls["inspect"] += 1
                target = argv[3]
                if target == name:
                    if calls["inspect"] == 1:
                        return mock.Mock(returncode=0, stdout=json.dumps([owned]), stderr="")
                    return mock.Mock(
                        returncode=1, stdout="[]",
                        stderr=f"Error response from daemon: No such container: {name}",
                    )
                self.assertEqual(target, container_id)
                return mock.Mock(
                    returncode=1, stdout="[]",
                    stderr=f"Error response from daemon: No such container: {container_id}",
                )
            if argv[1] == "rm":
                self.assertEqual(argv[3], container_id)
                return mock.Mock(returncode=0, stdout=container_id, stderr="")
            raise AssertionError(f"unexpected docker argv: {argv}")

        with mock.patch.object(
            self.release_update, "_qualification_interruption_docker", side_effect=fake_docker,
        ):
            result = self.release_update.qualification_execution_interruption_custody(
                {}, start_bytes, spec,
            )
        self.assertTrue(result["containerAbsent"])
        self.assertTrue(result["containerRemoved"])
        self.assertEqual(calls["inspect"], 3)

    def test_foreign_mismatched_racing_and_unprovable_containers_refuse_without_removal(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        owned = self._owned_record(spec, start_bytes, start)

        def inspect_runner(record):
            def fake_docker(argv):
                if argv[1] == "container" and argv[2] == "inspect":
                    return mock.Mock(returncode=0, stdout=json.dumps([record]), stderr="")
                if argv[1] == "rm":
                    raise AssertionError("foreign container must never be removed")
                raise AssertionError(f"unexpected docker argv: {argv}")
            return fake_docker

        foreign = json.loads(json.dumps(owned))
        foreign["Config"]["Labels"] = {**owned["Config"]["Labels"], "pixel.qualification.candidateId": "other"}
        with mock.patch.object(self.release_update, "_qualification_interruption_docker", side_effect=inspect_runner(foreign)):
            with self.assertRaisesRegex(self.release_update.UpdateError, "foreign or mismatched"):
                self.release_update.qualification_execution_interruption_custody({}, start_bytes, spec)

        mismatched = json.loads(json.dumps(owned))
        mismatched["Image"] = "sha256:" + ("cd" * 32)
        with mock.patch.object(self.release_update, "_qualification_interruption_docker", side_effect=inspect_runner(mismatched)):
            with self.assertRaisesRegex(self.release_update.UpdateError, "foreign or mismatched"):
                self.release_update.qualification_execution_interruption_custody({}, start_bytes, spec)

        def unprovable(argv):
            return mock.Mock(returncode=3, stdout="boom", stderr="boom")
        with mock.patch.object(self.release_update, "_qualification_interruption_docker", side_effect=unprovable):
            with self.assertRaisesRegex(self.release_update.UpdateError, "could not be determined"):
                self.release_update.qualification_execution_interruption_custody({}, start_bytes, spec)

        calls = {"n": 0}

        def racing(argv):
            if argv[1] == "container" and argv[2] == "inspect":
                calls["n"] += 1
                return mock.Mock(returncode=0, stdout=json.dumps([owned]), stderr="")
            if argv[1] == "rm":
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected docker argv: {argv}")
        with mock.patch.object(self.release_update, "_qualification_interruption_docker", side_effect=racing):
            with self.assertRaisesRegex(self.release_update.UpdateError, "absence not proven"):
                self.release_update.qualification_execution_interruption_custody({}, start_bytes, spec)

    def test_interruption_foreign_container_refuses_record_without_artifact(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        with mock.patch.object(
            self.release_update, "qualification_execution_interruption_custody", autospec=True,
            side_effect=self.release_update.UpdateError("claim-bound container is foreign or mismatched; refusing to mutate it"),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "foreign or mismatched"):
                self.release_update.qualification_execution_interruption_record(
                    self.interruption_args(candidate_id, preview["interruptionHash"]),
                )
        self.assertFalse(self.interruption_path(candidate_id).exists())
        self.assertFalse(self.interruption_marker_path(candidate_id).exists())


    def test_inspect_accepts_exactly_one_object_in_one_element_array(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        name = spec["runtime"]["containerName"]
        record = self._owned_record(spec, start_bytes, start)
        with mock.patch.object(
            self.release_update, "_qualification_interruption_docker",
            return_value=mock.Mock(returncode=0, stdout=json.dumps([record]), stderr=""),
        ):
            self.assertEqual(self.release_update._qualification_interruption_inspect_by_name(name), record)

    def test_inspect_rejects_malformed_shapes(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        name = spec["runtime"]["containerName"]
        for hostile in ("null", "{}", "[]", "[{}, {}]", "[1]", "[null]", '["x"]'):
            with self.subTest(payload=hostile):
                with mock.patch.object(
                    self.release_update, "_qualification_interruption_docker",
                    return_value=mock.Mock(returncode=0, stdout=hostile, stderr=""),
                ):
                    with self.assertRaises(self.release_update.UpdateError):
                        self.release_update._qualification_interruption_inspect_by_name(name)

    def test_inspect_nonzero_returncode_fails_closed(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        name = spec["runtime"]["containerName"]
        with mock.patch.object(
            self.release_update, "_qualification_interruption_docker",
            return_value=mock.Mock(returncode=3, stdout="[]", stderr="boom"),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "could not be determined"):
                self.release_update._qualification_interruption_inspect_by_name(name)

    def test_inspect_unexpected_absence_output_fails_closed(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        name = spec["runtime"]["containerName"]
        with mock.patch.object(
            self.release_update, "_qualification_interruption_docker",
            return_value=mock.Mock(returncode=1, stdout="[]", stderr="boom"),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "could not be determined"):
                self.release_update._qualification_interruption_inspect_by_name(name)
        with mock.patch.object(
            self.release_update, "_qualification_interruption_docker",
            return_value=mock.Mock(
                returncode=1, stdout="[]",
                stderr="Error response from daemon: No such container: some-other-name",
            ),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "could not be determined"):
                self.release_update._qualification_interruption_inspect_by_name(name)

    def test_docker_runner_oserror_fails_closed(self):
        with mock.patch.object(self.release_update.subprocess, "Popen", side_effect=OSError("boom")):
            with self.assertRaisesRegex(self.release_update.UpdateError, "could not be determined"):
                self.release_update._qualification_interruption_docker(
                    [self.release_update.QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", "x"],
                )

    def test_docker_runner_timeout_fails_closed(self):
        read_fd, write_fd = os.pipe()
        try:
            fake_proc = mock.Mock()
            fake_proc.poll.return_value = None
            fake_proc.stdout = os.fdopen(read_fd, "rb")
            fake_proc.stderr = open("/dev/null", "rb")
            fake_proc.returncode = None
            fake_proc.kill = mock.Mock()
            fake_proc.terminate = mock.Mock()
            fake_proc.wait = mock.Mock(return_value=None)
            with mock.patch.object(self.release_update.subprocess, "Popen", return_value=fake_proc), \
                 mock.patch.object(self.release_update, "QUALIFICATION_EXECUTION_INTERRUPTION_TIMEOUT", 0.2):
                with self.assertRaisesRegex(self.release_update.UpdateError, "could not be determined"):
                    self.release_update._qualification_interruption_docker(
                        [self.release_update.QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", "x"],
                    )
        finally:
            os.close(write_fd)

    def test_docker_runner_oversized_stdout_rejects(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"x" * 1000)
            os.close(write_fd)
            fake_proc = mock.Mock()
            fake_proc.poll.return_value = 0
            fake_proc.stdout = os.fdopen(read_fd, "rb")
            fake_proc.stderr = open("/dev/null", "rb")
            fake_proc.returncode = 0
            fake_proc.kill = mock.Mock()
            fake_proc.terminate = mock.Mock()
            fake_proc.wait = mock.Mock(return_value=0)
            with mock.patch.object(self.release_update.subprocess, "Popen", return_value=fake_proc), \
                 mock.patch.object(self.release_update, "QUALIFICATION_EXECUTION_INTERRUPTION_STREAM_LIMIT", 64):
                with self.assertRaisesRegex(self.release_update.UpdateError, "stdout exceeded its bound"):
                    self.release_update._qualification_interruption_docker(
                        [self.release_update.QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", "x"],
                    )
        finally:
            try:
                os.close(write_fd)
            except OSError:
                pass

    def test_docker_runner_oversized_stderr_rejects(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"y" * 1000)
            os.close(write_fd)
            fake_proc = mock.Mock()
            fake_proc.poll.return_value = 0
            fake_proc.stdout = open("/dev/null", "rb")
            fake_proc.stderr = os.fdopen(read_fd, "rb")
            fake_proc.returncode = 0
            fake_proc.kill = mock.Mock()
            fake_proc.terminate = mock.Mock()
            fake_proc.wait = mock.Mock(return_value=0)
            with mock.patch.object(self.release_update.subprocess, "Popen", return_value=fake_proc), \
                 mock.patch.object(self.release_update, "QUALIFICATION_EXECUTION_INTERRUPTION_STREAM_LIMIT", 64):
                with self.assertRaisesRegex(self.release_update.UpdateError, "stderr exceeded its bound"):
                    self.release_update._qualification_interruption_docker(
                        [self.release_update.QUALIFICATION_EXECUTION_DOCKER, "container", "inspect", "x"],
                    )
        finally:
            try:
                os.close(write_fd)
            except OSError:
                pass

    def test_interrupted_run_cannot_record_result(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        with self.assertRaisesRegex(self.release_update.UpdateError, "file set is invalid"):
            self.release_update.qualification_execution_result(
                self.result_arguments(candidate_id, self.result_path(candidate_id)),
            )
        self.assertFalse(self.result_path(candidate_id).exists())
        self.assertFalse(self.result_marker_path(candidate_id).exists())

    @unittest.skipUnless(docker_capable(), "requires real Docker integration with the pinned image")
    def test_real_docker_custody_removes_disposable_container_by_id(self):
        candidate_id, _ = self.acquire_started_run()
        spec, start_bytes, start = self._interruption_spec_and_start(candidate_id)
        name = spec["runtime"]["containerName"]
        labels = {
            "pixel.qualification.attemptId": start["attemptId"],
            "pixel.qualification.candidateId": start["candidateId"],
            "pixel.qualification.executionSpecSha256": start["executionSpecSha256"],
            "pixel.qualification.executionStartSha256": self.release_update.sha256(start_bytes),
        }
        create_argv = [DOCKER, "create", "--name", name, "--pull=never", "--network", "none"]
        for key, value in labels.items():
            create_argv += ["--label", f"{key}={value}"]
        create_argv += [spec["runtime"]["image"], "true"]
        container_id = None
        try:
            created = subprocess.run(create_argv, capture_output=True, text=True, env=SCRUBBED_ENV, timeout=90)
            self.assertEqual(created.returncode, 0, created.stderr)
            container_id = created.stdout.strip()
            self.assertRegex(container_id, r"^[0-9a-f]{64}$")
            result = self.release_update.qualification_execution_interruption_custody({}, start_bytes, spec)
            self.assertTrue(result["containerAbsent"])
            self.assertTrue(result["containerRemoved"])
            name_inspect = subprocess.run(
                [DOCKER, "container", "inspect", name], capture_output=True, text=True, env=SCRUBBED_ENV, timeout=60,
            )
            self.assertNotEqual(name_inspect.returncode, 0)
            id_inspect = subprocess.run(
                [DOCKER, "container", "inspect", container_id], capture_output=True, text=True, env=SCRUBBED_ENV, timeout=60,
            )
            self.assertNotEqual(id_inspect.returncode, 0)
        finally:
            if container_id:
                subprocess.run(
                    [DOCKER, "rm", "-f", container_id], capture_output=True, text=True, env=SCRUBBED_ENV, timeout=60,
                )


    def test_preview_holds_lock_through_binding_derivation(self):
        candidate_id, _ = self.acquire_started_run()
        held = {}
        real_bindings = self.release_update._qualification_execution_interruption_bindings

        def asserting_bindings(state):
            try:
                with self.release_update.exclusive_stage_lock(self.qualification_root):
                    held["during"] = False
            except self.release_update.UpdateError:
                held["during"] = True
            return real_bindings(state)

        with mock.patch.object(
            self.release_update, "_qualification_execution_interruption_bindings",
            side_effect=asserting_bindings,
        ):
            self.preview(candidate_id)
        self.assertTrue(held["during"], "preview must hold the lock through binding/hash derivation")

    def test_record_holds_lock_through_custody_and_write(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        held = {"custody": False, "write": False}

        def asserting_custody(state, start_bytes, spec):
            try:
                with self.release_update.exclusive_stage_lock(self.qualification_root):
                    held["custody"] = False
            except self.release_update.UpdateError:
                held["custody"] = True
            return {"containerAbsent": True, "containerRemoved": False}

        real_write = self.release_update.write_private

        def asserting_write(path, payload):
            try:
                with self.release_update.exclusive_stage_lock(self.qualification_root):
                    held["write"] = False
            except self.release_update.UpdateError:
                held["write"] = True
            return real_write(path, payload)

        with mock.patch.object(
            self.release_update, "qualification_execution_interruption_custody",
            side_effect=asserting_custody,
        ), mock.patch.object(self.release_update, "write_private", side_effect=asserting_write):
            self.release_update.qualification_execution_interruption_record(
                self.interruption_args(candidate_id, preview["interruptionHash"]),
            )
        self.assertTrue(held["custody"], "record must hold the lock through custody")
        self.assertTrue(held["write"], "record must hold the lock through O_EXCL writes")


    def test_interrupted_run_cannot_execute_again_via_harness(self):
        candidate_id, _ = self.acquire_started_run()
        preview = self.preview(candidate_id)
        self.record_interruption(candidate_id, preview["interruptionHash"])
        harness = load_harness()
        ru = harness.load_release_update()
        args = argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
        )
        with self.assertRaisesRegex(ru.UpdateError, "file set is invalid"):
            harness._run(ru, args)


if __name__ == "__main__":
    unittest.main()
