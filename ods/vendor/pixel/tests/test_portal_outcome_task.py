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
    "pixel_test_portal_outcome_task", ROOT / "scripts/portal_outcome_task.py",
)
tasks = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(tasks)


class PortalOutcomeTaskTests(unittest.TestCase):
    def setUp(self):
        corpus = json.loads((ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_text(encoding="utf-8"))
        self.journey = next(item for item in corpus["journeys"] if item["id"] == "bounded-security-scanner")

    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def write_private(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")
        if tasks.os.name != "nt":
            path.chmod(0o600)

    def fixture(self, parent: Path) -> tuple[Path, dict]:
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
                "journeyId": self.journey["id"],
                "acceptanceCriteria": ["Return measured bounded findings"],
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
            return {"relativePath": name, "sha256": self.digest(payload), "bytes": len(payload), "mediaType": media_type}

        value = {
            "$schema": tasks.TASK_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-task",
            "taskId": "outcometask-1786550400001-aaaaaaaaaaaa", "createdAt": "2026-08-12T12:00:00Z",
            "journeyId": self.journey["id"], "comparisonLane": "product-default", "profile": self.journey["profile"],
            "dataClass": self.journey["dataClass"], "effectBoundary": self.journey["effect"],
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
        path = parent / "task.json"
        self.write_private(path, value)
        return path, value

    def test_exact_task_admits_content_free_without_execution_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, value = self.fixture(parent)
            result = tasks.admit_task(ROOT, path)
            self.assertEqual(result["status"], "admitted-inert")
            self.assertEqual(result["taskSpecificationSha256"], self.digest(path.read_bytes()))
            self.assertEqual(result["journeyId"], self.journey["id"])
            self.assertEqual(result["comparisonLane"], "product-default")
            self.assertIsNone(result["bindings"]["sharedModelContractSha256"])
            self.assertEqual(result["bindings"]["sourceSnapshotSha256"], value["bindings"]["sourceSnapshot"]["sha256"])
            self.assertTrue(all(flag is False for flag in result["authority"].values()))
            self.assertFalse(any(result["privacy"].values()))
            encoded = json.dumps(result)
            self.assertNotIn(str(parent), encoded)
            self.assertNotIn("Inspect the supplied scanner", encoded)

    def test_tamper_link_unknown_fields_and_journey_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, value = self.fixture(parent)
            (parent / "source.tar").write_bytes(b"substituted\n")
            with self.assertRaisesRegex(tasks.evaluation.OutcomeError, "exact size and digest"):
                tasks.admit_task(ROOT, path)
            path, value = self.fixture(parent)
            value["profile"] = "researcher"
            self.write_private(path, value)
            with self.assertRaisesRegex(tasks.evaluation.OutcomeError, "differs from its journey"):
                tasks.admit_task(ROOT, path)
            value["profile"] = self.journey["profile"]
            value["unexpected"] = True
            self.write_private(path, value)
            with self.assertRaisesRegex(tasks.evaluation.OutcomeError, "missing or unknown"):
                tasks.admit_task(ROOT, path)
            linked = parent / "linked-task.json"
            try:
                linked.symlink_to(path)
            except OSError:
                return
            with self.assertRaises(tasks.evaluation.OutcomeError):
                tasks.evaluation.read_json(linked, "private outcome task", private=True)

    def test_effect_capability_route_budget_and_authority_cannot_widen(self):
        cases = []
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, base = self.fixture(parent)
            calendar = json.loads(json.dumps(base))
            calendar["capabilities"].insert(1, "calendar-write")
            cases.append((calendar, "write capability"))
            filesystem = json.loads(json.dumps(base))
            filesystem["capabilities"].insert(2, "filesystem-write")
            cases.append((filesystem, "filesystem-write"))
            remote = json.loads(json.dumps(base))
            remote["capabilities"].insert(-1, "remote-model")
            cases.append((remote, "remote model"))
            network = json.loads(json.dumps(base))
            network["capabilities"].insert(1, "public-web")
            cases.append((network, "network capability"))
            budget = json.loads(json.dumps(base))
            budget["budgets"]["externalWrites"] = 1
            cases.append((budget, "external-write budget"))
            authority = json.loads(json.dumps(base))
            authority["authority"]["grantsExecution"] = True
            cases.append((authority, "grant execution"))
            for value, label in cases:
                with self.subTest(label=label):
                    self.write_private(path, value)
                    with self.assertRaises(tasks.evaluation.OutcomeError):
                        tasks.admit_task(ROOT, path)

    def test_tool_policy_semantics_cannot_drift_from_task_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _path, base = self.fixture(parent)
            policy = json.loads((parent / "tools.json").read_text(encoding="utf-8"))
            cases = []
            read_only_mutation = json.loads(json.dumps(policy))
            read_only_mutation["workspace"] = "read-only"
            cases.append((read_only_mutation, base["capabilities"], "read-only workspace"))
            missing_model_service = json.loads(json.dumps(policy))
            missing_model_service["brokeredServices"] = []
            cases.append((missing_model_service, base["capabilities"], "broker services"))
            missing_process_surface = json.loads(json.dumps(policy))
            missing_process_surface["tools"] = [item for item in missing_process_surface["tools"] if item not in {"debug", "eval", "lsp", "shell"}]
            cases.append((missing_process_surface, base["capabilities"], "process surface"))
            undeclared_research = json.loads(json.dumps(policy))
            undeclared_research["brokeredServices"].append("public-research")
            undeclared_research["tools"].append("public-research")
            undeclared_research["tools"].sort()
            cases.append((undeclared_research, base["capabilities"], "broker services"))
            ambient = json.loads(json.dumps(policy))
            ambient["ambientCredentials"] = True
            cases.append((ambient, base["capabilities"], "ambient or external authority"))
            for value, capabilities, error in cases:
                with self.subTest(error=error):
                    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
                    with self.assertRaisesRegex(tasks.evaluation.OutcomeError, error):
                        tasks.validate_tool_policy(payload, capabilities)

    def test_unhashable_enum_values_fail_closed_as_domain_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, base = self.fixture(parent)
            cases = [
                ("profile", ["builder"]), ("dataClass", {"value": "public"}),
                ("effectBoundary", ["none"]), ("dataRoute", {"value": "local-only"}),
                ("comparisonLane", ["product-default"]),
            ]
            for key, bad in cases:
                with self.subTest(key=key):
                    value = json.loads(json.dumps(base))
                    value[key] = bad
                    self.write_private(path, value)
                    with self.assertRaises(tasks.evaluation.OutcomeError):
                        tasks.admit_task(ROOT, path)
            value = json.loads(json.dumps(base))
            value["scenario"]["kind"] = ["baseline"]
            self.write_private(path, value)
            with self.assertRaises(tasks.evaluation.OutcomeError):
                tasks.admit_task(ROOT, path)
            value = json.loads(json.dumps(base))
            value["bindings"]["userRequest"]["mediaType"] = ["text/plain"]
            self.write_private(path, value)
            with self.assertRaises(tasks.evaluation.OutcomeError):
                tasks.admit_task(ROOT, path)

    def test_same_model_lane_requires_exact_cross_run_isolated_contracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, value = self.fixture(parent)
            model = {
                "$schema": tasks.MODEL_SCHEMA, "schemaVersion": 1,
                "operation": "pixel-portal-outcome-model-contract", "modelId": "fixture-flash",
                "artifact": {
                    "kind": "single-file", "sha256": "1" * 64, "bytes": 4096, "fileCount": 1,
                    "format": "gguf", "quantization": "Q4_K_M",
                    "tokenizerSha256": "2" * 64, "chatTemplateSha256": "3" * 64, "metadataSha256": "4" * 64,
                },
                "runtime": {
                    "implementation": "llama.cpp", "imageDigest": "sha256:" + "5" * 64,
                    "executableSha256": "6" * 64, "launchArgumentsSha256": "7" * 64,
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
                "boundary": tasks.MODEL_BOUNDARY,
            }
            inference = {
                "$schema": tasks.INFERENCE_SCHEMA, "schemaVersion": 1,
                "operation": "pixel-portal-outcome-inference-contract",
                "sampling": {
                    "source": "request-boundary-enforced", "temperaturePermille": 700, "topPPermille": 950,
                    "topK": 40, "minPPermille": 50, "repeatPenaltyPermille": 1100, "seed": 42,
                    "reasoningEffort": "backend-default", "reasoningVisibility": "hidden",
                },
                "request": {
                    "wireApi": "openai-chat-completions", "stream": True, "maxOutputTokens": 4096, "toolEncoding": "function",
                    "requestFieldPolicySha256": "8" * 64, "promptCachePolicy": "empty-at-run-start",
                },
                "authority": {"grantsInference": False, "grantsToolUse": False, "grantsExecution": False, "grantsCompletion": False},
                "boundary": tasks.INFERENCE_BOUNDARY,
            }
            inference["request"]["requestFieldPolicySha256"] = tasks.inference_policy_sha256(inference)
            for name, contract in (("model.json", model), ("inference.json", inference)):
                payload = json.dumps(contract).encode("utf-8")
                (parent / name).write_bytes(payload)
                value["bindings"]["sharedModelContract" if name == "model.json" else "sharedInferenceContract"] = {
                    "relativePath": name, "sha256": self.digest(payload), "bytes": len(payload), "mediaType": "application/json",
                }
            value["comparisonLane"] = "same-model-harness"
            self.write_private(path, value)
            result = tasks.admit_task(ROOT, path)
            self.assertEqual(result["comparisonLane"], "same-model-harness")
            self.assertEqual(result["bindings"]["sharedModelContractSha256"], value["bindings"]["sharedModelContract"]["sha256"])
            inference["sampling"]["temperaturePermille"] = 701
            payload = json.dumps(inference).encode("utf-8")
            (parent / "inference.json").write_bytes(payload)
            value["bindings"]["sharedInferenceContract"].update(sha256=self.digest(payload), bytes=len(payload))
            self.write_private(path, value)
            with self.assertRaisesRegex(tasks.evaluation.OutcomeError, "field-policy digest"):
                tasks.admit_task(ROOT, path)
            inference["sampling"]["temperaturePermille"] = 700
            inference["request"]["requestFieldPolicySha256"] = tasks.inference_policy_sha256(inference)
            payload = json.dumps(inference).encode("utf-8")
            (parent / "inference.json").write_bytes(payload)
            value["bindings"]["sharedInferenceContract"].update(sha256=self.digest(payload), bytes=len(payload))
            model["runtime"]["crossRunStateAllowed"] = True
            payload = json.dumps(model).encode("utf-8")
            (parent / "model.json").write_bytes(payload)
            value["bindings"]["sharedModelContract"].update(sha256=self.digest(payload), bytes=len(payload))
            self.write_private(path, value)
            with self.assertRaisesRegex(tasks.evaluation.OutcomeError, "cross-run isolated"):
                tasks.admit_task(ROOT, path)

    def test_declared_fault_and_sanitized_remote_require_exact_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, value = self.fixture(parent)
            value["scenario"] = {"kind": "fault-injection", "fault": "malformed-output", "seedSha256": "2" * 64}
            value["dataRoute"] = "sanitized-remote"
            value["capabilities"].insert(-1, "remote-model")
            value["capabilities"].sort()
            sanitized = b'{"privateContentAbsent":true,"placeholdersBound":true}\n'
            (parent / "sanitized.json").write_bytes(sanitized)
            value["bindings"]["sanitizationEvidence"] = {
                "relativePath": "sanitized.json", "sha256": self.digest(sanitized),
                "bytes": len(sanitized), "mediaType": "application/json",
            }
            self.write_private(path, value)
            result = tasks.admit_task(ROOT, path)
            self.assertEqual(result["scenario"]["fault"], "malformed-output")
            self.assertIsNotNone(result["bindings"]["sanitizationEvidenceSha256"])
            value["scenario"]["fault"] = "prompt-injection"
            self.write_private(path, value)
            with self.assertRaisesRegex(tasks.evaluation.OutcomeError, "not declared"):
                tasks.admit_task(ROOT, path)
            value["scenario"]["fault"] = "malformed-output"
            value["bindings"]["sanitizationEvidence"] = None
            self.write_private(path, value)
            with self.assertRaises(tasks.evaluation.OutcomeError):
                tasks.admit_task(ROOT, path)


if __name__ == "__main__":
    unittest.main()
