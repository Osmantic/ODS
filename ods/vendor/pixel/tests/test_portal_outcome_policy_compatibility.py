import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_policy_compatibility as compatibility


def private_write(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name != "nt":
        path.chmod(0o600)


class PortalOutcomePolicyCompatibilityTests(unittest.TestCase):
    def test_full_inventory_is_bound_without_execution_or_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialized = parent / "materialized"
            transient = parent / "transient"
            materialized.mkdir()
            transient.mkdir()
            if os.name != "nt":
                materialized.chmod(0o700)
                transient.chmod(0o700)
            task_payloads = [b'{"task":1}\n', b'{"task":2}\n']
            tasks = []
            for index, payload in enumerate(task_payloads):
                relative = f"task-{index}.json"
                private_write(materialized / relative, payload)
                tasks.append({
                    "taskRelativePath": relative, "taskSha256": hashlib.sha256(payload).hexdigest(),
                    "partition": "tuning" if index == 0 else "held-out",
                    "profileFidelity": "exact" if index == 0 else "surrogate-rehearsal",
                    "proofClass": "real-disposable-workspace" if index == 0 else "journey-rehearsal",
                })
            materialization_payload = b'{"fixture":"materialization"}\n'
            private_write(materialized / "materialization.json", materialization_payload)
            config_path = parent / "pair.json"
            config_payload = b'{"fixture":"pair"}\n'
            private_write(config_path, config_payload)
            model_sha, inference_sha = "1" * 64, "2" * 64
            value = {
                "profile": "builder", "evaluationRegime": "matched-budget", "batterySha256": "3" * 64,
                "modelContractSha256": model_sha, "inferenceContractSha256": inference_sha, "tasks": tasks,
            }
            materialization_sha = hashlib.sha256(materialization_payload).hexdigest()
            calls = []

            def reviewer(**options):
                calls.append(options["task_path"])
                return {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-planned-system-review",
                    "runId": "outcomerun-0000000000000-123456abcdef", "profile": "builder",
                    "status": "structurally-compatible", "readiness": "qualification-required",
                    "modelContractSha256": model_sha, "inferenceContractSha256": inference_sha,
                    "taskCompatibilitySha256": hashlib.sha256(options["task_path"].read_bytes()).hexdigest(),
                    "harnessContractSha256": "9" * 64,
                    "workPolicySha256": "4" * 64, "environmentSha256": "5" * 64,
                    "backendConfigSha256": "6" * 64, "runnerImageDigest": "sha256:" + "7" * 64,
                    "backendImageDigest": "sha256:" + "8" * 64,
                    "changes": {
                        "policyMutated": False, "qualificationFabricated": False, "modelStarted": False,
                        "containerCreated": False, "networkCreated": False, "taskExecuted": False,
                        "externalEffects": False,
                    },
                    "authority": dict(compatibility.REPORT_AUTHORITY),
                    "boundary": compatibility.pair_preflight.PIXEL_PLANNED_REVIEW_BOUNDARY,
                }

            output = parent / "compatibility.json"
            report = compatibility.audit_planned_compatibility(
                root=ROOT, materialization_root=materialized, pair_configuration_path=config_path,
                temporary_parent=transient, output_path=output,
                materialization_loader=lambda _path, _root: (value, materialization_sha),
                configuration_loader=lambda _path: ({}, config_payload), reviewer=reviewer,
            )
            self.assertEqual(report["counts"], {
                "tasks": 2, "tuning": 1, "heldOut": 1, "exactProfile": 1, "surrogateRehearsal": 1,
                "qualificationRequired": 2, "policyReady": 0,
            })
            self.assertEqual(len(calls), 2)
            self.assertTrue(report["checks"]["noAuthorityGranted"])
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(report["harnessContractSha256"], "9" * 64)
            self.assertEqual(report["auditorSha256"], hashlib.sha256(
                Path(compatibility.__file__).resolve().read_bytes(),
            ).hexdigest())
            self.assertFalse(any(report["authority"].values()))
            self.assertNotIn(str(parent), json.dumps(report))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "structurally-compatible")
            drift_calls = 0

            def drifting_reviewer(**options):
                nonlocal drift_calls
                drift_calls += 1
                reviewed = reviewer(**options)
                if drift_calls == 2:
                    reviewed["harnessContractSha256"] = "a" * 64
                return reviewed

            with self.assertRaisesRegex(compatibility.evaluation.OutcomeError, "one exact Pixel envelope"):
                compatibility.audit_planned_compatibility(
                    root=ROOT, materialization_root=materialized, pair_configuration_path=config_path,
                    temporary_parent=transient, output_path=parent / "drift.json",
                    materialization_loader=lambda _path, _root: (value, materialization_sha),
                    configuration_loader=lambda _path: ({}, config_payload), reviewer=drifting_reviewer,
                )
            with self.assertRaisesRegex(compatibility.evaluation.OutcomeError, "already exists"):
                compatibility.audit_planned_compatibility(
                    root=ROOT, materialization_root=materialized, pair_configuration_path=config_path,
                    temporary_parent=transient, output_path=output,
                    materialization_loader=lambda _path, _root: (value, materialization_sha),
                    configuration_loader=lambda _path: ({}, config_payload), reviewer=reviewer,
                )

    def test_refuses_any_task_review_that_claims_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialized = parent / "materialized"
            transient = parent / "transient"
            materialized.mkdir()
            transient.mkdir()
            if os.name != "nt":
                materialized.chmod(0o700)
                transient.chmod(0o700)
            payload = b'{}\n'
            private_write(materialized / "task.json", payload)
            materialization_payload = b'{}\n'
            private_write(materialized / "materialization.json", materialization_payload)
            config_path = parent / "pair.json"
            private_write(config_path, payload)
            value = {
                "profile": "builder", "evaluationRegime": "matched-budget", "batterySha256": "3" * 64,
                "modelContractSha256": "1" * 64, "inferenceContractSha256": "2" * 64,
                "tasks": [{
                    "taskRelativePath": "task.json", "taskSha256": hashlib.sha256(payload).hexdigest(),
                    "partition": "tuning", "profileFidelity": "exact", "proofClass": "real-disposable-workspace",
                }],
            }

            def unsafe_reviewer(**_options):
                return {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-planned-system-review",
                    "runId": "outcomerun-0000000000000-123456abcdef", "profile": "builder",
                    "status": "structurally-compatible", "readiness": "qualification-required",
                    "modelContractSha256": "1" * 64, "inferenceContractSha256": "2" * 64,
                    "taskCompatibilitySha256": "3" * 64, "harnessContractSha256": "9" * 64,
                    "workPolicySha256": "4" * 64,
                    "environmentSha256": "5" * 64, "backendConfigSha256": "6" * 64,
                    "runnerImageDigest": "sha256:" + "7" * 64, "backendImageDigest": "sha256:" + "8" * 64,
                    "changes": {
                        "policyMutated": False, "qualificationFabricated": False, "modelStarted": False,
                        "containerCreated": False, "networkCreated": False, "taskExecuted": True,
                        "externalEffects": False,
                    },
                    "authority": dict(compatibility.REPORT_AUTHORITY),
                    "boundary": compatibility.pair_preflight.PIXEL_PLANNED_REVIEW_BOUNDARY,
                }

            with self.assertRaisesRegex(compatibility.evaluation.OutcomeError, "unsafe or mismatched"):
                compatibility.audit_planned_compatibility(
                    root=ROOT, materialization_root=materialized, pair_configuration_path=config_path,
                    temporary_parent=transient, output_path=parent / "unsafe.json",
                    materialization_loader=lambda _path, _root: (
                        value, hashlib.sha256(materialization_payload).hexdigest(),
                    ),
                    configuration_loader=lambda _path: ({}, payload), reviewer=unsafe_reviewer,
                )


if __name__ == "__main__":
    unittest.main()
