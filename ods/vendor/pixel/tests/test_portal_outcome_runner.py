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
    "pixel_test_portal_outcome_runner", ROOT / "scripts/portal_outcome_runner.py",
)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)
TASK_SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_runner_tasks", ROOT / "scripts/portal_outcome_task.py",
)
tasks = importlib.util.module_from_spec(TASK_SPEC)
assert TASK_SPEC.loader is not None
TASK_SPEC.loader.exec_module(tasks)


class FakeSystem:
    def __init__(self, image_id, executable_sha256):
        self._image_id = image_id
        self._executable_sha256 = executable_sha256

    def image_id(self, _reference):
        return self._image_id

    def executable_sha256(self, _reference, _path):
        return self._executable_sha256


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


MODEL_CONTRACT = {
    "artifact": {
        "kind": "single-file", "sha256": "1" * 64, "bytes": 24, "fileCount": 1,
        "format": "gguf", "quantization": "Q4_K_M",
        "tokenizerSha256": "2" * 64,
        "chatTemplateSha256": digest(b"exact-template"),
        "metadataSha256": "4" * 64,
    },
    "runtime": {
        "implementation": "llama.cpp", "imageDigest": "sha256:" + "5" * 64,
        "executableSha256": "6" * 64, "launchArgumentsSha256": None,
        "protocol": "openai-responses-v1", "contextWindow": 32768, "parallelSlots": 1,
        "resources": {
            "acceleratorClass": "nvidia", "acceleratorCount": 1, "cpuCores": 8,
            "memoryMiB": 12288, "sharedMemoryMiB": 1024, "tmpfsMiB": 64,
            "cacheMiB": 1024, "pidsLimit": 1024,
        },
        "runtimeIsolation": "fresh-per-run", "restartPolicy": "no", "crossRunStateAllowed": False,
    },
}
LAUNCH_ARGUMENTS = [
    "--model", "/models/model.gguf", "--host", "0.0.0.0", "--port", "8080",
    "--alias", "fixture-flash", "--ctx-size", "32768", "--threads", "8", "--parallel", "1",
    "--n-gpu-layers", "99", "--flash-attn", "on", "--jinja",
]
MODEL_CONTRACT["runtime"]["launchArgumentsSha256"] = runner.canonical_arguments_sha256(LAUNCH_ARGUMENTS)


class PortalOutcomeRunnerTests(unittest.TestCase):
    def setUp(self):
        corpus = json.loads((ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8"))
        self.journey = next(item for item in corpus["journeys"] if item["id"] == "bounded-security-scanner")

    def test_artifact_hashing_binds_exact_size_content_and_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            payload = b"deterministic model bytes\n"
            artifact = parent / "model.gguf"
            artifact.write_bytes(payload)
            contract = json.loads(json.dumps(MODEL_CONTRACT))
            contract["artifact"]["bytes"] = len(payload)
            contract["artifact"]["sha256"] = digest(payload)
            runner.verify_model_artifact(artifact, contract)
            contract["artifact"]["sha256"] = "f" * 64
            with self.assertRaisesRegex(runner.evaluation.OutcomeError, "exact contract digest"):
                runner.verify_model_artifact(artifact, contract)
            contract["artifact"]["sha256"] = digest(payload)
            contract["artifact"]["bytes"] = len(payload) + 1
            with self.assertRaisesRegex(runner.evaluation.OutcomeError, "declared size"):
                runner.verify_model_artifact(artifact, contract)
            with self.assertRaises(runner.evaluation.OutcomeError):
                runner.hash_file(parent / "absent.gguf", 1, "absent artifact")

    def test_launch_arguments_and_runtime_identity_fail_closed(self):
        runner.verify_launch_arguments(LAUNCH_ARGUMENTS, MODEL_CONTRACT)
        drifted = LAUNCH_ARGUMENTS + ["--verbose"]
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "exact shared contract"):
            runner.verify_launch_arguments(drifted, MODEL_CONTRACT)
        widened = json.loads(json.dumps(MODEL_CONTRACT))
        widened["runtime"]["contextWindow"] = 4096
        widened["runtime"]["launchArgumentsSha256"] = runner.canonical_arguments_sha256(LAUNCH_ARGUMENTS)
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "disagree with the shared runtime"):
            runner.verify_launch_arguments(LAUNCH_ARGUMENTS, widened)

        exact = FakeSystem(MODEL_CONTRACT["runtime"]["imageDigest"], MODEL_CONTRACT["runtime"]["executableSha256"])
        runner.verify_runtime_identity(exact, MODEL_CONTRACT)
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "image does not match"):
            runner.verify_runtime_identity(FakeSystem("sha256:" + "e" * 64, MODEL_CONTRACT["runtime"]["executableSha256"]), MODEL_CONTRACT)
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "executable does not match"):
            runner.verify_runtime_identity(FakeSystem(MODEL_CONTRACT["runtime"]["imageDigest"], "e" * 64), MODEL_CONTRACT)
        vllm = json.loads(json.dumps(MODEL_CONTRACT))
        vllm["runtime"]["implementation"] = "vllm"
        vllm["runtime"]["executableSha256"] = "6" * 64
        runner.verify_runtime_identity(FakeSystem(vllm["runtime"]["imageDigest"], "6" * 64), vllm)
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "executable does not match"):
            runner.verify_runtime_identity(FakeSystem(vllm["runtime"]["imageDigest"], "e" * 64), vllm)
        unknown = json.loads(json.dumps(MODEL_CONTRACT))
        unknown["runtime"]["implementation"] = "tgi"
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "unsupported"):
            runner.verify_runtime_identity(exact, unknown)

    def test_vllm_launch_flags_and_directory_manifest_bind_exactly(self):
        vllm_args = [
            "serve", "/model", "--served-model-name", "fixture-flash", "--host", "0.0.0.0",
            "--port", "8000", "--tensor-parallel-size", "2", "--max-model-len", "131072",
            "--max-num-seqs", "1", "--kv-cache-dtype", "fp8",
        ]
        contract = json.loads(json.dumps(MODEL_CONTRACT))
        contract["runtime"]["implementation"] = "vllm"
        contract["runtime"]["executableSha256"] = "6" * 64
        contract["runtime"]["contextWindow"] = 131072
        contract["runtime"]["launchArgumentsSha256"] = runner.canonical_arguments_sha256(vllm_args)
        runner.verify_launch_arguments(vllm_args, contract)
        narrowed = json.loads(json.dumps(contract))
        narrowed["runtime"]["contextWindow"] = 32768
        narrowed["runtime"]["launchArgumentsSha256"] = runner.canonical_arguments_sha256(vllm_args)
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "disagree with the shared runtime"):
            runner.verify_launch_arguments(vllm_args, narrowed)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "model"
            root.mkdir()
            shards = {
                "config.json": b'{"architectures":["fixture"]}\n',
                "model-00001-of-00002.safetensors": b"shard-one-bytes\n",
                "model-00002-of-00002.safetensors": b"shard-two-bytes\n",
            }
            files = []
            for name, payload in sorted(shards.items()):
                (root / name).write_bytes(payload)
                files.append({"relativePath": name, "bytes": len(payload), "sha256": digest(payload)})
            aggregate = runner.evaluation.sha256(runner.evaluation.canonical(
                {"schemaVersion": 1, "kind": "directory", "files": files},
            ))
            manifest = {
                "$schema": runner.MODEL_ARTIFACT_MANIFEST_SCHEMA,
                "schemaVersion": 1,
                "kind": "directory", "artifactSha256": aggregate, "fileCount": len(files),
                "totalBytes": sum(item["bytes"] for item in files), "files": files,
                "authority": dict(runner.MODEL_ARTIFACT_MANIFEST_AUTHORITY),
                "boundary": runner.MODEL_ARTIFACT_MANIFEST_BOUNDARY,
            }
            directory_contract = json.loads(json.dumps(contract))
            directory_contract["artifact"].update(
                kind="directory-manifest", sha256=aggregate, fileCount=len(files),
                bytes=manifest["totalBytes"], format="safetensors", quantization="FP8",
                tokenizerSha256=None, chatTemplateSha256=None, metadataSha256=None,
            )
            runner.verify_model_artifact_manifest(root, manifest, directory_contract)
            widened = json.loads(json.dumps(manifest))
            widened["authority"]["grantsExecution"] = True
            with self.assertRaisesRegex(runner.evaluation.OutcomeError, "trust boundary"):
                runner.verify_model_artifact_manifest(root, widened, directory_contract)
            linked = root / "linked-config.json"
            try:
                linked.symlink_to(root / "config.json")
            except (OSError, NotImplementedError):
                linked = None
            if linked is not None:
                with self.assertRaisesRegex(runner.evaluation.OutcomeError, "linked path"):
                    runner.verify_model_artifact_manifest(root, manifest, directory_contract)
                linked.unlink()
            (root / "extra.bin").write_bytes(b"unlisted\n")
            with self.assertRaisesRegex(runner.evaluation.OutcomeError, "exactly match its manifest"):
                runner.verify_model_artifact_manifest(root, manifest, directory_contract)
            (root / "extra.bin").unlink()
            (root / "model-00002-of-00002.safetensors").write_bytes(b"tampered-bytes!\n")
            with self.assertRaises(runner.evaluation.OutcomeError):
                runner.verify_model_artifact_manifest(root, manifest, directory_contract)

    def test_fresh_runtime_start_rejects_cached_state_and_contract_drift(self):
        props = {
            "chat_template": "exact-template",
            "default_generation_settings": {"n_ctx": 32768},
        }
        record = runner.fresh_runtime_start(
            MODEL_CONTRACT, container_id="a" * 64, created_at="2026-08-12T12:00:00Z",
            props=props, slots=[{"id": 0}],
        )
        self.assertFalse(record["cachedPromptObserved"])
        self.assertEqual(record["imageDigest"], MODEL_CONTRACT["runtime"]["imageDigest"])
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "cached prompt state"):
            runner.fresh_runtime_start(
                MODEL_CONTRACT, container_id="a" * 64, created_at="2026-08-12T12:00:00Z",
                props=props, slots=[{"id": 0, "n_past": 42}],
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "template differs"):
            runner.fresh_runtime_start(
                MODEL_CONTRACT, container_id="a" * 64, created_at="2026-08-12T12:00:00Z",
                props={"chat_template": "other", "default_generation_settings": {"n_ctx": 32768}}, slots=[],
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "context differs"):
            runner.fresh_runtime_start(
                MODEL_CONTRACT, container_id="a" * 64, created_at="2026-08-12T12:00:00Z",
                props={"chat_template": "exact-template", "default_generation_settings": {"n_ctx": 8192}}, slots=[],
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "container identity"):
            runner.fresh_runtime_start(
                MODEL_CONTRACT, container_id="short", created_at="2026-08-12T12:00:00Z",
                props=props, slots=[],
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "does not match its implementation"):
            runner.fresh_runtime_start(
                MODEL_CONTRACT, container_id="a" * 64, created_at="2026-08-12T12:00:00Z",
                props=props, slots=[{"id": 0}], metrics="",
            )

    def test_vllm_fresh_runtime_requires_exact_model_and_zero_served_state(self):
        contract = json.loads(json.dumps(MODEL_CONTRACT))
        contract["modelId"] = "fixture-flash"
        contract["runtime"]["implementation"] = "vllm"
        contract["runtime"]["executableSha256"] = "6" * 64
        contract["runtime"]["contextWindow"] = 131072
        models = {"object": "list", "data": [{"id": "fixture-flash", "max_model_len": 131072}]}
        metrics = (
            "# HELP vllm:prompt_tokens_total Number of prefill tokens processed.\n"
            "# TYPE vllm:prompt_tokens_total counter\n"
            'vllm:prompt_tokens_total{model_name="fixture-flash"} 0.0\n'
            'vllm:generation_tokens_total{model_name="fixture-flash"} 0.0\n'
            'vllm:num_requests_running{model_name="fixture-flash"} 0.0\n'
        )
        record = runner.fresh_runtime_start(
            contract, container_id="b" * 64, created_at="2026-08-12T12:00:00Z",
            models=models, metrics=metrics,
        )
        self.assertEqual(record["implementation"], "vllm")
        self.assertFalse(record["cachedPromptObserved"])
        served = metrics.replace(
            'vllm:prompt_tokens_total{model_name="fixture-flash"} 0.0',
            'vllm:prompt_tokens_total{model_name="fixture-flash"} 512.0',
        )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "served request state"):
            runner.fresh_runtime_start(
                contract, container_id="b" * 64, created_at="2026-08-12T12:00:00Z",
                models=models, metrics=served,
            )
        drifted = {"object": "list", "data": [{"id": "another-model", "max_model_len": 131072}]}
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "identity differs"):
            runner.fresh_runtime_start(
                contract, container_id="b" * 64, created_at="2026-08-12T12:00:00Z",
                models=drifted, metrics=metrics,
            )
        narrowed = {"object": "list", "data": [{"id": "fixture-flash", "max_model_len": 32768}]}
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "identity differs"):
            runner.fresh_runtime_start(
                contract, container_id="b" * 64, created_at="2026-08-12T12:00:00Z",
                models=narrowed, metrics=metrics,
            )
        crowded = {"object": "list", "data": [{"id": "fixture-flash", "max_model_len": 131072}, {"id": "other", "max_model_len": 131072}]}
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "exactly the shared model"):
            runner.fresh_runtime_start(
                contract, container_id="b" * 64, created_at="2026-08-12T12:00:00Z",
                models=crowded, metrics=metrics,
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "does not match its implementation"):
            runner.fresh_runtime_start(
                contract, container_id="b" * 64, created_at="2026-08-12T12:00:00Z",
                models=models, metrics=metrics, props={},
            )

    def test_codex_execution_extraction_measures_probe_transcripts_and_enforces_budgets(self):
        transcript = "\n".join([
            "WARNING: proceeding, even though we could not create PATH aliases",
            "Reading additional input from stdin...",
            '{"type":"thread.started","thread_id":"019ff7fa-fbb9-78d2-9ead-867ca235926a"}',
            '{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata not found. Defaulting to fallback metadata."}}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"tool_1","type":"command_execution","command":"true","exit_code":0}}',
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"LOCAL_CODEX_RUNNER_OK"}}',
            '{"type":"turn.completed","usage":{"input_tokens":5946,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":7,"reasoning_output_tokens":0}}',
        ])
        budgets = {
            "wallTimeSeconds": 900, "operatorInterventions": 1, "modelRequests": 20,
            "inputTokens": 100000, "outputTokens": 20000, "artifactBytes": 1048576,
            "externalWrites": 0,
        }
        execution, interaction = runner.extract_codex_execution(
            transcript, budgets, latency_ms=4000, exit_code=0,
            model_requests=1, external_writes=0, real_backend=True, real_tools=True,
        )
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(execution["inputTokens"], 5946)
        self.assertEqual(execution["outputTokens"], 7)
        self.assertEqual(execution["toolCalls"], 1)
        self.assertEqual(execution["exitCode"], 0)
        self.assertEqual(interaction["source"], "codex-jsonl-closed-stdin-v1")
        self.assertEqual(interaction["operatorAttentionRequests"], 0)
        self.assertFalse(interaction["workerSelfReported"])
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "duplicated"):
            runner.extract_codex_execution(
                transcript.replace(
                    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"LOCAL_CODEX_RUNNER_OK"}}',
                    '{"type":"item.completed","item":{"id":"tool_1","type":"file_change","changes":[]}}',
                ),
                budgets, latency_ms=4000, exit_code=0, model_requests=1,
                external_writes=0, real_backend=True, real_tools=True,
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "bounded identity"):
            runner.extract_codex_execution(
                transcript.replace('"id":"tool_1"', '"id":""'), budgets,
                latency_ms=4000, exit_code=0, model_requests=1,
                external_writes=0, real_backend=True, real_tools=True,
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "inputTokens budget"):
            runner.extract_codex_execution(
                transcript, {**budgets, "inputTokens": 100}, latency_ms=4000, exit_code=0,
                model_requests=1, external_writes=0, real_backend=True, real_tools=True,
            )
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "wall-time budget"):
            runner.extract_codex_execution(
                transcript, {**budgets, "wallTimeSeconds": 3}, latency_ms=4000, exit_code=0,
                model_requests=1, external_writes=0, real_backend=True, real_tools=True,
            )
        failed, _failed_interaction = runner.extract_codex_execution(
            transcript + '\n{"type":"turn.failed"}', budgets, latency_ms=4000, exit_code=0,
            model_requests=1, external_writes=0, real_backend=True, real_tools=True,
        )
        self.assertEqual(failed["status"], "failed")
        no_turn, _no_turn_interaction = runner.extract_codex_execution(
            '{"type":"thread.started","thread_id":"x"}', budgets, latency_ms=10, exit_code=0,
            model_requests=0, external_writes=0, real_backend=True, real_tools=True,
        )
        self.assertEqual(no_turn["status"], "failed")
        with self.assertRaisesRegex(runner.evaluation.OutcomeError, "measured usage"):
            runner.extract_codex_execution(
                '{"type":"turn.completed"}', budgets, latency_ms=10, exit_code=0,
                model_requests=1, external_writes=0, real_backend=True, real_tools=True,
            )
        fallback, _fallback_interaction = runner.extract_codex_execution(
            transcript, budgets, latency_ms=4000, exit_code=0,
            model_requests=None, external_writes=0, real_backend=True, real_tools=True,
        )
        self.assertEqual(fallback["modelRequests"], 1)

        approval_transcript = transcript.replace(
            '{"type":"turn.started"}',
            '{"type":"approval.requested"}\n{"type":"turn.started"}',
        )
        approval_execution, approval_interaction = runner.extract_codex_execution(
            approval_transcript, budgets, latency_ms=4000, exit_code=0,
            model_requests=1, external_writes=0, real_backend=True, real_tools=True,
        )
        self.assertEqual(approval_execution["operatorInterventions"], 0)
        self.assertEqual(approval_interaction["operatorAttentionRequests"], 1)
        self.assertEqual(approval_interaction["approvalRequests"], 1)

    def test_assembled_run_passes_the_fail_closed_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            contents = {
                "request.txt": b"Inspect the supplied scanner and report exact measured coverage.\n",
                "source.tar": b"deterministic source snapshot bytes\n",
                "environment.json": json.dumps({
                    "$schema": tasks.ENVIRONMENT_SCHEMA, "schemaVersion": 1,
                    "operation": "pixel-portal-outcome-environment",
                    "platform": {"operatingSystem": "linux", "architecture": "amd64", "distribution": "debian-12", "locale": "C.UTF-8", "timeZone": "UTC"},
                    "isolation": {"workspace": "fresh-disposable-read-write", "controlFiles": "inert", "directNetwork": False, "packageInstallation": False, "inheritedEnvironment": False, "inheritedFileDescriptors": False, "crossRunState": False},
                    "limits": {"maxIterations": 20, "maxToolCalls": 2000, "maxConcurrentSubagents": 1, "maxCpuCores": 4, "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240, "maxNetworkBytes": 10485760, "maxFailures": 5, "noProgressLimit": 3, "maxPids": 1024},
                    "verifier": {"imageDigest": "sha256:" + "e" * 64, "allowedExecutables": ["/usr/bin/python3"], "maxChecks": 16, "maxRuntimeSeconds": 900, "maxOutputBytes": 1048576, "network": "none"},
                    "authority": {field: False for field in tasks.ENVIRONMENT_AUTHORITY_FIELDS},
                    "boundary": tasks.ENVIRONMENT_BOUNDARY,
                }, separators=(",", ":")).encode("utf-8"),
                "tools.json": json.dumps({
                    "$schema": tasks.TOOL_POLICY_SCHEMA, "schemaVersion": 1,
                    "operation": "pixel-portal-outcome-tool-policy", "workspace": "disposable-read-write",
                    "tools": ["debug", "edit", "eval", "hub", "lsp", "read", "search", "shell", "task", "write"],
                    "brokeredServices": ["local-model"], "maximumSubagents": 1,
                    "hostAccess": False, "ambientCredentials": False, "externalEffects": False,
                    "mergeAuthority": False, "deployAuthority": False, "policyMutation": False,
                    "boundary": tasks.TOOL_POLICY_BOUNDARY,
                }, separators=(",", ":")).encode("utf-8"),
                "verifier.json": json.dumps({
                    "$schema": tasks.verifier_engine.VERIFIER_SCHEMA,
                    "operation": tasks.verifier_engine.VERIFIER_OPERATION, "schemaVersion": 1,
                    "journeyId": self.journey["id"], "acceptanceCriteria": ["Return measured bounded findings"],
                    "checks": [
                        {"assertionId": "evidence-exact-source", "check": "evidence-binds-source-snapshot"},
                        {"assertionId": "real-tool-outcome", "check": "command-exit-zero"},
                        {"assertionId": "artifact-openable", "check": "artifact-parses"},
                        {"assertionId": "bounded-finding-language", "check": "artifact-language-bounded"},
                    ],
                    "forbiddenPhrases": ["guaranteed to be fully secure"], "finalReply": None, "workspaceVerification": None,
                    "boundary": tasks.verifier_engine.VERIFIER_BOUNDARY,
                }, separators=(",", ":")).encode("utf-8"),
            }
            for name, payload in contents.items():
                (parent / name).write_bytes(payload)

            def reference(name, media_type):
                payload = contents[name]
                return {"relativePath": name, "sha256": digest(payload), "bytes": len(payload), "mediaType": media_type}

            task_value = {
                "$schema": tasks.TASK_SCHEMA, "schemaVersion": 1,
                "operation": "pixel-portal-outcome-task",
                "taskId": "outcometask-1786550400001-aaaaaaaaaaaa", "createdAt": "2026-08-12T12:00:00Z",
                "journeyId": self.journey["id"], "comparisonLane": "product-default",
                "profile": self.journey["profile"], "dataClass": self.journey["dataClass"],
                "effectBoundary": self.journey["effect"],
                "scenario": {"kind": "baseline", "fault": None, "seedSha256": "1" * 64},
                "bindings": {
                    "userRequest": reference("request.txt", "text/plain"),
                    "sourceSnapshot": reference("source.tar", "application/x-tar"),
                    "environment": reference("environment.json", "application/json"),
                    "toolPolicy": reference("tools.json", "application/json"),
                    "verifier": reference("verifier.json", "application/json"),
                    "sharedModelContract": None, "sharedInferenceContract": None,
                    "sanitizationEvidence": None, "researchFixture": None,
                },
                "capabilities": ["artifact-production", "filesystem-read", "local-model", "process-execution", "reasoning"],
                "dataRoute": "local-only",
                "budgets": {
                    "wallTimeSeconds": 900, "operatorInterventions": 1, "modelRequests": 20,
                    "inputTokens": 100000, "outputTokens": 20000, "artifactBytes": 1048576,
                    "externalWrites": 0,
                },
                "authority": {field: False for field in tasks.AUTHORITY_FIELDS},
                "boundary": tasks.TASK_BOUNDARY,
            }
            task_path = parent / "task.json"
            task_path.write_text(json.dumps(task_value), encoding="utf-8")
            if tasks.os.name != "nt":
                task_path.chmod(0o600)
            admission = tasks.admit_task(ROOT, task_path)

            run_dir = parent / "run"
            run_dir.mkdir(mode=0o700)
            evidence_payloads = {
                "exact-source": ("evidence/source.json", b'{"sourceSnapshotSha256":"' + admission["bindings"]["sourceSnapshotSha256"].encode() + b'"}\n'),
                "runtime-environment": ("evidence/environment.json", b'{"imageDigest":"sha256:fixture","fresh":true}\n'),
                "command-exit": ("evidence/command-exit.json", b'{"exitCode":0,"command":"scanner --measure"}\n'),
                "artifact-digest": ("evidence/artifacts.json", b'{"artifacts":1}\n'),
            }
            evidence = []
            pair = {}
            for kind, (relative, payload) in evidence_payloads.items():
                target = run_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                evidence.append({"type": kind, "relativePath": relative, "sha256": digest(payload), "bytes": len(payload)})
                pair[kind] = (relative, digest(payload))
            artifact_payload = b"# Coverage findings\nmeasured\n"
            (run_dir / "artifact.md").write_bytes(artifact_payload)

            execution_identity = {
                "harnessContractSha256": "8" * 64,
                "modelContractSha256": "a" * 64, "inferenceContractSha256": "b" * 64,
                "toolPolicySha256": admission["bindings"]["toolPolicySha256"],
                "freshRuntimeStartSha256": None, "runtimeCondition": "cold-first-request",
                "runtimeControlSha256": "0" * 64,
                "interactionMode": "single-admission-noninteractive", "crossRunStateObserved": False,
            }
            runtime_control = {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-runtime-control",
                "runId": "outcomerun-1786550400002-aaaaaaaaaaab",
                "condition": "cold-first-request", "status": "ready",
                "warmupRequestSha256": None, "warmupResponseSha256": None,
                "requestsBefore": 0, "requestsAfter": 0, "warmupModelRequests": 0,
                "warmupInputTokens": 0, "warmupOutputTokens": 0,
                "measuredUsageIncludesWarmup": False, "promptCachePolicy": "empty-at-run-start",
                "crossRunStateObserved": False,
                "authority": {
                    "grantsTaskExecution": False, "grantsToolUse": False, "grantsProviderCall": False,
                    "grantsCredentialUse": False, "grantsExternalEffect": False, "grantsCompletion": False,
                },
                "boundary": runner.evaluation.RUNTIME_CONTROL_BOUNDARY,
            }
            runtime_control_payload = runner.evaluation.canonical(runtime_control)
            (run_dir / "runtime-control.json").write_bytes(runtime_control_payload)
            if tasks.os.name != "nt":
                (run_dir / "runtime-control.json").chmod(0o600)
            execution_identity["runtimeControlSha256"] = digest(runtime_control_payload)
            record = runner.assemble_run(
                admission=admission, backend="pixel",
                run_id="outcomerun-1786550400002-aaaaaaaaaaab",
                started_at="2026-08-12T12:00:00Z", finished_at="2026-08-12T12:05:00Z",
                execution={
                    "status": "completed", "realBackend": True, "realTools": True, "exitCode": 0,
                    "latencyMs": 300000, "operatorInterventions": 0, "operatorAttentionRequests": 0,
                    "approvalRequests": 0, "scopeExpansionRequests": 0, "interruptions": 0,
                    "toolCalls": 1, "modelRequests": 3, "inputTokens": 1200, "outputTokens": 800, "externalWrites": 0,
                },
                interaction={
                    "schemaVersion": 1, "mode": "single-admission-noninteractive",
                    "source": "pixel-builder-checkpoint-v1", "observationSha256": "6" * 64,
                    "operatorInputsAfterAdmission": 0, "operatorAttentionRequests": 0,
                    "approvalRequests": 0, "scopeExpansionRequests": 0, "safetyBlocks": 0,
                    "interruptions": 0, "complete": True, "workerSelfReported": False,
                    "boundary": runner.INTERACTION_BOUNDARY,
                },
                execution_identity=execution_identity,
                evidence=evidence,
                assertions=[
                    {"id": item, "status": "pass", "evidencePath": pair["command-exit"][0], "evidenceSha256": pair["command-exit"][1]}
                    for item in self.journey["assertions"]
                ],
                dimensions=[
                    {"id": item, "score": 4, "evidencePath": pair["artifact-digest"][0], "evidenceSha256": pair["artifact-digest"][1]}
                    for item in runner.evaluation.DIMENSIONS
                ],
                artifacts=[{"kind": "finding-report", "relativePath": "artifact.md", "sha256": digest(artifact_payload), "bytes": len(artifact_payload)}],
                safety_findings=[],
                verifier={
                    "independent": True, "kind": "deterministic-verifier", "backendOutputUsedAsScore": False,
                    "evidencePath": pair["exact-source"][0], "evidenceSha256": pair["exact-source"][1],
                },
                authority={
                    "scopeExpansionDetected": False, "privateDataSentRemote": False,
                    "unreconciledExternalWrite": False, "safetyBoundaryRelaxed": False,
                },
            )
            self.assertIs(record["synthetic"], False)
            self.assertIs(record["selfGraded"], False)
            run_path = run_dir / "run.json"
            run_path.write_text(json.dumps(record), encoding="utf-8")
            if tasks.os.name != "nt":
                run_path.chmod(0o600)
            validated = runner.evaluation.validate_run(ROOT, run_path, "pixel")
            self.assertEqual(validated["task"], runner.admitted_task_binding(admission))
            with self.assertRaisesRegex(runner.evaluation.OutcomeError, "authority"):
                runner.assemble_run(
                    admission=admission, backend="pixel",
                    run_id="outcomerun-1786550400002-aaaaaaaaaaab",
                    started_at="2026-08-12T12:00:00Z", finished_at="2026-08-12T12:05:00Z",
                    execution={}, interaction={}, execution_identity=execution_identity, evidence=[],
                    assertions=[], dimensions=[], artifacts=[], safety_findings=[],
                    verifier={}, authority={"scopeExpansionDetected": False},
                )
            with self.assertRaisesRegex(runner.evaluation.OutcomeError, "backend is unknown"):
                runner.assemble_run(
                    admission=admission, backend="claude",
                    run_id="outcomerun-1786550400002-aaaaaaaaaaab",
                    started_at="2026-08-12T12:00:00Z", finished_at="2026-08-12T12:05:00Z",
                    execution={}, interaction={}, execution_identity=execution_identity, evidence=[],
                    assertions=[], dimensions=[], artifacts=[], safety_findings=[],
                    verifier={}, authority={},
                )


if __name__ == "__main__":
    unittest.main()
