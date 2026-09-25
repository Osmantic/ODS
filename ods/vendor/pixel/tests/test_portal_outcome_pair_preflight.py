import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_pair_preflight as preflight


def private_json(path: Path, value) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


CAPABILITY_IDS = [
    "workspace-read", "content-search", "file-discovery", "file-write", "targeted-edit", "command-execution",
    "debugging-repair", "language-intelligence", "local-evaluation", "bounded-subagent", "goal-coordination",
]
SOURCE_ARCHIVE_SHA256 = "f" * 64


def capability_evidence(path: Path, runner_image: str) -> None:
    results = [{"id": item, "status": "pass", "verification": "external-fixture"} for item in CAPABILITY_IDS]
    lane = {
        "outerSandboxed": True, "network": "local-model-only", "promptCount": 1, "midJobPrompts": 0,
        "externallyVerified": True, "taskSetSha256": "d" * 64, "passed": 11, "total": 11, "results": results,
    }
    private_json(path, {
        "$schema": "./schemas/work-capability-retention-evidence-v1.schema.json", "schemaVersion": 1,
        "operation": "pixel-builder-capability-retention", "status": "pass",
        "sourceCommit": "1" * 40, "sourceTree": "2" * 40,
        "sourceArchiveSha256": SOURCE_ARCHIVE_SHA256,
        "startedAt": "2026-08-14T00:00:00.000Z", "finishedAt": "2026-08-14T00:01:00.000Z",
        "executor": {"id": "omp", "version": "17.2.12", "artifactSha256": "a" * 64},
        "runner": {"imageDigest": runner_image, "builderContractSha256": "b" * 64},
        "corpus": {"id": "pixel-builder-standard-v1", "sha256": "c" * 64, "tasks": 11},
        "baseline": {"mode": "direct-omp", **lane}, "contained": {"mode": "pixel-builder", **lane},
        "comparison": {
            "eligibleBaselinePasses": 11, "retainedPasses": 11, "retentionPermille": 1000,
            "thresholdPermille": 900, "requiredCapabilitiesRetained": True, "sameTaskSet": True,
            "sameExecutor": True, "sameRunner": True, "oneJobAuthorization": True, "midJobPrompts": 0,
        },
        "privacy": {
            "providerCalls": 0, "credentialInputs": 0, "directNetworkRequests": 0,
            "clientDataInputs": 0, "productionDeploymentsTouched": 0,
        },
        "authority": {
            "grantsExecution": False, "grantsCredentials": False, "grantsNetwork": False,
            "grantsExternalEffects": False, "grantsCompletion": False, "grantsRelease": False,
        },
        "boundary": preflight.CAPABILITY_RETENTION_BOUNDARY,
    })


def codex_surface(path: Path, runner_image: str, toolchain_sha256: str = "4" * 64) -> None:
    private_json(path, {
        "schemaVersion": 1, "operation": "pixel-codex-comparison-surface-qualified",
        "modelId": "DeepSeek-V4-Flash-0731",
        "functionTools": ["exec_command", "request_user_input", "update_plan", "view_image", "write_stdin"],
        "customTool": "apply_patch", "customFormat": "grammar:lark", "namespace": "multi_agent_v1",
        "namespaceTools": ["close_agent", "resume_agent", "send_input", "spawn_agent", "wait_agent"],
        "toolCount": 7, "contentStored": False, "credentialUsed": False, "providerCalled": False,
        "externalEffects": False, "codexRunnerImageId": runner_image,
        "codexComparisonToolchainSha256": toolchain_sha256, "fixtureSha256": "5" * 64,
        "workspaceBytes": 536870912, "workspaceTreeSha256": "6" * 64,
        "boundary": preflight.pair_runner.CODEX_SURFACE_BOUNDARY,
    })


class PortalOutcomePairPreflightTests(unittest.TestCase):
    def test_cross_language_review_and_schema_boundaries_are_exact(self):
        source = (ROOT / "deploy/agent-comparison/pixel-system-cli.mjs").read_text(encoding="utf-8")
        self.assertIn(json.dumps(preflight.PIXEL_REVIEW_BOUNDARY), source)
        self.assertIn(json.dumps(preflight.PIXEL_PLANNED_REVIEW_BOUNDARY), source)
        schema = json.loads((ROOT / "schemas/portal-outcome-pair-preflight-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["boundary"]["const"], preflight.PREFLIGHT_BOUNDARY)

    def test_review_binds_both_harnesses_and_removes_temporary_state_without_starting_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            model_dir = parent / "model"
            model_dir.mkdir()
            task_path = parent / "task.json"
            pixel_config = parent / "pixel.json"
            manifest_path = parent / "manifest.json"
            launch_path = parent / "launch.json"
            pair_config = parent / "pair.json"
            capability_path = parent / "capability-retention.json"
            surface_path = parent / "codex-surface.json"
            output = parent / "preflight.json"
            private_json(task_path, {"bindings": {
                "userRequest": {"kind": "request"}, "sourceSnapshot": {"kind": "source"},
                "environment": {"kind": "environment"}, "toolPolicy": {"kind": "tools"},
                "verifier": {"kind": "verifier"}, "sharedModelContract": {"kind": "model"},
                "sharedInferenceContract": {"kind": "inference"},
            }})
            private_json(pixel_config, {})
            private_json(manifest_path, {})
            launch = ["/models/model", "--max-model-len", "1048576"]
            private_json(launch_path, {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-dsv4-launch-arguments",
                "arguments": launch, "boundary": preflight.pair_runner.LAUNCH_BOUNDARY,
            })
            images = {
                "codex": "sha256:" + "1" * 64, "boundary": "sha256:" + "2" * 64,
                "runner": "sha256:" + "3" * 64, "verifier": "sha256:" + "4" * 64,
                "backend": "sha256:" + "5" * 64,
            }
            capability_evidence(capability_path, images["runner"])
            codex_surface(surface_path, images["codex"])
            private_json(pair_config, {
                "$schema": preflight.pair_runner.CONFIG_SCHEMA, "schemaVersion": 1,
                "candidateSourceArchiveSha256": SOURCE_ARCHIVE_SHA256,
                "pixelSystemConfigPath": str(pixel_config), "codexRunnerImage": images["codex"],
                "codexBoundaryImage": images["boundary"], "modelArtifactPath": str(model_dir),
                "codexSurfaceQualificationPath": str(surface_path),
                "modelArtifactManifestPath": str(manifest_path), "launchArgumentsPath": str(launch_path),
                "capabilityRetentionEvidencePath": str(capability_path),
                "preflightPath": str(output),
                "boundary": preflight.pair_runner.CONFIG_BOUNDARY,
            })
            model = {
                "modelId": "DeepSeek-V4-Flash-0731", "artifact": {"sha256": "6" * 64},
                "runtime": {"imageDigest": images["backend"]},
            }
            inference = {"request": {"wireApi": "openai-chat-completions"}}
            model_payload = json.dumps(model, sort_keys=True).encode("utf-8")
            inference_payload = json.dumps(inference, sort_keys=True).encode("utf-8")
            fixture_payloads = {
                "request": b"do work\n", "source": b"synthetic tar", "environment": b"{}",
                "tools": b"{}", "verifier": b"{}", "model": model_payload, "inference": inference_payload,
            }
            digest = lambda payload: hashlib.sha256(payload).hexdigest()
            admission = {
                "comparisonLane": "same-model-harness", "profile": "builder", "capabilities": [],
                "bindings": {
                    "userRequestSha256": digest(fixture_payloads["request"]),
                    "sourceSnapshotSha256": digest(fixture_payloads["source"]),
                    "environmentSha256": digest(fixture_payloads["environment"]),
                    "toolPolicySha256": digest(fixture_payloads["tools"]),
                    "verifierSha256": digest(fixture_payloads["verifier"]),
                },
            }
            calls = []

            class Pixel:
                def review_runtime(self, **options):
                    calls.append(("pixel-review", options["run_id"], options["run_dir"]))
                    return {
                        "schemaVersion": 1, "operation": "pixel-portal-outcome-system-review",
                        "runId": options["run_id"], "profile": options["admission"]["profile"], "status": "ready",
                        "modelContractSha256": digest(model_payload),
                        "inferenceContractSha256": digest(inference_payload),
                        "taskCompatibilitySha256": "e" * 64,
                        "workPolicySha256": "7" * 64, "environmentSha256": "6" * 64,
                        "runtimeEnvironmentSha256": "5" * 64,
                        "harnessContractSha256": "8" * 64,
                        "qualificationReceiptSha256": "0" * 64,
                        "runnerImageDigest": images["runner"], "verifierImageDigest": images["verifier"],
                        "backendImageDigest": images["backend"], "modelArtifactSha256": "6" * 64,
                        "launchBundleSha256": "9" * 64,
                        "changes": {
                            "temporaryPrivateReviewStateRemoved": True, "modelStarted": False,
                            "containerCreated": False, "networkCreated": False,
                            "taskExecuted": False, "externalEffects": False,
                        },
                        "authority": {
                            "grantsModelStart": False, "grantsExecution": False, "grantsNetwork": False,
                            "grantsProviderCall": False, "grantsCredentialUse": False,
                            "grantsExternalEffects": False, "grantsCompletion": False,
                        },
                        "boundary": preflight.PIXEL_REVIEW_BOUNDARY,
                    }

                def review_planned_runtime(self, **options):
                    calls.append(("pixel-planned-review", options["run_id"], options["run_dir"]))
                    return {
                        "schemaVersion": 1, "operation": "pixel-portal-outcome-planned-system-review",
                        "runId": options["run_id"], "profile": options["admission"]["profile"],
                        "status": "structurally-compatible", "readiness": "qualification-required",
                        "modelContractSha256": digest(model_payload),
                        "inferenceContractSha256": digest(inference_payload),
                        "taskCompatibilitySha256": "e" * 64, "harnessContractSha256": "8" * 64,
                        "workPolicySha256": "7" * 64,
                        "environmentSha256": "6" * 64, "backendConfigSha256": "5" * 64,
                        "runnerImageDigest": images["runner"], "backendImageDigest": images["backend"],
                        "changes": {
                            "policyMutated": False, "qualificationFabricated": False, "modelStarted": False,
                            "containerCreated": False, "networkCreated": False, "taskExecuted": False,
                            "externalEffects": False,
                        },
                        "authority": dict(preflight.PLANNED_REVIEW_AUTHORITY),
                        "boundary": preflight.PIXEL_PLANNED_REVIEW_BOUNDARY,
                    }

            class Codex:
                def image_id(self, image):
                    calls.append(("image", image))
                    return image

                def codex_harness_contract_sha256(self):
                    calls.append(("codex-review",))
                    return "a" * 64

                def codex_comparison_toolchain_sha256(self):
                    return "4" * 64

            def resolved(_task_path, reference, _label):
                payload = fixture_payloads[reference["kind"]]
                media_type = "application/x-tar" if reference["kind"] == "source" else "application/json"
                return {"sha256": digest(payload), "bytes": len(payload), "mediaType": media_type}, payload

            with mock.patch.object(preflight.outcome_task, "admit_task", return_value=admission), \
                    mock.patch.object(preflight.outcome_task, "resolved_reference", side_effect=resolved), \
                    mock.patch.object(preflight.outcome_task, "validate_model_contract"), \
                    mock.patch.object(preflight.outcome_task, "validate_inference_contract"), \
                    mock.patch.object(preflight.outcome_task, "validate_tool_policy", return_value={}), \
                    mock.patch.object(preflight.outcome_task, "validate_environment", return_value={}), \
                    mock.patch.object(preflight.verifier_engine, "load_definition", return_value={}), \
                    mock.patch.object(preflight.outcome_runner, "verify_model_artifact_manifest"), \
                    mock.patch.object(preflight.outcome_runner, "verify_launch_arguments"), \
                    mock.patch.object(preflight.outcome_runner, "verify_runtime_identity"):
                launch_original = launch_path.read_bytes()
                drift_output = parent / "drift-preflight.json"
                drift_config = parent / "drift-pair.json"
                drift_value = json.loads(pair_config.read_text(encoding="utf-8"))
                drift_value["preflightPath"] = str(drift_output)
                private_json(drift_config, drift_value)

                class DriftingPixel(Pixel):
                    def review_runtime(self, **options):
                        reviewed = super().review_runtime(**options)
                        launch_path.write_bytes(launch_original + b" ")
                        if os.name != "nt":
                            launch_path.chmod(0o600)
                        return reviewed

                with self.assertRaisesRegex(preflight.evaluation.OutcomeError, "inputs changed during read-only review"):
                    preflight.review_pair(
                        root=ROOT, task_path=task_path, configuration_path=drift_config, output_path=drift_output,
                        pixel_system_factory=lambda **_options: DriftingPixel(),
                        codex_system_factory=lambda **_options: Codex(),
                    )
                self.assertFalse(drift_output.exists())
                launch_path.write_bytes(launch_original)
                if os.name != "nt":
                    launch_path.chmod(0o600)
                result = preflight.review_pair(
                    root=ROOT, task_path=task_path, configuration_path=pair_config, output_path=output,
                    pixel_system_factory=lambda **_options: Pixel(), codex_system_factory=lambda **_options: Codex(),
                )
                compatibility = preflight.review_materialized_pixel_task_compatibility(
                    root=ROOT, task_path=task_path, configuration_path=pair_config, temporary_parent=parent,
                    pixel_system_factory=lambda **_options: Pixel(),
                )
                planned = preflight.review_materialized_pixel_task_planned_compatibility(
                    root=ROOT, task_path=task_path, configuration_path=pair_config, temporary_parent=parent,
                    pixel_system_factory=lambda **_options: Pixel(),
                )

                class UnsafePlannedPixel(Pixel):
                    def review_planned_runtime(self, **options):
                        reviewed = super().review_planned_runtime(**options)
                        reviewed["changes"]["modelStarted"] = True
                        return reviewed

                with self.assertRaisesRegex(preflight.evaluation.OutcomeError, "non-authorizing compatibility contract"):
                    preflight.review_materialized_pixel_task_planned_compatibility(
                        root=ROOT, task_path=task_path, configuration_path=pair_config, temporary_parent=parent,
                        pixel_system_factory=lambda **_options: UnsafePlannedPixel(),
                    )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["profile"], "builder")
            self.assertEqual(result["contractSourceTaskSha256"], digest(task_path.read_bytes()))
            self.assertEqual(result["configurationSha256"], digest(pair_config.read_bytes()))
            self.assertTrue(result["checks"]["temporaryReviewStateRemoved"])
            self.assertFalse(result["checks"]["modelStarted"])
            self.assertFalse(result["authority"]["grantsExecution"])
            self.assertEqual(result["images"]["modelBackend"], images["backend"])
            self.assertEqual(result["pixelTaskCompatibilitySha256"], "e" * 64)
            self.assertEqual(compatibility["taskCompatibilitySha256"], "e" * 64)
            self.assertEqual(planned["status"], "structurally-compatible")
            self.assertEqual(planned["readiness"], "qualification-required")
            self.assertFalse(any(planned["changes"].values()))
            self.assertFalse(any(planned["authority"].values()))
            self.assertEqual(result["pixelModelQualificationReceiptSha256"], "0" * 64)
            self.assertEqual(result["capabilityRetentionEvidenceSha256"], digest(capability_path.read_bytes()))
            self.assertEqual(result["codexSurfaceQualificationSha256"], digest(surface_path.read_bytes()))
            self.assertTrue(result["checks"]["containedBuilderCapabilitiesRetained"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["codexHarnessSha256"], "a" * 64)
            review_root = next(item[2] for item in calls if item[0] == "pixel-review")
            self.assertFalse(review_root.exists())
            self.assertNotIn(str(parent), json.dumps(result))
            unsafe = json.loads(json.dumps(result))
            unsafe["authority"]["grantsExecution"] = True
            unsafe_path = parent / "unsafe-preflight.json"
            private_json(unsafe_path, unsafe)
            with self.assertRaisesRegex(preflight.evaluation.OutcomeError, "preflight contract is invalid"):
                preflight.pair_runner.load_preflight(unsafe_path)

    def test_review_refuses_an_output_not_bound_by_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            model = parent / "model"
            model.mkdir()
            for name in ("task.json", "pixel.json", "manifest.json"):
                private_json(parent / name, {"bindings": {}} if name == "task.json" else {})
            private_json(parent / "capability.json", {})
            private_json(parent / "codex-surface.json", {})
            launch = parent / "launch.json"
            private_json(launch, {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-dsv4-launch-arguments",
                "arguments": ["/models/model"], "boundary": preflight.pair_runner.LAUNCH_BOUNDARY,
            })
            config = parent / "pair.json"
            private_json(config, {
                "$schema": preflight.pair_runner.CONFIG_SCHEMA, "schemaVersion": 1,
                "candidateSourceArchiveSha256": SOURCE_ARCHIVE_SHA256,
                "pixelSystemConfigPath": str(parent / "pixel.json"),
                "codexRunnerImage": "sha256:" + "1" * 64,
                "codexBoundaryImage": "sha256:" + "2" * 64,
                "codexSurfaceQualificationPath": str(parent / "codex-surface.json"),
                "modelArtifactPath": str(model), "modelArtifactManifestPath": str(parent / "manifest.json"),
                "launchArgumentsPath": str(launch), "capabilityRetentionEvidencePath": str(parent / "capability.json"),
                "preflightPath": str(parent / "expected.json"),
                "boundary": preflight.pair_runner.CONFIG_BOUNDARY,
            })
            with self.assertRaisesRegex(preflight.evaluation.OutcomeError, "must equal the path bound"):
                preflight.review_pair(
                    root=ROOT, task_path=parent / "task.json", configuration_path=config,
                    output_path=parent / "substitute.json",
                )

    def test_capability_retention_must_be_complete_external_and_bound_to_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capability.json"
            runner = "sha256:" + "3" * 64
            capability_evidence(path, runner)
            _value, raw = preflight._validated_capability_retention(
                path, runner_image_digest=runner, source_archive_sha256=SOURCE_ARCHIVE_SHA256,
            )
            self.assertEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaisesRegex(preflight.evaluation.OutcomeError, "does not prove full contained capability"):
                preflight._validated_capability_retention(
                    path, runner_image_digest=runner, source_archive_sha256="0" * 64,
                )
            value = json.loads(path.read_text(encoding="utf-8"))
            value["contained"]["results"].pop()
            value["contained"]["passed"] = 10
            private_json(path, value)
            with self.assertRaisesRegex(preflight.evaluation.OutcomeError, "incomplete or interactive"):
                preflight._validated_capability_retention(
                    path, runner_image_digest=runner, source_archive_sha256=SOURCE_ARCHIVE_SHA256,
                )


if __name__ == "__main__":
    unittest.main()
