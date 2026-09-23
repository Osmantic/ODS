import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_campaign", ROOT / "scripts/portal_outcome_campaign.py",
)
campaign = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(campaign)


class PortalOutcomeCampaignTests(unittest.TestCase):
    def setUp(self):
        corpus_path = ROOT / "security-evals" / "portal-user-journeys" / "corpus-v1.json"
        self.corpus_sha = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        self.expected, observed_sha = campaign.corpus_scenarios(ROOT)
        self.assertEqual(observed_sha, self.corpus_sha)

    @staticmethod
    def write_private(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")
        if campaign.os.name != "nt":
            path.chmod(0o600)

    def plan(self, parent: Path, keys=None):
        keys = sorted(keys or self.expected, key=lambda item: (item[0], item[1], item[2], item[3] or ""))
        pairs = []
        mapping = {}
        for index, key in enumerate(keys):
            journey, lane, kind, fault = key
            task_name = f"task-{index:03d}.json"
            pixel_name = f"run-{index:03d}-pixel.json"
            codex_name = f"run-{index:03d}-codex.json"
            (parent / task_name).write_text("task", encoding="utf-8")
            (parent / pixel_name).write_text("pixel", encoding="utf-8")
            (parent / codex_name).write_text("codex", encoding="utf-8")
            pairs.append({
                "journeyId": journey, "comparisonLane": lane, "scenarioKind": kind, "scenarioFault": fault,
                "taskPath": task_name,
                "pixelRunPath": pixel_name, "codexRunPath": codex_name,
            })
            mapping[parent / pixel_name] = key
            mapping[parent / task_name] = key
        value = {
            "$schema": campaign.PLAN_SCHEMA,
            "schemaVersion": 1,
            "operation": "pixel-portal-outcome-campaign-plan",
            "planId": "outcomeplan-1786550400001-aaaaaaaaaaaa",
            "corpusSha256": self.corpus_sha,
            "runtimeCondition": "cold-first-request",
            "pairs": pairs,
            "boundary": campaign.PLAN_BOUNDARY,
        }
        path = parent / "plan.json"
        self.write_private(path, value)
        return path, value, mapping

    @staticmethod
    def admission(key):
        journey, lane, kind, fault = key
        scenario = {
            "kind": kind, "fault": fault,
            "seedSha256": hashlib.sha256("|".join((journey, lane, kind, fault or "none")).encode()).hexdigest(),
        }
        return {
            "corpusSha256": "1" * 64, "journeySha256": hashlib.sha256(journey.encode()).hexdigest(),
            "taskSpecificationSha256": hashlib.sha256(("task|" + "|".join((journey, lane, kind, fault or "none"))).encode()).hexdigest(),
            "comparisonLane": lane,
            "bindings": {
                "userRequestSha256": "2" * 64, "sourceSnapshotSha256": "3" * 64,
                "environmentSha256": "4" * 64, "toolPolicySha256": "5" * 64,
                "verifierSha256": "6" * 64,
                "sharedModelContractSha256": "7" * 64 if lane == "same-model-harness" else None,
                "sharedInferenceContractSha256": "8" * 64 if lane == "same-model-harness" else None,
                "sanitizationEvidenceSha256": None,
                "researchFixtureSha256": None,
            },
            "capabilities": ["reasoning"], "budgets": {
                "wallTimeSeconds": 60, "operatorInterventions": 0, "modelRequests": 1,
                "inputTokens": 100, "outputTokens": 100, "artifactBytes": 100, "externalWrites": 0,
            },
            "dataRoute": "local-only", "effectBoundary": "none", "scenario": scenario,
        }

    @classmethod
    def comparison(cls, key, *, status="pass", classification="parity"):
        journey, lane, kind, fault = key
        admission = cls.admission(key)
        admission_sha = campaign.evaluation.sha256(campaign.evaluation.canonical(admission))
        task_binding = {
            "comparisonLane": admission["comparisonLane"],
            "corpusSha256": admission["corpusSha256"], "journeySha256": admission["journeySha256"],
            "taskSpecificationSha256": admission["taskSpecificationSha256"], "taskAdmissionSha256": admission_sha,
            "userRequestSha256": admission["bindings"]["userRequestSha256"],
            "sourceSnapshotSha256": admission["bindings"]["sourceSnapshotSha256"],
            "environmentSha256": admission["bindings"]["environmentSha256"],
            "toolPolicySha256": admission["bindings"]["toolPolicySha256"],
            "verifierSha256": admission["bindings"]["verifierSha256"],
            "sharedModelContractSha256": admission["bindings"]["sharedModelContractSha256"],
            "sharedInferenceContractSha256": admission["bindings"]["sharedInferenceContractSha256"],
            "researchFixtureSha256": admission["bindings"]["researchFixtureSha256"],
            "capabilitiesSha256": campaign.evaluation.sha256(campaign.evaluation.canonical(admission["capabilities"])),
            "budgetsSha256": campaign.evaluation.sha256(campaign.evaluation.canonical(admission["budgets"])),
            "dataRoute": admission["dataRoute"], "effectBoundary": admission["effectBoundary"],
            "scenario": admission["scenario"],
        }
        return {
            "journeyId": journey, "comparisonLane": lane, "scenarioKind": kind, "scenarioFault": fault,
            "runtimeCondition": "cold-first-request",
            "scenarioSeedSha256": admission["scenario"]["seedSha256"],
            "taskBindingSha256": campaign.evaluation.sha256(campaign.evaluation.canonical(task_binding)),
            "pixelHarnessContractSha256": hashlib.sha256(f"harness|pixel|{lane}".encode()).hexdigest(),
            "codexHarnessContractSha256": hashlib.sha256(f"harness|codex|{lane}".encode()).hexdigest(),
            "evidenceCutoffAt": "2026-08-12T12:05:00Z",
            "status": status, "classification": classification,
            "metrics": {
                "pixelToolCalls": 5, "codexToolCalls": 4,
                "pixelOperatorInterventions": 0, "codexOperatorInterventions": 0,
                "pixelExcessOperatorInterventions": 0, "codexExcessOperatorInterventions": 0,
                "pixelOperatorAttentionRequests": 0, "codexOperatorAttentionRequests": 0,
                "pixelExcessOperatorAttentionRequests": 0, "codexExcessOperatorAttentionRequests": 0,
                "pixelApprovalRequests": 0, "codexApprovalRequests": 0,
                "pixelScopeExpansionRequests": 0, "codexScopeExpansionRequests": 0,
                "pixelUnnecessarySafetyBlocks": 0, "codexUnnecessarySafetyBlocks": 0,
            },
        }

    def test_complete_baselines_and_every_declared_fault_pass_content_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan_path, _value, mapping = self.plan(parent)

            def compare(_root, pixel_path, _codex_path):
                return self.comparison(mapping[pixel_path])

            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(campaign.evaluation, "compare_runs", side_effect=compare):
                result = campaign.build_campaign(ROOT, plan_path)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["requiredJourneys"], len({key[0] for key in self.expected}))
            self.assertEqual(result["coveredJourneys"], result["requiredJourneys"])
            self.assertEqual(result["requiredScenarios"], len(self.expected))
            self.assertEqual(result["coveredScenarios"], result["requiredScenarios"])
            self.assertEqual(result["runtimeCondition"], "cold-first-request")
            self.assertEqual(result["summary"]["pass"], len(self.expected))
            self.assertEqual(result["summary"]["blocked"], 0)
            self.assertEqual(result["summary"]["capabilityBlocking"], 0)
            self.assertEqual(result["autonomy"]["pixelToolCalls"], 5 * len(self.expected))
            self.assertEqual(result["autonomy"]["codexToolCalls"], 4 * len(self.expected))
            self.assertTrue(result["autonomy"]["singleAdmissionNoninteractive"])
            self.assertEqual(result["autonomy"]["pixelExcessOperatorInterventions"], 0)
            self.assertEqual([item["comparisonLane"] for item in result["laneHarnessContracts"]], ["product-default", "same-model-harness"])
            for item in result["laneHarnessContracts"]:
                self.assertEqual(item["pixelHarnessContractSha256"], hashlib.sha256(f"harness|pixel|{item['comparisonLane']}".encode()).hexdigest())
                self.assertNotEqual(item["pixelHarnessContractSha256"], item["codexHarnessContractSha256"])
            encoded = json.dumps(result)
            self.assertNotIn(str(parent), encoded)
            self.assertNotIn("run-000", encoded)
            self.assertFalse(any(result["privacy"].values()))

    def test_incomplete_or_nonpassing_campaign_stays_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            one_key = next(iter(self.expected))
            plan_path, _value, mapping = self.plan(parent, [one_key])
            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(
                campaign.evaluation, "compare_runs",
                side_effect=lambda _root, pixel, _codex: self.comparison(mapping[pixel]),
            ):
                incomplete = campaign.build_campaign(ROOT, plan_path)
            self.assertEqual(incomplete["status"], "blocked")
            self.assertEqual(incomplete["coveredScenarios"], 1)

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan_path, _value, mapping = self.plan(parent)
            blocked_key = next(iter(self.expected))

            def compare(_root, pixel_path, _codex_path):
                key = mapping[pixel_path]
                return self.comparison(
                    key, status="blocked", classification="pixel-regression",
                ) if key == blocked_key else self.comparison(key)

            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(campaign.evaluation, "compare_runs", side_effect=compare):
                result = campaign.build_campaign(ROOT, plan_path)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["summary"]["pixelRegression"], 1)
            self.assertEqual(result["summary"]["capabilityBlocking"], 0)

    def test_campaign_counts_unnecessary_pixel_blocking_separately_from_safety_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            one_key = next(iter(self.expected))
            plan_path, _value, mapping = self.plan(parent, [one_key])
            comparison = self.comparison(one_key, status="blocked", classification="capability-blocking")
            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(
                campaign.evaluation, "compare_runs", return_value=comparison,
            ):
                result = campaign.build_campaign(ROOT, plan_path)
            self.assertEqual(result["summary"]["capabilityBlocking"], 1)
            self.assertEqual(result["summary"]["safetyFailure"], 0)

    def test_campaign_rejects_mixed_harness_contracts_within_a_lane(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            lane_keys = sorted(
                (key for key in self.expected if key[1] == "product-default"),
                key=lambda item: (item[0], item[1], item[2], item[3] or ""),
            )[:2]
            plan_path, _value, mapping = self.plan(parent, lane_keys)

            def compare(_root, pixel_path, _codex_path):
                result = self.comparison(mapping[pixel_path])
                if mapping[pixel_path] == lane_keys[1]:
                    result["pixelHarnessContractSha256"] = "f" * 64
                return result

            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(campaign.evaluation, "compare_runs", side_effect=compare):
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "mixes harness contracts"):
                    campaign.build_campaign(ROOT, plan_path)

    def test_unknown_duplicate_reused_or_mismatched_scenarios_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan_path, value, mapping = self.plan(parent)
            value["pairs"][0]["scenarioFault"] = "not-declared"
            self.write_private(plan_path, value)
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "unknown or duplicated"):
                campaign.validate_plan(ROOT, plan_path)

            plan_path, value, mapping = self.plan(parent)
            value["pairs"][1]["pixelRunPath"] = value["pairs"][0]["pixelRunPath"]
            self.write_private(plan_path, value)
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "reuses a task or run"):
                campaign.validate_plan(ROOT, plan_path)

            plan_path, _value, mapping = self.plan(parent)
            first = next(iter(mapping.values()))

            def mismatch(_root, pixel_path, _codex_path):
                result = self.comparison(mapping[pixel_path])
                if mapping[pixel_path] == first:
                    result["journeyId"] = "wrong-journey"
                return result

            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(campaign.evaluation, "compare_runs", side_effect=mismatch):
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "declared campaign scenario"):
                    campaign.build_campaign(ROOT, plan_path)

    def test_comparison_must_bind_exact_admitted_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            key = next(iter(self.expected))
            plan_path, _value, mapping = self.plan(parent, [key])
            bad = self.comparison(key)
            bad["taskBindingSha256"] = "f" * 64
            with mock.patch.object(campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task])), mock.patch.object(campaign.evaluation, "compare_runs", return_value=bad):
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "exact admitted task"):
                    campaign.build_campaign(ROOT, plan_path)

    def test_comparison_must_match_immutable_campaign_runtime_condition(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            key = next(iter(self.expected))
            plan_path, _value, mapping = self.plan(parent, [key])
            wrong = self.comparison(key)
            wrong["runtimeCondition"] = "warm-neutral-probe"
            with mock.patch.object(
                campaign.outcome_task, "admit_task", side_effect=lambda _root, task: self.admission(mapping[task]),
            ), mock.patch.object(campaign.evaluation, "compare_runs", return_value=wrong):
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "campaign runtime condition"):
                    campaign.build_campaign(ROOT, plan_path)

    def test_plan_requires_exact_corpus_private_shape_and_no_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan_path, value, _mapping = self.plan(parent, [next(iter(self.expected))])
            value["corpusSha256"] = "f" * 64
            self.write_private(plan_path, value)
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "exact public corpus"):
                campaign.validate_plan(ROOT, plan_path)
            value["corpusSha256"] = self.corpus_sha
            value["privateNote"] = "must not be accepted"
            self.write_private(plan_path, value)
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "missing or unknown"):
                campaign.validate_plan(ROOT, plan_path)


if __name__ == "__main__":
    unittest.main()
