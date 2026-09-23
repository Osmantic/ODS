from datetime import datetime, timezone
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_evaluation", ROOT / "scripts/portal_outcome_evaluation.py",
)
outcomes = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(outcomes)


class PortalOutcomeEvaluationTests(unittest.TestCase):
    def setUp(self):
        corpus_path = ROOT / "security-evals" / "portal-user-journeys" / "corpus-v1.json"
        self.corpus_raw = corpus_path.read_bytes()
        corpus = json.loads(self.corpus_raw)
        self.journey = next(item for item in corpus["journeys"] if item["id"] == "bounded-security-scanner")

    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def runtime_control(run_id: str, condition: str = "cold-first-request") -> dict:
        warm = condition == "warm-neutral-probe"
        return {
            "schemaVersion": 1,
            "operation": "pixel-portal-outcome-runtime-control",
            "runId": run_id,
            "condition": condition,
            "status": "ready",
            "warmupRequestSha256": "c" * 64 if warm else None,
            "warmupResponseSha256": "d" * 64 if warm else None,
            "requestsBefore": 0,
            "requestsAfter": 1 if warm else 0,
            "warmupModelRequests": 1 if warm else 0,
            "warmupInputTokens": 9 if warm else 0,
            "warmupOutputTokens": 1 if warm else 0,
            "measuredUsageIncludesWarmup": False,
            "promptCachePolicy": "empty-at-run-start",
            "crossRunStateObserved": False,
            "authority": {
                "grantsTaskExecution": False,
                "grantsToolUse": False,
                "grantsProviderCall": False,
                "grantsCredentialUse": False,
                "grantsExternalEffect": False,
                "grantsCompletion": False,
            },
            "boundary": outcomes.RUNTIME_CONTROL_BOUNDARY,
        }

    def make_run(self, backend: str, evidence: bytes, artifact: bytes) -> dict:
        evidence_sha = self.digest(evidence)
        artifact_sha = self.digest(artifact)
        marker = "1" if backend == "pixel" else "2"
        return {
            "$schema": outcomes.RUN_SCHEMA,
            "schemaVersion": 1,
            "operation": "pixel-portal-outcome-run",
            "runId": f"outcomerun-178655040000{marker}-{'a' if backend == 'pixel' else 'b'}" + ("a" if backend == "pixel" else "b") * 11,
            "journeyId": self.journey["id"],
            "backend": backend,
            "synthetic": False,
            "selfGraded": False,
            "startedAt": "2026-08-12T12:00:00Z",
            "finishedAt": "2026-08-12T12:05:00Z",
            "task": {
                "comparisonLane": "product-default",
                "corpusSha256": self.digest(self.corpus_raw),
                "journeySha256": self.digest(outcomes.canonical(self.journey)),
                "taskSpecificationSha256": "1" * 64,
                "taskAdmissionSha256": "2" * 64,
                "userRequestSha256": "3" * 64,
                "sourceSnapshotSha256": "2" * 64,
                "environmentSha256": "3" * 64,
                "toolPolicySha256": "4" * 64,
                "verifierSha256": "5" * 64,
                "sharedModelContractSha256": None,
                "sharedInferenceContractSha256": None,
                "researchFixtureSha256": None,
                "capabilitiesSha256": "6" * 64,
                "budgetsSha256": "7" * 64,
                "dataRoute": "local-only",
                "effectBoundary": self.journey["effect"],
                "scenario": {"kind": "baseline", "fault": None, "seedSha256": "5" * 64},
            },
            "executionIdentity": {
                "harnessContractSha256": ("8" if backend == "pixel" else "9") * 64,
                "modelContractSha256": "a" * 64, "inferenceContractSha256": "b" * 64,
                "toolPolicySha256": "4" * 64, "freshRuntimeStartSha256": None,
                "runtimeCondition": "cold-first-request", "runtimeControlSha256": "0" * 64,
                "interactionMode": "single-admission-noninteractive",
                "crossRunStateObserved": False,
            },
            "execution": {
                "status": "completed", "realBackend": True, "realTools": True, "exitCode": 0,
                "latencyMs": 300000, "operatorInterventions": 0,
                "operatorAttentionRequests": 0, "approvalRequests": 0, "scopeExpansionRequests": 0,
                "interruptions": 0,
                "toolCalls": 2, "modelRequests": 3, "inputTokens": 1200, "outputTokens": 800, "externalWrites": 0,
            },
            "interaction": {
                "schemaVersion": 1, "mode": "single-admission-noninteractive",
                "source": "pixel-builder-checkpoint-v1" if backend == "pixel" else "codex-jsonl-closed-stdin-v1",
                "observationSha256": ("c" if backend == "pixel" else "d") * 64,
                "operatorInputsAfterAdmission": 0, "operatorAttentionRequests": 0,
                "approvalRequests": 0, "scopeExpansionRequests": 0, "safetyBlocks": 0,
                "interruptions": 0, "complete": True, "workerSelfReported": False,
                "boundary": outcomes.INTERACTION_BOUNDARY,
            },
            "evidence": [
                {"type": kind, "relativePath": "evidence.txt", "sha256": evidence_sha, "bytes": len(evidence)}
                for kind in self.journey["requiredEvidence"]
            ],
            "assertions": [
                {"id": assertion, "status": "pass", "evidencePath": "evidence.txt", "evidenceSha256": evidence_sha}
                for assertion in self.journey["assertions"]
            ],
            "dimensions": [
                {"id": dimension, "score": 4, "evidencePath": "evidence.txt", "evidenceSha256": evidence_sha}
                for dimension in outcomes.DIMENSIONS
            ],
            "artifacts": [
                {"kind": "finding-report", "relativePath": "artifact.txt", "sha256": artifact_sha, "bytes": len(artifact)},
            ],
            "safetyFindings": [],
            "verifier": {
                "independent": True, "kind": "deterministic-verifier", "backendOutputUsedAsScore": False,
                "evidencePath": "evidence.txt", "evidenceSha256": evidence_sha,
            },
            "authority": {
                "scopeExpansionDetected": False, "privateDataSentRemote": False,
                "unreconciledExternalWrite": False, "safetyBoundaryRelaxed": False,
            },
            "boundary": outcomes.RUN_BOUNDARY,
        }

    def records(self, parent: Path) -> tuple[Path, Path, dict, dict]:
        evidence = b"PRIVATE_CANARY exact independent verifier output\n"
        artifact = b"PRIVATE_CANARY report artifact\n"
        pixel_root = parent / "pixel"
        codex_root = parent / "codex"
        pixel_root.mkdir(mode=0o700)
        codex_root.mkdir(mode=0o700)
        for arm_root in (pixel_root, codex_root):
            (arm_root / "evidence.txt").write_bytes(evidence)
            (arm_root / "artifact.txt").write_bytes(artifact)
        pixel = self.make_run("pixel", evidence, artifact)
        codex = self.make_run("codex", evidence, artifact)
        pixel["runId"] = "outcomerun-1786550400001-aaaaaaaaaaaa"
        codex["runId"] = "outcomerun-1786550400002-bbbbbbbbbbbb"
        pixel_path = pixel_root / "run.json"
        codex_path = codex_root / "run.json"
        self.write_run(pixel_path, pixel)
        self.write_run(codex_path, codex)
        return pixel_path, codex_path, pixel, codex

    @classmethod
    def write_run(cls, path: Path, value: dict) -> None:
        control = cls.runtime_control(value["runId"], value["executionIdentity"]["runtimeCondition"])
        control_payload = outcomes.canonical(control)
        value["executionIdentity"]["runtimeControlSha256"] = cls.digest(control_payload)
        control_path = path.parent / "runtime-control.json"
        control_path.write_bytes(control_payload)
        path.write_text(json.dumps(value), encoding="utf-8")
        if outcomes.os.name != "nt":
            control_path.chmod(0o600)
            path.chmod(0o600)

    def test_exact_real_pair_passes_without_projecting_private_content_or_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, _pixel, _codex = self.records(parent)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["classification"]), ("pass", "parity"))
            self.assertEqual(result["comparisonLane"], "product-default")
            self.assertEqual(result["pixelHarnessContractSha256"], "8" * 64)
            self.assertEqual(result["codexHarnessContractSha256"], "9" * 64)
            self.assertTrue(all(item["relation"] == "parity" for item in result["assertions"]))
            self.assertTrue(all(item["delta"] == 0 for item in result["dimensions"]))
            encoded = json.dumps(result)
            self.assertNotIn("PRIVATE_CANARY", encoded)
            self.assertNotIn(str(parent), encoded)
            self.assertFalse(any(result["privacy"].values()))

    def test_interaction_telemetry_is_mandatory_mechanical_arm_bound_and_consistent(self):
        mutations = (
            ("omitted", lambda run: run.pop("interaction"), "missing or unknown"),
            ("self-reported", lambda run: run["interaction"].update(workerSelfReported=True), "incomplete or has the wrong source"),
            ("cross-arm-source", lambda run: run["interaction"].update(source="codex-jsonl-closed-stdin-v1"), "wrong source"),
            ("attention-mismatch", lambda run: run["execution"].update(operatorAttentionRequests=1), "differs from its mechanically observed"),
            ("safety-mismatch", lambda run: run["interaction"].update(safetyBlocks=1), "differs from its mechanically observed"),
        )
        for label, mutate, error in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary)
                pixel_path, codex_path, pixel, _codex = self.records(parent)
                mutate(pixel)
                self.write_run(pixel_path, pixel)
                with self.assertRaisesRegex(outcomes.OutcomeError, error):
                    outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_pixel_assertion_regression_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["assertions"][0]["status"] = "fail"
            self.write_run(pixel_path, pixel)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["classification"]), ("blocked", "pixel-regression"))

    def test_declared_fault_is_bound_and_undeclared_fault_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, codex = self.records(parent)
            for run in (pixel, codex):
                run["task"]["scenario"] = {
                    "kind": "fault-injection", "fault": "malformed-output", "seedSha256": "6" * 64,
                }
            self.write_run(pixel_path, pixel)
            self.write_run(codex_path, codex)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["scenarioKind"], result["scenarioFault"]), ("fault-injection", "malformed-output"))
            pixel["task"]["scenario"]["fault"] = "prompt-injection"
            self.write_run(pixel_path, pixel)
            with self.assertRaisesRegex(outcomes.OutcomeError, "not declared"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_score_delta_or_incomplete_execution_cannot_be_called_near_parity(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["dimensions"][0]["score"] = 3
            self.write_run(pixel_path, pixel)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual(result["classification"], "unexplained-delta")
            pixel["dimensions"][0]["score"] = 4
            pixel["execution"]["status"] = "failed"
            self.write_run(pixel_path, pixel)
            self.assertEqual(outcomes.compare_runs(ROOT, pixel_path, codex_path)["classification"], "unexplained-delta")

    def test_unnecessary_pixel_safety_block_is_measured_as_capability_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["execution"]["status"] = "safety-blocked"
            pixel["execution"]["exitCode"] = None
            pixel["interaction"]["safetyBlocks"] = 1
            self.write_run(pixel_path, pixel)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["classification"]), ("blocked", "capability-blocking"))
            self.assertEqual(result["metrics"]["pixelSafetyBlocks"], 1)
            self.assertEqual(result["metrics"]["pixelUnnecessarySafetyBlocks"], 1)
            self.assertEqual(result["metrics"]["codexUnnecessarySafetyBlocks"], 0)
            self.assertFalse(any(result["safety"].values()))

    def test_excess_pixel_operator_friction_is_capability_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["execution"]["operatorInterventions"] = 1
            pixel["interaction"]["operatorInputsAfterAdmission"] = 1
            self.write_run(pixel_path, pixel)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["classification"]), ("blocked", "capability-blocking"))
            self.assertEqual(result["metrics"]["pixelExcessOperatorInterventions"], 1)
            self.assertEqual(result["metrics"]["codexExcessOperatorInterventions"], 0)

    def test_real_tool_claim_requires_measured_tool_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["execution"]["toolCalls"] = 0
            self.write_run(pixel_path, pixel)
            with self.assertRaisesRegex(outcomes.OutcomeError, "real tool outcome"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_reference_safety_block_does_not_masquerade_as_pixel_safety_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, _pixel, codex = self.records(parent)
            codex["execution"]["status"] = "safety-blocked"
            codex["execution"]["exitCode"] = None
            codex["interaction"]["safetyBlocks"] = 1
            self.write_run(codex_path, codex)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual(result["classification"], "reference-failure")
            self.assertEqual(result["metrics"]["codexUnnecessarySafetyBlocks"], 1)

    def test_excess_pixel_attention_request_is_capability_blocking_without_claiming_operator_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["execution"]["operatorAttentionRequests"] = 1
            pixel["execution"]["approvalRequests"] = 1
            pixel["interaction"]["operatorAttentionRequests"] = 1
            pixel["interaction"]["approvalRequests"] = 1
            self.write_run(pixel_path, pixel)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["classification"]), ("blocked", "capability-blocking"))
            self.assertEqual(result["metrics"]["pixelExcessOperatorAttentionRequests"], 1)
            self.assertEqual(result["metrics"]["pixelOperatorInterventions"], 0)

    def test_same_model_lane_requires_observed_exact_contracts_fresh_runtime_and_distinct_harnesses(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, codex = self.records(parent)
            for run in (pixel, codex):
                run["task"]["comparisonLane"] = "same-model-harness"
                run["task"]["sharedModelContractSha256"] = "c" * 64
                run["task"]["sharedInferenceContractSha256"] = "d" * 64
                run["executionIdentity"]["modelContractSha256"] = "c" * 64
                run["executionIdentity"]["inferenceContractSha256"] = "d" * 64
                run["executionIdentity"]["freshRuntimeStartSha256"] = "e" * 64
            self.write_run(pixel_path, pixel)
            self.write_run(codex_path, codex)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["comparisonLane"]), ("pass", "same-model-harness"))
            codex["executionIdentity"]["modelContractSha256"] = "f" * 64
            self.write_run(codex_path, codex)
            with self.assertRaisesRegex(outcomes.OutcomeError, "exact shared model"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)
            codex["executionIdentity"]["modelContractSha256"] = "c" * 64
            codex["executionIdentity"]["harnessContractSha256"] = pixel["executionIdentity"]["harnessContractSha256"]
            self.write_run(codex_path, codex)
            with self.assertRaisesRegex(outcomes.OutcomeError, "distinct Pixel and Codex harness"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_interactive_or_reapproval_capable_run_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["executionIdentity"]["interactionMode"] = "interactive"
            self.write_run(pixel_path, pixel)
            with self.assertRaisesRegex(outcomes.OutcomeError, "interaction mode"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_safety_or_authority_failure_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["authority"]["scopeExpansionDetected"] = True
            self.write_run(pixel_path, pixel)
            result = outcomes.compare_runs(ROOT, pixel_path, codex_path)
            self.assertEqual((result["status"], result["classification"]), ("blocked", "safety-failure"))
            self.assertTrue(result["safety"]["authorityViolation"])

    def test_runtime_control_is_hash_bound_and_mixed_conditions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, codex = self.records(parent)
            codex["executionIdentity"]["runtimeCondition"] = "warm-neutral-probe"
            self.write_run(codex_path, codex)
            with self.assertRaisesRegex(outcomes.OutcomeError, "exact cold or warm runtime condition"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

            codex["executionIdentity"]["runtimeCondition"] = "cold-first-request"
            self.write_run(codex_path, codex)
            control_path = pixel_path.parent / "runtime-control.json"
            control = json.loads(control_path.read_text(encoding="utf-8"))
            control["requestsAfter"] = 1
            control_path.write_text(json.dumps(control), encoding="utf-8")
            if outcomes.os.name != "nt":
                control_path.chmod(0o600)
            with self.assertRaisesRegex(outcomes.OutcomeError, "differs from its exact cold or warm condition"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_unbound_task_missing_contract_or_tampered_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, codex = self.records(parent)
            codex["task"]["environmentSha256"] = "9" * 64
            self.write_run(codex_path, codex)
            with self.assertRaisesRegex(outcomes.OutcomeError, "exact task"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)
            codex["task"] = copy.deepcopy(pixel["task"])
            del pixel["assertions"][0]
            self.write_run(pixel_path, pixel)
            self.write_run(codex_path, codex)
            with self.assertRaisesRegex(outcomes.OutcomeError, "assertions do not exactly cover"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)
            pixel = self.make_run("pixel", b"PRIVATE_CANARY exact independent verifier output\n", b"PRIVATE_CANARY report artifact\n")
            pixel["runId"] = "outcomerun-1786550400001-aaaaaaaaaaaa"
            self.write_run(pixel_path, pixel)
            (pixel_path.parent / "evidence.txt").write_bytes(b"tampered\n")
            with self.assertRaisesRegex(outcomes.OutcomeError, "exact size and digest"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_synthetic_self_grade_unknown_evidence_and_duplicate_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            pixel_path, codex_path, pixel, _codex = self.records(parent)
            pixel["synthetic"] = True
            self.write_run(pixel_path, pixel)
            with self.assertRaises(outcomes.OutcomeError):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)
            pixel["synthetic"] = False
            pixel["verifier"]["backendOutputUsedAsScore"] = True
            self.write_run(pixel_path, pixel)
            with self.assertRaisesRegex(outcomes.OutcomeError, "self-graded"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)
            pixel["verifier"]["backendOutputUsedAsScore"] = False
            pixel["evidence"][0]["type"] = "typed-call"
            self.write_run(pixel_path, pixel)
            with self.assertRaisesRegex(outcomes.OutcomeError, "exactly cover"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)
            pixel_path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
            if outcomes.os.name != "nt":
                pixel_path.chmod(0o600)
            with self.assertRaisesRegex(outcomes.OutcomeError, "duplicate"):
                outcomes.compare_runs(ROOT, pixel_path, codex_path)

    def test_output_is_new_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            output = parent / "comparison.json"
            payload = b"{}\n"
            outcomes.write_new_private(output, payload)
            self.assertEqual(output.read_bytes(), payload)
            if outcomes.os.name != "nt":
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(outcomes.OutcomeError, "already exists"):
                outcomes.write_new_private(output, b"replacement")
            self.assertEqual(output.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
