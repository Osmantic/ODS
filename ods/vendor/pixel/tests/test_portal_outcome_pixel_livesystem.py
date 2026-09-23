import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_pixel_livesystem as livesystem


RUN_ID = "outcomerun-1786622400100-abcdefabcdef"


def private_json(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


class PixelOutcomeLiveSystemTests(unittest.TestCase):
    def test_failed_control_invocation_keeps_bounded_private_diagnostic_after_teardown(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            run_dir = parent / "evidence"
            runtime_root = parent / "runtime"
            run_dir.mkdir(mode=0o700)
            runtime_root.mkdir(mode=0o700)
            if os.name != "nt":
                run_dir.chmod(0o700)
                runtime_root.chmod(0o700)
            system = object.__new__(livesystem.PixelDockerSystem)
            system.node_path = "node"
            system.cli_path = ROOT / "deploy" / "agent-comparison" / "pixel-system-cli.mjs"
            system.configuration_path = parent / "system.json"
            system.command_timeout_seconds = 60
            system.runtime_root = runtime_root
            system._run_dirs = {RUN_ID: run_dir}
            system._started = set()
            system._contracts = {}
            system._profiles = {}
            system._runtime_controls = {}
            stderr = b"private-prefix-must-not-survive-truncation\n" + b"x" * (
                livesystem.MAX_PIXEL_CONTROL_DIAGNOSTIC_BYTES + 17
            )
            failed = subprocess.CompletedProcess(["node"], 2, b"", stderr)
            with mock.patch.object(livesystem.subprocess, "run", return_value=failed):
                with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "Pixel system start failed"):
                    system._invoke("start", RUN_ID, run_dir, {"schemaVersion": 1})
            diagnostic_path = run_dir / "pixel-system-start-failure-diagnostic.json"
            diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["stderrBytes"], len(stderr))
            self.assertEqual(diagnostic["stderrSha256"], hashlib.sha256(stderr).hexdigest())
            self.assertEqual(
                diagnostic["retainedBytes"], livesystem.MAX_PIXEL_CONTROL_DIAGNOSTIC_BYTES
            )
            self.assertTrue(diagnostic["truncated"])
            self.assertNotIn("private-prefix-must-not-survive-truncation", json.dumps({
                key: value for key, value in diagnostic.items() if key != "retainedBase64"
            }))
            self.assertEqual(
                livesystem.base64.b64decode(diagnostic["retainedBase64"]),
                stderr[-livesystem.MAX_PIXEL_CONTROL_DIAGNOSTIC_BYTES:],
            )
            if os.name != "nt":
                self.assertEqual(diagnostic_path.stat().st_mode & 0o077, 0)
            system.teardown_runtime(run_id=RUN_ID, runtime=None)
            self.assertTrue(diagnostic_path.is_file())
            self.assertFalse((run_dir / "pixel-system-start-input.json").exists())

    def test_control_diagnostic_failure_never_replaces_primary_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary).resolve()
            with mock.patch.object(
                livesystem, "_private_json",
                side_effect=livesystem.evaluation.OutcomeError("synthetic diagnostic persistence failure"),
            ):
                livesystem._write_control_failure_diagnostic(run_dir, "start", b"primary failure\n")

    def test_adapter_carries_exact_contract_bytes_collects_artifacts_and_tears_down(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            runtime_root = parent / "runtime"
            run_dir = parent / "evidence"
            runtime_root.mkdir()
            run_dir.mkdir()
            if os.name != "nt":
                runtime_root.chmod(0o700)
                run_dir.chmod(0o700)
            templates = []
            for name in ("policy.json", "environment.json", "backend.json", "assistant.json"):
                path = parent / name
                private_json(path, {})
                templates.append(path)
            config_path = parent / "system.json"
            private_json(config_path, {
                "$schema": "https://osmantic.com/pixel/schemas/portal-outcome-pixel-system-v1.schema.json",
                "schemaVersion": 1, "runtimeRoot": str(runtime_root),
                "policyTemplatePath": str(templates[0]), "environmentTemplatePath": str(templates[1]),
                "modelBackendTemplatePath": str(templates[2]), "assistantTemplatePath": str(templates[3]),
                "boundary": livesystem.CONFIG_BOUNDARY,
            })
            model_payload = b'{"modelId":"DeepSeek-V4-Flash-0731"}\n'
            inference_payload = b'{"request":{"wireApi":"openai-chat-completions"}}\n'
            model_sha = hashlib.sha256(model_payload).hexdigest()
            inference_sha = hashlib.sha256(inference_payload).hexdigest()
            runtime_receipt = {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-runtime", "runId": RUN_ID,
                "profile": "builder",
                "modelId": "DeepSeek-V4-Flash-0731", "modelContractSha256": model_sha,
                "inferenceContractSha256": inference_sha, "workPolicySha256": "1" * 64,
                "environmentSha256": "0" * 64,
                "runtimeEnvironmentSha256": "9" * 64,
                "harnessContractSha256": "2" * 64, "runnerImageDigest": "sha256:" + "3" * 64,
                "verifierImageDigest": "sha256:" + "6" * 64,
                "backendImageDigest": "sha256:" + "4" * 64, "modelArtifactSha256": "5" * 64,
                "launchBundleSha256": "7" * 64,
                "qualificationReceiptSha256": "8" * 64,
                "backendFresh": True, "runnerFresh": True, "realBackend": True, "realTools": True,
                "crossRunStateObserved": False, "qwenProductModel": False, "boundary": "fixture",
            }
            observed_inputs = {}

            def fake_run(argv, **_kwargs):
                command = argv[2]
                input_path = Path(argv[argv.index("--input") + 1])
                output_path = Path(argv[argv.index("--output") + 1])
                observed_inputs[command] = json.loads(input_path.read_text(encoding="utf-8"))
                if command == "planned-review":
                    private_json(output_path, {
                        "schemaVersion": 1, "operation": "pixel-portal-outcome-planned-system-review",
                        "runId": RUN_ID, "profile": "builder", "status": "structurally-compatible",
                    })
                elif command == "start":
                    private_json(output_path, runtime_receipt)
                elif command == "run":
                    artifact_root = runtime_root / RUN_ID / "artifacts"
                    artifact_root.mkdir(parents=True)
                    payload = b"verified artifact\n"
                    artifact_path = artifact_root / "artifact-000-artifact.md"
                    artifact_path.write_bytes(payload)
                    if os.name != "nt":
                        artifact_path.chmod(0o600)
                    private_json(output_path, {
                        "exitCode": 0, "latencyMs": 1234, "finalMessage": "Done",
                        "usage": {"modelRequests": 2, "inputTokens": 100, "outputTokens": 20, "networkBytes": 1000, "toolCalls": 3},
                        "authority": {"sourceMutation": False, "merge": False, "deploy": False, "externalEffects": False},
                        "interaction": {
                            "schemaVersion": 1, "mode": "single-admission-noninteractive",
                            "source": "pixel-builder-checkpoint-v1", "observationSha256": "9" * 64,
                            "operatorInputsAfterAdmission": 0, "operatorAttentionRequests": 0,
                            "approvalRequests": 0, "scopeExpansionRequests": 0, "safetyBlocks": 0,
                            "interruptions": 0, "complete": True, "workerSelfReported": False,
                            "boundary": livesystem.INTERACTION_BOUNDARY,
                        },
                        "independentVerification": {"status": "pass"},
                        "artifacts": [{
                            "kind": "finding-report", "relativePath": "artifact.md", "payloadPath": str(artifact_path),
                            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                        }],
                    })
                else:
                    private_json(output_path, {
                        "schemaVersion": 1, "operation": "pixel-portal-outcome-system-stop", "runId": RUN_ID,
                        "backendRemoved": True, "privateRunStateRemoved": True, "externalEffects": False,
                    })
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            system = livesystem.PixelDockerSystem(root=ROOT, configuration_path=config_path)
            with mock.patch.object(livesystem.subprocess, "run", side_effect=fake_run):
                planned = system.review_planned_runtime(
                    run_id=RUN_ID, admission={"profile": "builder"}, model_contract_payload=model_payload,
                    inference_contract_payload=inference_payload, model_contract_sha256=model_sha,
                    inference_contract_sha256=inference_sha, run_dir=run_dir, task={"profile": "builder"},
                    request_payload=b"Do the work", source_reference={"mediaType": "application/x-tar"},
                    environment={}, tool_policy={}, verifier_definition={},
                )
                self.assertEqual(planned["status"], "structurally-compatible")
                receipt = system.qualify_runtime(
                    run_id=RUN_ID, admission={"profile": "builder"}, model_contract={}, inference_contract={},
                    model_contract_payload=model_payload, inference_contract_payload=inference_payload,
                    model_contract_sha256=model_sha, inference_contract_sha256=inference_sha, run_dir=run_dir,
                )
                self.assertEqual(receipt, runtime_receipt)
                with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "lacks its exact cold or warm runtime control"):
                    system.run_pixel(
                        run_id=RUN_ID, run_dir=run_dir, source_payload=b"source",
                        source_reference={"mediaType": "application/x-tar", "sha256": hashlib.sha256(b"source").hexdigest()},
                        request_payload=b"Do the work", admission={"profile": "builder"},
                        task={"profile": "builder"}, environment={}, tool_policy={}, verifier_definition={},
                        model_contract={}, inference_contract={},
                    )
                control_receipt = {"condition": "cold-first-request", "requestsBefore": 0, "requestsAfter": 0}
                with mock.patch.object(livesystem.runtime_control, "execute", return_value=control_receipt) as controlled:
                    self.assertEqual(system.runtime_control(run_id=RUN_ID, condition="cold-first-request"), control_receipt)
                controlled.assert_called_once_with(
                    docker_path="docker", container=f"pixel-outcome-model-{RUN_ID[-12:]}",
                    run_id=RUN_ID, condition="cold-first-request",
                )
                with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "profile differs"):
                    system.run_pixel(
                        run_id=RUN_ID, run_dir=run_dir, source_payload=b"source",
                        source_reference={"mediaType": "application/x-tar", "sha256": hashlib.sha256(b"source").hexdigest()},
                        request_payload=b"Do the work", admission={"profile": "researcher"},
                        task={"profile": "researcher"}, environment={}, tool_policy={}, verifier_definition={},
                        model_contract={}, inference_contract={},
                    )
                result = system.run_pixel(
                    run_id=RUN_ID, run_dir=run_dir, source_payload=b"source", source_reference={"mediaType": "application/x-tar", "sha256": hashlib.sha256(b"source").hexdigest()},
                    request_payload=b"Do the work", admission={"profile": "builder"}, task={"profile": "builder"}, environment={}, tool_policy={},
                    verifier_definition={}, model_contract={}, inference_contract={},
                )
                self.assertEqual(result["artifacts"][0]["payload"], b"verified artifact\n")
                system.teardown_runtime(run_id=RUN_ID, runtime=receipt)
                self.assertNotIn(RUN_ID, system._runtime_controls)
            self.assertEqual(observed_inputs["start"]["modelContractSha256"], model_sha)
            self.assertEqual(observed_inputs["planned-review"]["operation"], "planned-review")
            self.assertEqual(observed_inputs["planned-review"]["profile"], "builder")
            self.assertEqual(observed_inputs["start"]["inferenceContractSha256"], inference_sha)
            self.assertEqual(observed_inputs["start"]["profile"], "builder")
            self.assertEqual(observed_inputs["run"]["sourceReference"]["bytes"], 6)
            for name in livesystem.CONTROL_FILES:
                self.assertFalse((run_dir / name).exists(), name)

    def test_assistant_product_run_executes_exact_preparation_and_collects_workspace_delta(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve(); runtime_root = parent / "runtime"; run_dir = parent / "evidence"
            runtime_root.mkdir(mode=0o700); run_dir.mkdir(mode=0o700)
            templates = []
            for name in ("policy.json", "environment.json", "backend.json", "assistant.json"):
                path = parent / name; private_json(path, {}); templates.append(path)
            config_path = parent / "system.json"
            private_json(config_path, {
                "$schema": "https://osmantic.com/pixel/schemas/portal-outcome-pixel-system-v1.schema.json",
                "schemaVersion": 1, "runtimeRoot": str(runtime_root),
                "policyTemplatePath": str(templates[0]), "environmentTemplatePath": str(templates[1]),
                "modelBackendTemplatePath": str(templates[2]), "assistantTemplatePath": str(templates[3]),
                "boundary": livesystem.CONFIG_BOUNDARY,
            })
            request = b"Improve the admitted local fixture."
            source = b"source archive fixture"
            source_sha = hashlib.sha256(source).hexdigest(); plan_sha = "1" * 64; tree_sha = "2" * 64
            model = {"modelId": "DeepSeek-V4-Flash-0731"}; inference = {"request": {"wireApi": "openai-chat-completions"}}
            observed = {}

            def assistant_runner(**options):
                observed.update(options)
                workspace = options["run_root"] / "workspace"
                (workspace / "source" / "main.txt").write_text("improved\n", encoding="utf-8")
                (workspace / "notes.txt").write_text("verified notes\n", encoding="utf-8")
                return {
                    "turnId": "turn-1786622400100-abcdefabcdef",
                    "conversation": {"turns": [{
                        "turnId": "turn-1786622400100-abcdefabcdef", "state": "succeeded",
                        "assistantText": "Implemented and checked.", "toolCalls": 4,
                    }]},
                    "modelProxyFinalReceipt": {
                        "planSha256": plan_sha, "modelRequests": 3, "inputTokens": 240,
                        "outputTokens": 80, "networkBytes": 4096,
                    },
                }

            system = livesystem.PixelDockerSystem(
                root=ROOT, configuration_path=config_path, assistant_runner=assistant_runner,
            )
            assistant_root = runtime_root / RUN_ID / "assistant"
            workspace = assistant_root / "workspace"; home = assistant_root / "openclaw-home"; journal = parent / "journal"
            for directory in (assistant_root, workspace, workspace / "source", home, journal):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            original = b"original\n"; (workspace / "source" / "main.txt").write_bytes(original)
            inventory = assistant_root / "source-inventory.json"
            private_json(inventory, {
                "schemaVersion": 1, "archiveSha256": source_sha, "archiveBytes": len(source),
                "extractedBytes": len(original), "treeSha256": tree_sha,
                "entries": [{
                    "path": "source/main.txt", "sourcePath": "source/main.txt", "kind": "file",
                    "bytes": len(original), "sha256": hashlib.sha256(original).hexdigest(), "inert": False,
                }],
            })
            onboarding = assistant_root / "onboarding.json"; private_json(onboarding, {"fixture": True})
            proxy_config = assistant_root / "model-proxy.json"
            private_json(proxy_config, {
                "schemaVersion": 1, "jobId": "work-1786622400100-abcdefabcdef",
                "claimId": "workclaim-1786622400100-abcdefabcdef", "planSha256": plan_sha,
                "provider": "vllm", "modelId": "DeepSeek-V4-Flash-0731", "contextWindow": 131072,
                "supportsVision": False, "backendOrigin": "http://127.0.0.1:18080/",
                "listenHost": "127.0.0.1", "listenPort": 18881, "allowedClientIpv4": "127.0.0.1",
                "allowedTools": ["read", "write"], "receiptPath": "/run/pixel-work-output/model-proxy-receipt.json",
                "qualification": {"profile": "assistant"}, "inference": {}, "budgets": {},
            })
            openclaw = parent / "openclaw"; openclaw.write_text("fixture", encoding="utf-8")
            if os.name != "nt": openclaw.chmod(0o700)
            preparation = {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-assistant-preparation", "runId": RUN_ID,
                "requestSha256": hashlib.sha256(request).hexdigest(), "planSha256": plan_sha,
                "assistantRootPath": str(assistant_root), "workspacePath": str(workspace),
                "workspaceTreeSha256": tree_sha, "sourceInventoryPath": str(inventory),
                "openclawHomePath": str(home), "onboardingPath": str(onboarding),
                "proxyConfigPath": str(proxy_config), "proxyReceiptPath": str(assistant_root / "model-proxy-receipt.json"),
                "openclawBinaryPath": str(openclaw),
                "openclawBinarySha256": hashlib.sha256(openclaw.read_bytes()).hexdigest(),
                "openclawBinaryBytes": openclaw.stat().st_size,
                "nodeBinaryPath": str(Path(sys.executable).resolve()),
                "nodeBinarySha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
                "nodeBinaryBytes": Path(sys.executable).resolve().stat().st_size,
                "proxyLauncherPath": str((ROOT / "deploy/agent-comparison/assistant-model-proxy.mjs").resolve()),
                "allowedTools": ["read", "write"],
                "chatEnvironment": {"PIXEL_LIMB_EMAIL_ENABLED": "0"},
                "actionJournalRoots": [str(journal)], "externalEffects": False,
                "boundary": livesystem.ASSISTANT_PREPARATION_BOUNDARY,
            }
            system._run_dirs[RUN_ID] = run_dir; system._started.add(RUN_ID); system._profiles[RUN_ID] = "assistant"
            system._runtime_controls[RUN_ID] = {"condition": "cold-first-request"}
            system._contracts[RUN_ID] = (b"model", b"inference", "3" * 64, "4" * 64, model, inference)
            system._invoke = mock.Mock(return_value=preparation)
            admission = {"profile": "assistant", "budgets": {"artifactBytes": 1_048_576}}
            changed_identity = dict(preparation); changed_identity["openclawBinarySha256"] = "0" * 64
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "executable identity changed"):
                livesystem._assistant_preparation(
                    changed_identity, root=ROOT, runtime_root=runtime_root, run_id=RUN_ID,
                    request_sha256=hashlib.sha256(request).hexdigest(),
                    source_reference={"mediaType": "application/x-tar", "sha256": source_sha},
                )
            result = system.run_pixel(
                run_id=RUN_ID, run_dir=run_dir, source_payload=source,
                source_reference={"mediaType": "application/x-tar", "sha256": source_sha},
                request_payload=request, admission=admission, task={"profile": "assistant"},
                environment={}, tool_policy={}, verifier_definition={}, model_contract=model, inference_contract=inference,
            )
            self.assertEqual(result["finalMessage"], "Implemented and checked.")
            self.assertEqual(result["usage"]["modelRequests"], 3)
            self.assertEqual({item["relativePath"] for item in result["artifacts"]}, {
                "workspace-delta.json", "workspace/source/main.txt", "workspace/notes.txt",
            })
            self.assertEqual(observed["expected_model"], "DeepSeek-V4-Flash-0731")
            self.assertEqual(observed["chat_environment"], {"PIXEL_LIMB_EMAIL_ENABLED": "0"})
            self.assertFalse(result["workspaceDelta"]["immutablePathViolation"])

    def test_assistant_interaction_counts_capability_approval_without_inventing_operator_input(self):
        envelope = {"conversationSha256": "a" * 64, "actionJournalChains": []}
        turn = {
            "turnId": "turn-1786622400100-abcdefabcdef", "state": "succeeded", "toolCalls": 1,
            "capability": {
                "state": "broker-approval-required", "approvalRequired": True,
                "autonomousWithinPolicy": False,
            },
        }
        receipt = livesystem._assistant_interaction(envelope, turn)
        self.assertEqual(receipt["operatorInputsAfterAdmission"], 0)
        self.assertEqual(receipt["operatorAttentionRequests"], 1)
        self.assertEqual(receipt["approvalRequests"], 1)
        self.assertFalse(receipt["workerSelfReported"])

    def test_assistant_independent_verifier_is_controller_selected_no_network_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve() / "workspace"; workspace.mkdir(mode=0o700)
            (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
            image = "sha256:" + "a" * 64
            system = object.__new__(livesystem.PixelDockerSystem); system.docker_path = "docker"
            system._assistant_verifier_check = mock.Mock(return_value={
                "id": "semantic", "kind": "command", "criterionIndexes": [0], "status": "pass",
                "runtimeMilliseconds": 12, "exitCode": 0, "signal": None, "timedOut": False,
                "outputLimitExceeded": False, "spawnFailed": False, "stdoutBytes": 2,
                "stdoutSha256": hashlib.sha256(b"ok").hexdigest(), "stderrBytes": 0,
                "stderrSha256": hashlib.sha256(b"").hexdigest(), "cleanupVerified": True,
            })
            definition = {
                "acceptanceCriteria": ["semantic check passes"],
                "workspaceVerification": {
                    "checks": [
                        {"id": "patch", "kind": "patch-integrity", "criterionIndexes": [0]},
                        {"id": "semantic", "kind": "command", "criterionIndexes": [0],
                         "workingDirectory": "source", "argv": ["/usr/bin/python3", "main.py"],
                         "timeoutSeconds": 30, "maxOutputBytes": 4096},
                    ],
                    "maxRuntimeSeconds": 60, "maxOutputBytes": 8192,
                },
            }
            environment = {"verifier": {"imageDigest": image}, "limits": {
                "maxPids": 64, "maxMemoryMiB": 512, "maxCpuCores": 1,
            }}
            admission = {"bindings": {"sourceSnapshotSha256": "b" * 64}, "budgets": {"artifactBytes": 1_048_576}}
            delta = {"changes": 1, "files": 1, "bytes": 12, "immutablePathViolation": False}
            with mock.patch.object(livesystem.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, (image + "\n").encode(), b"")):
                receipt = system._verify_assistant_workspace(
                    workspace=workspace, definition=definition, environment=environment,
                    admission=admission, run_id=RUN_ID, workspace_delta=delta,
                )
            self.assertEqual(receipt["status"], "pass")
            self.assertEqual(receipt["network"], "none")
            self.assertFalse(receipt["workerSelectedChecks"])
            system._assistant_verifier_check.assert_called_once()

            delta["immutablePathViolation"] = True
            with mock.patch.object(livesystem.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, (image + "\n").encode(), b"")):
                receipt = system._verify_assistant_workspace(
                    workspace=workspace, definition=definition, environment=environment,
                    admission=admission, run_id=RUN_ID, workspace_delta=delta,
                )
            self.assertEqual(receipt["status"], "fail")

    def test_assistant_collection_reads_payloads_only_for_changed_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            workspace = parent / "workspace"; workspace.mkdir(mode=0o700)
            unchanged = b"u" * 524288
            changed_before = b"before\n"
            changed_after = b"after\n"
            (workspace / "unchanged-a.bin").write_bytes(unchanged)
            (workspace / "unchanged-b.bin").write_bytes(unchanged)
            (workspace / "changed.txt").write_bytes(changed_after)
            source_reference = {"sha256": "1" * 64, "bytes": 1024}
            inventory = parent / "source-inventory.json"
            private_json(inventory, {
                "schemaVersion": 1, "archiveSha256": source_reference["sha256"],
                "archiveBytes": source_reference["bytes"], "extractedBytes": len(unchanged) * 2 + len(changed_before),
                "treeSha256": "2" * 64,
                "entries": [
                    {"path": name, "sourcePath": name, "kind": "file", "bytes": len(payload),
                     "sha256": hashlib.sha256(payload).hexdigest(), "inert": False}
                    for name, payload in (
                        ("unchanged-a.bin", unchanged), ("unchanged-b.bin", unchanged),
                        ("changed.txt", changed_before),
                    )
                ],
            })
            original_reader = livesystem._read_private_payload
            with mock.patch.object(livesystem, "_read_private_payload", wraps=original_reader) as reader:
                artifacts, delta = livesystem._collect_assistant_artifacts(
                    workspace=workspace, inventory_path=inventory, source_reference=source_reference,
                    source_tree_sha256="2" * 64, byte_ceiling=1024,
                )
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(reader.call_args.args[0], workspace / "changed.txt")
            self.assertEqual(delta["bytes"], len(changed_after))
            self.assertEqual({item["relativePath"] for item in artifacts}, {
                "workspace-delta.json", "workspace/changed.txt",
            })


if __name__ == "__main__":
    unittest.main()
