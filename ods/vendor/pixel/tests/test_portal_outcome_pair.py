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
import portal_outcome_pair as pair


def write_private(path: Path, value) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


class PortalOutcomePairTests(unittest.TestCase):
    def test_codex_surface_binding_rejects_weaker_or_different_tool_contracts(self):
        image = "sha256:" + "a" * 64
        toolchain = "b" * 64
        surface = {
            "schemaVersion": 1, "operation": "pixel-codex-comparison-surface-qualified",
            "modelId": "DeepSeek-V4-Flash-0731",
            "functionTools": ["exec_command", "request_user_input", "update_plan", "view_image", "write_stdin"],
            "customTool": "apply_patch", "customFormat": "grammar:lark", "namespace": "multi_agent_v1",
            "namespaceTools": ["close_agent", "resume_agent", "send_input", "spawn_agent", "wait_agent"],
            "toolCount": 7, "contentStored": False, "credentialUsed": False, "providerCalled": False,
            "externalEffects": False, "codexRunnerImageId": image,
            "codexComparisonToolchainSha256": toolchain, "fixtureSha256": "c" * 64,
            "workspaceBytes": 536870912, "workspaceTreeSha256": "d" * 64,
            "boundary": pair.CODEX_SURFACE_BOUNDARY,
        }
        self.assertEqual(pair.validate_codex_surface(surface, codex_image=image, toolchain_sha256=toolchain), surface)
        for field, value in (("providerCalled", True), ("toolCount", 6), ("codexRunnerImageId", "sha256:" + "e" * 64)):
            hostile = dict(surface)
            hostile[field] = value
            with self.assertRaisesRegex(pair.evaluation.OutcomeError, "differs from the exact broad tool contract"):
                pair.validate_codex_surface(hostile, codex_image=image, toolchain_sha256=toolchain)

    def test_preflight_bound_pixel_runtime_fails_before_task_on_identity_drift(self):
        class System:
            def qualify_runtime(self, **_options):
                return {"workPolicySha256": "b" * 64}

        bound = pair._PreflightBoundPixelSystem(System(), {"workPolicySha256": "a" * 64})
        with self.assertRaisesRegex(pair.evaluation.OutcomeError, "differs from its exact read-only preflight"):
            bound.qualify_runtime()

    def test_validate_preflight_binds_invariants_but_not_reference_task_launch_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            preflight_path = parent / "preflight.json"
            write_private(preflight_path, {})
            records = [b"pixel-config", b"manifest", b"launch", b"capability", b"surface"]
            model_payload = b'{"model":true}'
            inference_payload = b'{"inference":true}'
            launch_arguments = ["/models/model"]
            images = {
                "codexRunner": "sha256:" + "1" * 64,
                "inferenceBoundary": "sha256:" + "2" * 64,
                "modelBackend": "sha256:" + "3" * 64,
                "pixelRunner": "sha256:" + "4" * 64,
                "pixelVerifier": "sha256:" + "5" * 64,
            }
            model = {"artifact": {"sha256": "6" * 64}, "runtime": {"imageDigest": images["modelBackend"]}}
            preflight = {
                "profile": "builder", "modelContractSha256": hashlib.sha256(model_payload).hexdigest(),
                "inferenceContractSha256": hashlib.sha256(inference_payload).hexdigest(),
                "pixelWorkPolicySha256": "7" * 64, "pixelEnvironmentSha256": "8" * 64,
                "pixelHarnessSha256": "9" * 64, "pixelLaunchBundleSha256": "a" * 64,
                "pixelModelQualificationReceiptSha256": "b" * 64,
                "modelArtifactSha256": model["artifact"]["sha256"], "images": images,
                "configurationSha256": hashlib.sha256(b"configuration").hexdigest(),
                "pixelSystemConfigurationSha256": hashlib.sha256(records[0]).hexdigest(),
                "modelArtifactManifestFileSha256": hashlib.sha256(records[1]).hexdigest(),
                "launchArgumentsFileSha256": hashlib.sha256(records[2]).hexdigest(),
                "launchArgumentsSha256": pair.outcome_runner.canonical_arguments_sha256(launch_arguments),
                "capabilityRetentionEvidenceSha256": hashlib.sha256(records[3]).hexdigest(),
                "codexSurfaceQualificationSha256": hashlib.sha256(records[4]).hexdigest(),
                "codexHarnessSha256": "c" * 64,
            }
            configuration = {
                "preflightPath": str(preflight_path), "pixelSystemConfigPath": str(parent / "pixel.json"),
                "modelArtifactManifestPath": str(parent / "manifest.json"),
                "launchArgumentsPath": str(parent / "launch.json"),
                "capabilityRetentionEvidencePath": str(parent / "capability.json"),
                "codexSurfaceQualificationPath": str(parent / "surface.json"),
                "codexRunnerImage": images["codexRunner"], "codexBoundaryImage": images["inferenceBoundary"],
            }

            class CodexSystem:
                def codex_comparison_toolchain_sha256(self):
                    return "d" * 64

                def codex_harness_contract_sha256(self):
                    return preflight["codexHarnessSha256"]

            with mock.patch.object(pair, "load_preflight", return_value=(preflight, b"preflight")), mock.patch.object(
                pair, "load_shared_contracts", return_value=(model, model_payload, {}, inference_payload),
            ), mock.patch.object(pair, "load_launch_arguments", return_value=launch_arguments), mock.patch.object(
                pair.evaluation, "read_json", side_effect=[({}, raw) for raw in records],
            ), mock.patch.object(pair, "validate_codex_surface"), mock.patch.object(
                pair, "_exact_image",
            ), mock.patch.object(pair.outcome_runner, "verify_runtime_identity"):
                *_, expected = pair.validate_preflight(
                    root=ROOT, task_path=parent / "task.json", configuration=configuration,
                    configuration_raw=b"configuration", admission={"comparisonLane": "same-model-harness", "profile": "builder"},
                    codex_system=CodexSystem(),
                )

            self.assertNotIn("launchBundleSha256", expected)
            self.assertEqual(expected["workPolicySha256"], preflight["pixelWorkPolicySha256"])

            class PixelSystem:
                def qualify_runtime(self, **_options):
                    return {**expected, "launchBundleSha256": "e" * 64}

            runtime = pair._PreflightBoundPixelSystem(PixelSystem(), expected).qualify_runtime()
            self.assertEqual(runtime["launchBundleSha256"], "e" * 64)
            drifted = dict(runtime)
            drifted["workPolicySha256"] = "f" * 64

            class DriftedSystem:
                def qualify_runtime(self, **_options):
                    return drifted

            with self.assertRaisesRegex(pair.evaluation.OutcomeError, "differs from its exact read-only preflight"):
                pair._PreflightBoundPixelSystem(DriftedSystem(), expected).qualify_runtime()

    def test_mid_pair_preflight_substitution_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            task = parent / "task.json"
            config = parent / "config.json"
            preflight = parent / "preflight.json"
            for path in (task, config, preflight):
                write_private(path, {})
            admission = {"comparisonLane": "same-model-harness"}
            admission_sha = pair.evaluation.sha256(pair.evaluation.canonical(admission))
            with mock.patch.object(pair.evaluation, "read_json", return_value=({}, b"task")), mock.patch.object(
                pair, "load_configuration_record", return_value=({
                    "capabilityRetentionEvidencePath": str(config), "codexSurfaceQualificationPath": str(config),
                }, b"config"),
            ), mock.patch.object(pair, "load_preflight", return_value=({
                "capabilityRetentionEvidenceSha256": pair.evaluation.sha256(b"task"),
                "codexSurfaceQualificationSha256": pair.evaluation.sha256(b"task"),
            }, b"changed")), mock.patch.object(
                pair.outcome_task, "admit_task", return_value=admission,
            ):
                with self.assertRaisesRegex(pair.evaluation.OutcomeError, "changed during execution"):
                    pair._assert_pair_inputs_stable(
                        root=ROOT, task_path=task, task_raw=b"task", admission_sha256=admission_sha,
                        configuration_path=config, configuration_raw=b"config",
                        preflight_path=preflight, preflight_raw=b"original",
                    )

    def test_neutral_dimensions_are_evidence_derived_and_budget_binned(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary).resolve()
            evidence_dir = run_dir / "evidence"
            evidence_dir.mkdir()
            if os.name != "nt":
                run_dir.chmod(0o700)
                evidence_dir.chmod(0o700)
            values = {
                "independent-verifier": {
                    "status": "pass", "workerSelectedChecks": False, "network": "none", "externalEffects": False,
                },
                "command-exit": {
                    "exitCode": 0, "latencyMs": 20000, "modelRequests": 2, "inputTokens": 300,
                    "outputTokens": 400, "operatorInterventions": 0, "toolCalls": 3,
                },
            }
            entries = []
            for kind, value in values.items():
                payload = json.dumps(value).encode("utf-8")
                path = evidence_dir / f"{kind}.json"
                path.write_bytes(payload)
                if os.name != "nt":
                    path.chmod(0o600)
                entries.append({
                    "type": kind, "relativePath": f"evidence/{kind}.json",
                    "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                })
            scorer = pair.neutral_dimensions(run_dir, {"profile": "builder", "budgets": {
                "wallTimeSeconds": 100, "modelRequests": 8, "inputTokens": 1000, "outputTokens": 2000,
            }})
            result = {item["id"]: item for item in scorer(entries, [{"kind": "patch"}])}
            self.assertEqual(result["correctness"]["score"], 4)
            self.assertEqual(result["artifact-quality"]["score"], 4)
            self.assertEqual(result["recovery"]["score"], 0)
            self.assertEqual(result["operator-effort"]["score"], 4)
            self.assertEqual(result["latency"]["score"], 4)
            self.assertEqual(result["resource-use"]["score"], 3)
            assistant_scorer = pair.neutral_dimensions(run_dir, {"profile": "assistant", "budgets": {
                "wallTimeSeconds": 100, "modelRequests": 8, "inputTokens": 1000, "outputTokens": 2000,
            }})
            assistant_result = {item["id"]: item["score"] for item in assistant_scorer(entries, [{"kind": "patch"}])}
            self.assertEqual(assistant_result, {item: value["score"] for item, value in result.items()})
            values["command-exit"]["latencyMs"] = 100001
            payload = json.dumps(values["command-exit"]).encode("utf-8")
            (evidence_dir / "command-exit.json").write_bytes(payload)
            command = next(item for item in entries if item["type"] == "command-exit")
            command.update(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
            self.assertEqual({item["id"]: item for item in scorer(entries, [{"kind": "patch"}])}["latency"]["score"], 0)
            values["command-exit"]["operatorInterventions"] = 1
            payload = json.dumps(values["command-exit"]).encode("utf-8")
            (evidence_dir / "command-exit.json").write_bytes(payload)
            command.update(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
            self.assertEqual(
                {item["id"]: item for item in scorer(entries, [{"kind": "patch"}])}["operator-effort"]["score"], 0,
            )

    def test_research_dimensions_award_no_unverified_quality_or_efficiency_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary).resolve()
            evidence_dir = run_dir / "evidence"
            evidence_dir.mkdir()
            value = {
                "status": "evidence-pass", "independent": True, "semanticEntailmentVerified": False,
                "network": "none", "externalEffects": False,
            }
            payload = json.dumps(value).encode("utf-8")
            (evidence_dir / "research.json").write_bytes(payload)
            entries = [{
                "type": "independent-verifier", "relativePath": "evidence/research.json",
                "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
            }]
            scorer = pair.neutral_dimensions(run_dir, {"profile": "researcher", "budgets": {
                "wallTimeSeconds": 100, "modelRequests": 8, "inputTokens": 1000, "outputTokens": 2000,
            }})
            result = {item["id"]: item for item in scorer(entries, [{"kind": "finding-report"}])}
            self.assertEqual(result["artifact-quality"]["score"], 4)
            for dimension in ("outcome-completeness", "correctness", "recovery", "operator-effort", "latency", "resource-use"):
                self.assertEqual(result[dimension]["score"], 0, dimension)

    def test_pair_executes_both_real_adapter_interfaces_in_declared_order_and_writes_content_free_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            model = parent / "model"
            model.mkdir()
            private_files = {}
            for name, value in (
                ("task.json", {"task": True}), ("pixel.json", {"pixel": True}),
                ("manifest.json", {"manifest": True}),
                ("launch.json", {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-dsv4-launch-arguments",
                    "arguments": ["/models/model", "--served-model-name", "DeepSeek-V4-Flash-0731"],
                    "boundary": pair.LAUNCH_BOUNDARY,
                }),
            ):
                path = parent / name
                write_private(path, value)
                private_files[name] = path
            config = parent / "pair.json"
            preflight_path = parent / "preflight.json"
            capability_path = parent / "capability.json"
            surface_path = parent / "codex-surface.json"
            write_private(capability_path, {"capability": True})
            write_private(surface_path, {"surface": True})
            write_private(config, {
                "$schema": pair.CONFIG_SCHEMA, "schemaVersion": 1,
                "candidateSourceArchiveSha256": "f" * 64,
                "pixelSystemConfigPath": str(private_files["pixel.json"]),
                "codexRunnerImage": "sha256:" + "e" * 64, "codexBoundaryImage": "sha256:" + "a" * 64,
                "codexSurfaceQualificationPath": str(surface_path),
                "modelArtifactPath": str(model), "modelArtifactManifestPath": str(private_files["manifest.json"]),
                "launchArgumentsPath": str(private_files["launch.json"]),
                "capabilityRetentionEvidencePath": str(capability_path), "preflightPath": str(preflight_path),
                "boundary": pair.CONFIG_BOUNDARY,
            })
            admission = {
                "comparisonLane": "same-model-harness", "budgets": {
                    "wallTimeSeconds": 60, "modelRequests": 4, "inputTokens": 100, "outputTokens": 100,
                },
            }
            calls = []

            class Pixel:
                pass

            class Codex:
                def codex_harness_contract_sha256(self):
                    return "b" * 64

            def pixel_runner(_system, **options):
                calls.append("pixel")
                (options["run_dir"] / "run.json").write_text("pixel", encoding="utf-8")

            def codex_runner(_system, **options):
                calls.append("codex")
                self.assertEqual(options["harness_contract_sha256"], "b" * 64)
                (options["run_dir"] / "run.json").write_text("codex", encoding="utf-8")

            comparison = {
                "pixelRunSha256": "c" * 64, "codexRunSha256": "d" * 64,
                "status": "pass", "classification": "parity",
            }
            output = parent / "output"
            preflight = {
                "codexHarnessSha256": "b" * 64, "modelContractSha256": "1" * 64,
                "inferenceContractSha256": "2" * 64,
            }
            preflight_raw = b'{"preflight":true}\n'
            validated = (
                preflight, preflight_raw, {}, b"model", {}, b"inference",
                ["/models/model", "--served-model-name", "DeepSeek-V4-Flash-0731"], {},
            )
            with mock.patch.object(pair.outcome_task, "admit_task", return_value=admission), mock.patch.object(
                pair.evaluation, "compare_runs", return_value=comparison,
            ), mock.patch.object(pair, "validate_preflight", return_value=validated), mock.patch.object(
                pair, "_assert_pair_inputs_stable",
            ):
                result = pair.execute_pair(
                    root=ROOT, task_path=private_files["task.json"], configuration_path=config,
                    output_root=output, order="codex-first", runtime_condition="cold-first-request",
                    pixel_system_factory=lambda **_kwargs: Pixel(),
                    codex_system_factory=lambda **_kwargs: Codex(), pixel_runner=pixel_runner, codex_runner=codex_runner,
                )
            self.assertEqual(calls, ["codex", "pixel"])
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["preflightSha256"], hashlib.sha256(preflight_raw).hexdigest())
            self.assertEqual(result["runtimeCondition"], "cold-first-request")
            self.assertEqual(json.loads((output / "pair.json").read_text(encoding="utf-8"))["armOrder"], ["codex", "pixel"])
            self.assertTrue((output / "comparison.json").is_file())
            self.assertNotIn(str(parent), json.dumps(result))


if __name__ == "__main__":
    unittest.main()
