import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_orchestrate", ROOT / "scripts/portal_outcome_orchestrate.py",
)
orchestrate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(orchestrate)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


PROBE_TRANSCRIPT = "\n".join([
    '{"type":"thread.started","thread_id":"019ff7fa-fbb9-78d2-9ead-867ca235926a"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"tool_1","type":"command_execution","command":"scanner --measure","exit_code":0}}',
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"measured coverage: 41 of 44"}}',
    '{"type":"turn.completed","usage":{"input_tokens":5946,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":7,"reasoning_output_tokens":0}}',
])
TEMPLATE_TEXT = "exact-template"
LAUNCH_ARGUMENTS = [
    "--model", "/models/model.gguf", "--host", "0.0.0.0", "--port", "8080",
    "--alias", "fixture-flash", "--ctx-size", "32768", "--threads", "8", "--parallel", "1",
]


class FakeSystem:
    def __init__(self, contract, *, expected_profile="builder", independent_verification=None):
        self.contract = contract
        self.expected_profile = expected_profile
        self.independent_verification = independent_verification
        self.calls = []
        self.request_counts = [0, 3]
        self.boundary_requests = 3
        self.fail_health = False

    def codex_harness_contract_sha256(self):
        self.calls.append("harness_contract")
        return "9" * 64

    def image_id(self, _reference):
        self.calls.append("image_id")
        return self.contract["runtime"]["imageDigest"]

    def executable_sha256(self, _reference, _path):
        self.calls.append("executable_sha256")
        return self.contract["runtime"]["executableSha256"]

    def network_create(self, run_id):
        self.calls.append("network_create")
        return f"net-{run_id}"

    def model_container_start(self, _network, _contract, _arguments, _artifact):
        self.calls.append("container_start")
        return "container-1"

    def wait_healthy(self, _container):
        self.calls.append("wait_healthy")
        if self.fail_health:
            raise orchestrate.evaluation.OutcomeError("model container never became healthy")

    def runtime_control(self, *, container, run_id, condition):
        self.calls.append("runtime_control")
        self.assert_runtime_control_container = container
        warm = condition == "warm-neutral-probe"
        return {
            "schemaVersion": 1, "operation": "pixel-portal-outcome-runtime-control",
            "runId": run_id, "condition": condition, "status": "ready",
            "warmupRequestSha256": "7" * 64 if warm else None,
            "warmupResponseSha256": "8" * 64 if warm else None,
            "requestsBefore": 0, "requestsAfter": 1 if warm else 0,
            "warmupModelRequests": 1 if warm else 0,
            "warmupInputTokens": 9 if warm else 0, "warmupOutputTokens": 1 if warm else 0,
            "measuredUsageIncludesWarmup": False, "promptCachePolicy": "empty-at-run-start",
            "crossRunStateObserved": False,
            "authority": {
                "grantsTaskExecution": False, "grantsToolUse": False, "grantsProviderCall": False,
                "grantsCredentialUse": False, "grantsExternalEffect": False, "grantsCompletion": False,
            },
            "boundary": orchestrate.evaluation.RUNTIME_CONTROL_BOUNDARY,
        }

    def inference_boundary_start(self, _network, _run_id, _model, _inference, _admission, _run_dir):
        self.calls.append("boundary_start")
        return "boundary-1"

    def wait_inference_boundary(self, _container):
        self.calls.append("wait_boundary")

    def inference_boundary_receipt(self, _container, expected_policy_sha256):
        self.calls.append("boundary_receipt")
        return {
            "schemaVersion": 1, "runId": "outcomerun-1786550400003-aaaaaaaaaaac",
            "inferencePolicySha256": expected_policy_sha256, "requests": self.boundary_requests,
            "inputTokens": 5946, "outputTokens": 7,
            "deniedRequests": 0, "backendFailures": 0, "responseBytes": 1024,
            "backendResponseBytes": 768, "ingressWireApi": "openai-responses",
            "backendWireApi": "openai-chat-completions", "adapter": "responses-to-chat-v2", "active": False,
            "lastFailureCode": None, "contentStored": False, "credentialsForwarded": False,
            "arbitraryNetwork": False, "externalEffects": False,
        }

    def container_id(self, _container):
        return "c" * 64

    def container_created_at(self, _container):
        return "2026-08-12T12:00:00Z"

    def server_get(self, _container, path):
        self.calls.append(f"get:{path}")
        if path == "/v1/models" and self.contract["runtime"]["implementation"] == "vllm":
            return {
                "object": "list",
                "data": [{
                    "id": self.contract["modelId"],
                    "max_model_len": self.contract["runtime"]["contextWindow"],
                }],
            }
        if path == "/props":
            return {"chat_template": TEMPLATE_TEXT, "default_generation_settings": {"n_ctx": 32768}}
        if path == "/slots":
            return [{"id": 0}]
        raise AssertionError(path)

    def server_get_text(self, _container, path):
        self.calls.append(f"get-text:{path}")
        if path == "/metrics" and self.contract["runtime"]["implementation"] == "vllm":
            return "# fresh server\nvllm:request_success_total 0\nvllm:num_requests_running 0\n"
        raise AssertionError(path)

    def server_request_count(self, _container):
        self.calls.append("request_count")
        return self.request_counts.pop(0)

    def run_codex(
        self, _network, _contract, _admission, request_payload, *, source_payload,
        source_reference, environment_payload, environment, tool_policy_payload, tool_policy,
        verifier_definition, run_dir, run_id, task, model_contract_payload, inference_contract_payload,
        research_fixture_payload, research_fixture_reference,
    ):
        self.calls.append("run_codex")
        assert b"scanner" in request_payload
        assert source_payload == b"deterministic source snapshot bytes\n"
        assert source_reference["mediaType"] == "application/x-tar"
        assert b'"operation":"pixel-portal-outcome-environment"' in environment_payload
        assert environment["operation"] == "pixel-portal-outcome-environment"
        assert b'"operation":"pixel-portal-outcome-tool-policy"' in tool_policy_payload
        assert tool_policy["operation"] == "pixel-portal-outcome-tool-policy"
        assert verifier_definition["operation"] == "pixel-portal-outcome-deterministic-verifier"
        assert run_dir.is_dir()
        assert run_id.startswith("outcomerun-")
        assert task["profile"] == self.expected_profile
        assert model_contract_payload and inference_contract_payload
        assert research_fixture_payload is None and research_fixture_reference is None
        return {
            "transcript": PROBE_TRANSCRIPT, "exitCode": 0, "latencyMs": 4000,
            "artifacts": [{"kind": "finding-report", "relativePath": "artifact.md", "payload": b"# Findings\nmeasured coverage: 41 of 44 checks\n"}],
            "independentVerification": self.independent_verification,
        }

    def teardown(self, network, container, boundary=None):
        self.calls.append(f"teardown:{network}:{container}:{boundary}")


class PortalOutcomeOrchestrateTests(unittest.TestCase):
    def setUp(self):
        corpus = json.loads((ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8"))
        self.journey = next(item for item in corpus["journeys"] if item["id"] == "bounded-security-scanner")

    def fixture(self, parent: Path):
        model_bytes = b"deterministic fixture model bytes\n"
        artifact_path = parent / "model.gguf"
        artifact_path.write_bytes(model_bytes)
        model_contract = {
            "$schema": orchestrate.outcome_task.MODEL_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-model-contract", "modelId": "fixture-flash",
            "artifact": {
                "kind": "single-file", "sha256": digest(model_bytes), "bytes": len(model_bytes),
                "fileCount": 1, "format": "gguf", "quantization": "Q4_K_M",
                "tokenizerSha256": "2" * 64, "chatTemplateSha256": digest(TEMPLATE_TEXT.encode("utf-8")),
                "metadataSha256": "4" * 64,
            },
            "runtime": {
                "implementation": "llama.cpp", "imageDigest": "sha256:" + "5" * 64,
                "executableSha256": "6" * 64,
                "launchArgumentsSha256": orchestrate.runner.canonical_arguments_sha256(LAUNCH_ARGUMENTS),
                "protocol": "openai-responses-v1", "contextWindow": 32768, "parallelSlots": 1,
                "resources": {
                    "acceleratorClass": "nvidia", "acceleratorCount": 1, "cpuCores": 8,
                    "memoryMiB": 12288, "sharedMemoryMiB": 1024, "tmpfsMiB": 64,
                    "cacheMiB": 1024, "pidsLimit": 1024,
                },
                "runtimeIsolation": "fresh-per-run", "restartPolicy": "no", "crossRunStateAllowed": False,
            },
            "authority": {
                "grantsModelStart": False, "grantsProviderCall": False, "grantsNetwork": False,
                "grantsCredentialUse": False, "grantsExecution": False, "grantsCompletion": False,
            },
            "boundary": orchestrate.outcome_task.MODEL_BOUNDARY,
        }
        inference_contract = {
            "$schema": orchestrate.outcome_task.INFERENCE_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-inference-contract",
            "sampling": {
                "source": "request-boundary-enforced", "temperaturePermille": 1000, "topPPermille": 950,
                "topK": 0, "minPPermille": 0, "repeatPenaltyPermille": 1000, "seed": 42,
                "reasoningEffort": "backend-default", "reasoningVisibility": "hidden",
            },
            "request": {
                "wireApi": "openai-responses", "stream": True, "maxOutputTokens": 4096, "toolEncoding": "function",
                "requestFieldPolicySha256": "8" * 64, "promptCachePolicy": "empty-at-run-start",
            },
            "authority": {"grantsInference": False, "grantsToolUse": False, "grantsExecution": False, "grantsCompletion": False},
            "boundary": orchestrate.outcome_task.INFERENCE_BOUNDARY,
        }
        inference_contract["request"]["requestFieldPolicySha256"] = orchestrate.outcome_task.inference_policy_sha256(inference_contract)
        verifier_definition = {
            "$schema": orchestrate.verifier_engine.VERIFIER_SCHEMA,
            "operation": orchestrate.verifier_engine.VERIFIER_OPERATION, "schemaVersion": 1,
            "journeyId": self.journey["id"],
            "acceptanceCriteria": ["Return measured bounded findings"],
            "checks": [
                {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
                {"assertionId": "real-tool-outcome", "check": "command-exit-zero"},
                {"assertionId": "artifact-openable", "check": "artifact-parses"},
                {"assertionId": "bounded-finding-language", "check": "artifact-language-bounded"},
            ],
            "forbiddenPhrases": ["guaranteed to be fully secure"],
            "finalReply": None,
            "workspaceVerification": None,
            "boundary": orchestrate.verifier_engine.VERIFIER_BOUNDARY,
        }
        environment = {
            "$schema": orchestrate.outcome_task.ENVIRONMENT_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-environment",
            "platform": {"operatingSystem": "linux", "architecture": "amd64", "distribution": "debian-12", "locale": "C.UTF-8", "timeZone": "UTC"},
            "isolation": {"workspace": "fresh-disposable-read-write", "controlFiles": "inert", "directNetwork": False, "packageInstallation": False, "inheritedEnvironment": False, "inheritedFileDescriptors": False, "crossRunState": False},
            "limits": {"maxIterations": 20, "maxToolCalls": 2000, "maxConcurrentSubagents": 1, "maxCpuCores": 4, "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240, "maxNetworkBytes": 10485760, "maxFailures": 5, "noProgressLimit": 3, "maxPids": 1024},
            "verifier": {"imageDigest": "sha256:" + "e" * 64, "allowedExecutables": ["/usr/bin/python3"], "maxChecks": 16, "maxRuntimeSeconds": 900, "maxOutputBytes": 1048576, "network": "none"},
            "authority": {field: False for field in orchestrate.outcome_task.ENVIRONMENT_AUTHORITY_FIELDS},
            "boundary": orchestrate.outcome_task.ENVIRONMENT_BOUNDARY,
        }
        contents = {
            "request.txt": b"Inspect the supplied scanner and report exact measured coverage.\n",
            "source.tar": b"deterministic source snapshot bytes\n",
            "environment.json": json.dumps(environment, separators=(",", ":")).encode("utf-8"),
            "tools.json": json.dumps({
                "$schema": orchestrate.outcome_task.TOOL_POLICY_SCHEMA, "schemaVersion": 1,
                "operation": "pixel-portal-outcome-tool-policy", "workspace": "disposable-read-write",
                "tools": ["debug", "edit", "eval", "hub", "lsp", "read", "search", "shell", "task", "write"],
                "brokeredServices": ["local-model"], "maximumSubagents": 1,
                "hostAccess": False, "ambientCredentials": False, "externalEffects": False,
                "mergeAuthority": False, "deployAuthority": False, "policyMutation": False,
                "boundary": orchestrate.outcome_task.TOOL_POLICY_BOUNDARY,
            }, separators=(",", ":")).encode("utf-8"),
            "verifier.json": json.dumps(verifier_definition).encode("utf-8"),
            "model-contract.json": json.dumps(model_contract).encode("utf-8"),
            "inference-contract.json": json.dumps(inference_contract).encode("utf-8"),
        }
        for name, payload in contents.items():
            (parent / name).write_bytes(payload)

        def reference(name, media_type):
            payload = contents[name]
            return {"relativePath": name, "sha256": digest(payload), "bytes": len(payload), "mediaType": media_type}

        task_value = {
            "$schema": orchestrate.outcome_task.TASK_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-task",
            "taskId": "outcometask-1786550400001-aaaaaaaaaaaa", "createdAt": "2026-08-12T12:00:00Z",
            "journeyId": self.journey["id"], "comparisonLane": "same-model-harness",
            "profile": self.journey["profile"], "dataClass": self.journey["dataClass"],
            "effectBoundary": self.journey["effect"],
            "scenario": {"kind": "baseline", "fault": None, "seedSha256": "1" * 64},
            "bindings": {
                "userRequest": reference("request.txt", "text/plain"),
                "sourceSnapshot": reference("source.tar", "application/x-tar"),
                "environment": reference("environment.json", "application/json"),
                "toolPolicy": reference("tools.json", "application/json"),
                "verifier": reference("verifier.json", "application/json"),
                "sharedModelContract": reference("model-contract.json", "application/json"),
                "sharedInferenceContract": reference("inference-contract.json", "application/json"),
                "sanitizationEvidence": None, "researchFixture": None,
            },
            "capabilities": ["artifact-production", "filesystem-read", "local-model", "process-execution", "reasoning"],
            "dataRoute": "local-only",
            "budgets": {
                "wallTimeSeconds": 900, "operatorInterventions": 1, "modelRequests": 20,
                "inputTokens": 100000, "outputTokens": 20000, "artifactBytes": 1048576,
                "externalWrites": 0,
            },
            "authority": {field: False for field in orchestrate.outcome_task.AUTHORITY_FIELDS},
            "boundary": orchestrate.outcome_task.TASK_BOUNDARY,
        }
        task_path = parent / "task.json"
        task_path.write_text(json.dumps(task_value), encoding="utf-8")
        if orchestrate.os.name != "nt":
            task_path.chmod(0o600)
        return task_path, model_contract, artifact_path

    def directory_fixture(self, parent: Path):
        task_path, model_contract, single_path = self.fixture(parent)
        single_path.unlink()
        model_root = parent / "model"
        model_root.mkdir()
        (model_root / "tokenizer").mkdir()
        payloads = {
            "config.json": b'{"architectures":["FixtureFlash"]}\n',
            "model-00001-of-00002.safetensors": b"shard-one\n",
            "model-00002-of-00002.safetensors": b"shard-two\n",
            "tokenizer/tokenizer.json": b'{"version":"1.0"}\n',
        }
        files = []
        for relative, payload in sorted(payloads.items()):
            (model_root / relative).write_bytes(payload)
            files.append({"relativePath": relative, "bytes": len(payload), "sha256": digest(payload)})
        aggregate = orchestrate.evaluation.sha256(orchestrate.evaluation.canonical({
            "schemaVersion": 1, "kind": "directory", "files": files,
        }))
        manifest = {
            "$schema": orchestrate.runner.MODEL_ARTIFACT_MANIFEST_SCHEMA,
            "schemaVersion": 1,
            "kind": "directory", "artifactSha256": aggregate, "fileCount": len(files),
            "totalBytes": sum(item["bytes"] for item in files), "files": files,
            "authority": dict(orchestrate.runner.MODEL_ARTIFACT_MANIFEST_AUTHORITY),
            "boundary": orchestrate.runner.MODEL_ARTIFACT_MANIFEST_BOUNDARY,
        }
        manifest_path = parent / "model-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        if orchestrate.os.name != "nt":
            manifest_path.chmod(0o600)

        vllm_arguments = [
            "serve", "/model", "--served-model-name", "fixture-flash", "--host", "0.0.0.0",
            "--port", "8080", "--max-model-len", "32768", "--max-num-seqs", "1",
        ]
        model_contract["artifact"].update({
            "kind": "directory-manifest", "sha256": aggregate, "bytes": manifest["totalBytes"],
            "fileCount": len(files), "format": "safetensors", "quantization": "FP8",
            "tokenizerSha256": None, "chatTemplateSha256": None, "metadataSha256": None,
        })
        model_contract["runtime"].update({
            "implementation": "vllm", "executableSha256": "6" * 64,
            "launchArgumentsSha256": orchestrate.runner.canonical_arguments_sha256(vllm_arguments),
            "protocol": "openai-chat-completions-v1",
        })
        model_payload = json.dumps(model_contract).encode("utf-8")
        (parent / "model-contract.json").write_bytes(model_payload)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["bindings"]["sharedModelContract"].update({
            "sha256": digest(model_payload), "bytes": len(model_payload),
        })
        inference_path = parent / "inference-contract.json"
        inference_contract = json.loads(inference_path.read_text(encoding="utf-8"))
        inference_contract["request"]["wireApi"] = "openai-chat-completions"
        inference_contract["request"]["requestFieldPolicySha256"] = orchestrate.outcome_task.inference_policy_sha256(inference_contract)
        inference_payload = json.dumps(inference_contract).encode("utf-8")
        inference_path.write_bytes(inference_payload)
        task["bindings"]["sharedInferenceContract"].update({
            "sha256": digest(inference_payload), "bytes": len(inference_payload),
        })
        task_path.write_text(json.dumps(task), encoding="utf-8")
        if orchestrate.os.name != "nt":
            task_path.chmod(0o600)
        return task_path, model_contract, model_root, manifest_path, vllm_arguments

    def assistant_fixture(self, parent: Path):
        task_path, model_contract, artifact_path = self.fixture(parent)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        journey = next(
            item for item in json.loads(
                (ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8")
            )["journeys"]
            if item["id"] == "assistant-harness-challenge"
        )
        verifier = {
            "$schema": orchestrate.verifier_engine.VERIFIER_SCHEMA,
            "operation": orchestrate.verifier_engine.VERIFIER_OPERATION,
            "schemaVersion": 1,
            "journeyId": journey["id"],
            "acceptanceCriteria": ["Complete the admitted workspace task and pass controller-selected checks."],
            "checks": [
                {"assertionId": "real-backend-required", "check": "command-exit-zero"},
                {"assertionId": "real-tool-outcome", "check": "workspace-verification-passes"},
                {"assertionId": "no-scripted-answer-credit", "check": "workspace-verification-passes"},
                {"assertionId": "independent-completion", "check": "workspace-verification-passes"},
                {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
            ],
            "forbiddenPhrases": [],
            "finalReply": None,
            "workspaceVerification": None,
            "boundary": orchestrate.verifier_engine.VERIFIER_BOUNDARY,
        }
        verifier_payload = json.dumps(verifier, separators=(",", ":")).encode("utf-8")
        (parent / "verifier.json").write_bytes(verifier_payload)
        task["bindings"]["verifier"].update({
            "sha256": digest(verifier_payload), "bytes": len(verifier_payload),
        })
        task["journeyId"] = journey["id"]
        task["profile"] = "assistant"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        return task_path, model_contract, artifact_path

    def controller_fixture(self, parent: Path):
        task_path, model_contract, artifact_path = self.fixture(parent)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        journey = next(
            item for item in json.loads(
                (ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8")
            )["journeys"] if item["id"] == "inspectable-result-evidence"
        )
        verifier = {
            "$schema": orchestrate.verifier_engine.VERIFIER_SCHEMA,
            "operation": orchestrate.verifier_engine.VERIFIER_OPERATION, "schemaVersion": 1,
            "journeyId": journey["id"], "acceptanceCriteria": [journey["objective"]],
            "checks": [
                {"assertionId": "artifact-openable", "check": "artifact-parses"},
                {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
                {"assertionId": "independent-completion", "check": "workspace-verification-passes"},
                {"assertionId": "no-scripted-answer-credit", "check": "workspace-verification-passes"},
            ],
            "forbiddenPhrases": [], "finalReply": None, "workspaceVerification": None,
            "boundary": orchestrate.verifier_engine.VERIFIER_BOUNDARY,
        }
        verifier_payload = json.dumps(verifier, separators=(",", ":")).encode("utf-8")
        (parent / "verifier.json").write_bytes(verifier_payload)
        task["bindings"]["verifier"].update({"sha256": digest(verifier_payload), "bytes": len(verifier_payload)})
        task["journeyId"] = journey["id"]
        task["profile"] = "controller"
        task["dataClass"] = journey["dataClass"]
        task["effectBoundary"] = journey["effect"]
        task_path.write_text(json.dumps(task), encoding="utf-8")
        return task_path, model_contract, artifact_path

    @staticmethod
    def scorer(evidence, _artifacts):
        pair = next(item for item in evidence if item["type"] == "command-exit")
        return [
            {"id": item, "score": 4, "evidencePath": pair["relativePath"], "evidenceSha256": pair["sha256"]}
            for item in orchestrate.evaluation.DIMENSIONS
        ]

    def test_orchestrated_codex_run_passes_the_fail_closed_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model_contract, artifact_path = self.fixture(parent)
            run_dir = parent / "run"
            run_dir.mkdir(mode=0o700)
            system = FakeSystem(model_contract)
            record = orchestrate.orchestrate_codex_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir,
                run_id="outcomerun-1786550400003-aaaaaaaaaaac",
                runtime_condition="cold-first-request",
                artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                harness_contract_sha256="9" * 64, dimensions=self.scorer,
                clock=iter(["2026-08-12T12:00:00Z", "2026-08-12T12:05:00Z"]).__next__,
            )
            self.assertEqual(record["backend"], "codex")
            self.assertEqual(record["execution"]["modelRequests"], 3)
            self.assertEqual(record["execution"]["toolCalls"], 1)
            run_path = run_dir / "run.json"
            if orchestrate.os.name != "nt":
                run_path.chmod(0o600)
            validated = orchestrate.evaluation.validate_run(ROOT, run_path, "codex")
            self.assertEqual(validated["task"]["comparisonLane"], "same-model-harness")
            self.assertTrue(all(item["status"] == "pass" for item in validated["assertions"]))
            self.assertTrue(any(call.startswith("teardown:") for call in system.calls))
            self.assertLess(system.calls.index("wait_healthy"), system.calls.index("run_codex"))

            counterless_dir = parent / "run-counterless"
            counterless_dir.mkdir(mode=0o700)
            counterless = FakeSystem(model_contract)
            counterless.request_counts = [None, None]
            counterless.boundary_requests = 1
            fallback = orchestrate.orchestrate_codex_run(
                counterless, root=ROOT, task_path=task_path, run_dir=counterless_dir,
                run_id="outcomerun-1786550400004-aaaaaaaaaaad",
                runtime_condition="cold-first-request",
                artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                harness_contract_sha256="9" * 64, dimensions=self.scorer,
                clock=iter(["2026-08-12T12:00:00Z", "2026-08-12T12:05:00Z"]).__next__,
            )
            self.assertEqual(fallback["execution"]["modelRequests"], 1)
            command_exit = json.loads((counterless_dir / "evidence/command-exit.json").read_text(encoding="utf-8"))
            self.assertEqual(command_exit["modelRequestsSource"], "inference-boundary-receipt")
            self.assertEqual(command_exit["inputTokens"], 5946)
            self.assertEqual(command_exit["outputTokens"], 7)

            mismatched_dir = parent / "run-usage-mismatch"
            mismatched_dir.mkdir(mode=0o700)
            mismatched = FakeSystem(model_contract)
            mismatched.boundary_requests = 3
            original_receipt = mismatched.inference_boundary_receipt
            def wrong_usage(container, expected):
                value = original_receipt(container, expected)
                value["outputTokens"] = 8
                return value
            mismatched.inference_boundary_receipt = wrong_usage
            with self.assertRaisesRegex(orchestrate.evaluation.OutcomeError, "usage disagree"):
                orchestrate.orchestrate_codex_run(
                    mismatched, root=ROOT, task_path=task_path, run_dir=mismatched_dir,
                    run_id="outcomerun-1786550400007-aaaaaaaaaaab",
                    runtime_condition="cold-first-request",
                    artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                    harness_contract_sha256="9" * 64, dimensions=self.scorer,
                    clock=iter(["2026-08-12T12:00:00Z", "2026-08-12T12:05:00Z"]).__next__,
                )
            self.assertTrue(any(call.startswith("teardown:") for call in mismatched.calls))

    def test_orchestrated_codex_run_accepts_an_exact_vllm_directory_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model_contract, model_root, manifest_path, arguments = self.directory_fixture(parent)
            run_dir = parent / "run"
            run_dir.mkdir(mode=0o700)
            system = FakeSystem(model_contract)
            record = orchestrate.orchestrate_codex_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir,
                run_id="outcomerun-1786550400005-aaaaaaaaaaae",
                runtime_condition="warm-neutral-probe",
                artifact_path=model_root, artifact_manifest_path=manifest_path,
                launch_arguments=arguments, harness_contract_sha256="9" * 64,
                dimensions=self.scorer,
                clock=iter(["2026-08-12T12:00:00Z", "2026-08-12T12:05:00Z"]).__next__,
            )
            self.assertEqual(record["execution"]["status"], "completed")
            self.assertIn("get:/v1/models", system.calls)
            self.assertIn("get-text:/metrics", system.calls)

            blocked_dir = parent / "run-missing-manifest"
            blocked_dir.mkdir(mode=0o700)
            blocked = FakeSystem(model_contract)
            with self.assertRaisesRegex(orchestrate.evaluation.OutcomeError, "requires a manifest path"):
                orchestrate.orchestrate_codex_run(
                    blocked, root=ROOT, task_path=task_path, run_dir=blocked_dir,
                    run_id="outcomerun-1786550400006-aaaaaaaaaaaf",
                    runtime_condition="cold-first-request",
                    artifact_path=model_root, launch_arguments=arguments,
                    harness_contract_sha256="9" * 64, dimensions=self.scorer,
                    clock=lambda: "2026-08-12T12:00:00Z",
                )
            self.assertEqual(blocked.calls, [])

    def test_assistant_codex_path_emits_exact_matched_authority_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model_contract, artifact_path = self.assistant_fixture(parent)
            run_dir = parent / "assistant-run"
            run_dir.mkdir(mode=0o700)
            independent = {
                "schemaVersion": 1,
                "format": "codex-neutral-independent-verification-v1",
                "sourceSnapshotSha256": json.loads(task_path.read_text(encoding="utf-8"))["bindings"]["sourceSnapshot"]["sha256"],
                "candidateSha256": "a" * 64,
                "status": "pass",
                "checks": [{"id": "semantic", "kind": "command", "status": "pass"}],
                "criteria": [{"index": 0, "status": "pass", "checkIds": ["semantic"]}],
                "network": "none",
                "workerSelectedChecks": False,
                "externalEffects": False,
            }
            system = FakeSystem(
                model_contract, expected_profile="assistant", independent_verification=independent,
            )
            record = orchestrate.orchestrate_codex_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir,
                run_id="outcomerun-1786550400008-aaaaaaaaaaba",
                runtime_condition="cold-first-request",
                artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                harness_contract_sha256="9" * 64, dimensions=self.scorer,
                clock=iter(["2026-08-12T12:00:00Z", "2026-08-12T12:05:00Z"]).__next__,
            )
            self.assertEqual({item["type"] for item in record["evidence"]}, {
                "exact-source", "runtime-environment", "command-exit", "checkpoint-lineage",
                "privacy-route", "independent-verifier",
            })
            self.assertTrue(all(item["status"] == "pass" for item in record["assertions"]))
            privacy = json.loads((run_dir / "evidence/assistant-privacy-route.json").read_text(encoding="utf-8"))
            self.assertEqual(privacy["routes"], ["local-model-boundary", "local-workspace"])
            self.assertFalse(privacy["privateDataSentRemote"])
            checkpoint = json.loads((run_dir / "evidence/assistant-checkpoint-lineage.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["completedTurns"], 1)
            self.assertTrue(checkpoint["lineageValidated"])
            run_path = run_dir / "run.json"
            if orchestrate.os.name != "nt":
                run_path.chmod(0o600)
            validated = orchestrate.evaluation.validate_run(ROOT, run_path, "codex")
            self.assertEqual(validated["task"]["dataRoute"], "local-only")
            self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["profile"], "assistant")

    def test_controller_codex_path_is_profile_exact_and_emits_only_journey_required_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model_contract, artifact_path = self.controller_fixture(parent)
            run_dir = parent / "controller-run"; run_dir.mkdir(mode=0o700)
            independent = {
                "schemaVersion": 1, "format": "codex-neutral-independent-verification-v1",
                "sourceSnapshotSha256": json.loads(task_path.read_text(encoding="utf-8"))["bindings"]["sourceSnapshot"]["sha256"],
                "candidateSha256": "a" * 64, "status": "pass",
                "checks": [{"id": "semantic", "kind": "command", "status": "pass"}],
                "criteria": [{"index": 0, "status": "pass", "checkIds": ["semantic"]}],
                "network": "none", "workerSelectedChecks": False, "externalEffects": False,
            }
            system = FakeSystem(model_contract, expected_profile="controller", independent_verification=independent)

            def controller_scorer(evidence, _artifacts):
                item = next(value for value in evidence if value["type"] == "independent-verifier")
                return [
                    {"id": name, "score": 4, "evidencePath": item["relativePath"], "evidenceSha256": item["sha256"]}
                    for name in orchestrate.evaluation.DIMENSIONS
                ]

            record = orchestrate.orchestrate_codex_run(
                system, root=ROOT, task_path=task_path, run_dir=run_dir,
                run_id="outcomerun-1786550400009-aaaaaaaaaabb", runtime_condition="cold-first-request",
                artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                harness_contract_sha256="9" * 64, dimensions=controller_scorer,
                clock=iter(["2026-08-12T12:00:00Z", "2026-08-12T12:05:00Z"]).__next__,
            )
            self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["profile"], "controller")
            self.assertEqual({item["type"] for item in record["evidence"]}, {
                "exact-source", "runtime-environment", "artifact-digest", "independent-verifier",
            })
            self.assertTrue(all(item["status"] == "pass" for item in record["assertions"]))
            self.assertTrue((run_dir / "evidence/controller-checkpoint-lineage.json").is_file())
            self.assertTrue(any(call.startswith("teardown:") for call in system.calls))

    def test_teardown_runs_on_failure_and_refusals_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path, model_contract, artifact_path = self.fixture(parent)
            run_dir = parent / "run"
            run_dir.mkdir(mode=0o700)
            system = FakeSystem(model_contract)
            system.fail_health = True
            with self.assertRaisesRegex(orchestrate.evaluation.OutcomeError, "never became healthy"):
                orchestrate.orchestrate_codex_run(
                    system, root=ROOT, task_path=task_path, run_dir=run_dir,
                    run_id="outcomerun-1786550400003-aaaaaaaaaaac",
                    runtime_condition="cold-first-request",
                    artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                    harness_contract_sha256="9" * 64, dimensions=self.scorer,
                    clock=lambda: "2026-08-12T12:00:00Z",
                )
            self.assertTrue(any(call.startswith("teardown:") for call in system.calls))

            system = FakeSystem(model_contract)
            with self.assertRaisesRegex(orchestrate.evaluation.OutcomeError, "refusing to fabricate"):
                orchestrate.orchestrate_codex_run(
                    system, root=ROOT, task_path=task_path, run_dir=run_dir,
                    run_id="outcomerun-1786550400003-aaaaaaaaaaac",
                    runtime_condition="cold-first-request",
                    artifact_path=artifact_path, launch_arguments=list(LAUNCH_ARGUMENTS),
                    harness_contract_sha256="9" * 64, dimensions=None,
                    clock=lambda: "2026-08-12T12:00:00Z",
                )
            self.assertEqual(system.calls, [])

            tampered = parent / "model.gguf"
            tampered.write_bytes(b"substituted bytes\n")
            system = FakeSystem(model_contract)
            with self.assertRaises(orchestrate.evaluation.OutcomeError):
                orchestrate.orchestrate_codex_run(
                    system, root=ROOT, task_path=task_path, run_dir=run_dir,
                    run_id="outcomerun-1786550400003-aaaaaaaaaaac",
                    runtime_condition="cold-first-request",
                    artifact_path=tampered, launch_arguments=list(LAUNCH_ARGUMENTS),
                    harness_contract_sha256="9" * 64, dimensions=self.scorer,
                    clock=lambda: "2026-08-12T12:00:00Z",
                )
            self.assertNotIn("network_create", system.calls)


if __name__ == "__main__":
    unittest.main()
