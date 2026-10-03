import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_trial_coverage as coverage


class PortalOutcomeTrialCoverageTests(unittest.TestCase):
    def inputs(self):
        return {
            "trial_payload": (ROOT / "security-evals/portal-user-trial/trial-journeys-v1.json").read_bytes(),
            "product_corpus_payload": (ROOT / "security-evals/portal-user-journeys/corpus-v1.json").read_bytes(),
            "battery_payload": (ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_bytes(),
            "pixel_profiles": {"assistant", "builder", "controller", "researcher"},
            "codex_profiles": {"assistant", "builder", "controller", "researcher"},
        }

    def test_real_trial_coverage_names_rehearsal_and_adapter_gaps_without_promotional_credit(self):
        result = coverage.build_coverage(**self.inputs())
        self.assertEqual(result["source"]["lineCount"], 535)
        self.assertFalse(result["source"]["verifiedFromOwnerCopy"])
        self.assertEqual(result["summary"], {
            "totalJourneys": 14,
            "formalProductTaskDefined": 14,
            "formalProductProofReady": 0,
            "profileMatchedRehearsalOnly": 5,
            "surrogateProfileRehearsalOnly": 9,
            "uncovered": 0,
            "releaseStatus": "blocked",
        })
        indexed = {item["trialJourneyId"]: item for item in result["journeys"]}
        self.assertEqual(indexed["audit-plan-authoring"]["status"], "profile-matched-rehearsal-only")
        self.assertEqual(indexed["deep-research-live-cited"]["status"], "profile-matched-rehearsal-only")
        self.assertNotIn("surrogate-profile", indexed["deep-research-live-cited"]["blockers"])
        self.assertTrue(indexed["deep-research-live-cited"]["codexComparisonAdapterImplemented"])
        self.assertNotIn("codex-comparison-adapter-missing", indexed["deep-research-live-cited"]["blockers"])
        self.assertTrue(indexed["proactive-monitor-briefing"]["pixelProductAdapterImplemented"])
        self.assertTrue(all(not item["formalProductProofTaskIds"] for item in result["journeys"]))
        for trial_id in ("audit-plan-authoring", "plan-self-critique-harden"):
            self.assertEqual(indexed[trial_id]["formalProductTaskIds"], ["battery-trial-deep-repo-audit"])
            self.assertIn("no-formal-product-proof", indexed[trial_id]["blockers"])
        self.assertEqual(indexed["proactive-monitor-briefing"]["formalProductTaskIds"], ["battery-trial-owner-briefing"])
        self.assertEqual(indexed["factual-followup-qa"]["formalProductTaskIds"], ["battery-trial-conversation-quality"])
        self.assertEqual(indexed["deep-research-live-cited"]["formalProductTaskIds"], ["battery-trial-cited-research"])
        self.assertEqual(indexed["live-agentic-audit-execution"]["formalProductTaskIds"], ["battery-trial-readonly-audit"])
        self.assertEqual(indexed["harness-self-analysis"]["formalProductTaskIds"], ["battery-trial-controller-harness-analysis"])
        self.assertEqual(indexed["self-replica-build-request"]["formalProductTaskIds"], ["battery-trial-controller-bounded-shell"])
        self.assertTrue(indexed["harness-self-analysis"]["pixelProductAdapterImplemented"])
        self.assertTrue(indexed["harness-self-analysis"]["codexComparisonAdapterImplemented"])

    def test_exact_private_source_identity_is_verified_without_emitting_content(self):
        values = self.inputs()
        source = b"private owner trial line one\nprivate owner trial line two\n"
        digest = hashlib.sha256(source).hexdigest()
        trial = json.loads(values["trial_payload"])
        product = json.loads(values["product_corpus_payload"])
        trial["sourceTranscriptSha256"] = digest
        trial["sourceTranscriptBytes"] = len(source)
        product["source"]["sha256"] = digest
        product["source"]["lineCount"] = 2
        values["trial_payload"] = json.dumps(trial).encode("utf-8")
        values["product_corpus_payload"] = json.dumps(product).encode("utf-8")
        values["source_payload"] = source
        result = coverage.build_coverage(**values)
        self.assertTrue(result["source"]["verifiedFromOwnerCopy"])
        self.assertNotIn("private owner trial", json.dumps(result))
        self.assertEqual(result["summary"]["releaseStatus"], "blocked")

    def test_source_substitution_and_profile_registry_widening_fail_closed(self):
        values = self.inputs()
        values["source_payload"] = b"substituted\n"
        with self.assertRaisesRegex(coverage.evaluation.OutcomeError, "differs from its exact corpus identity"):
            coverage.build_coverage(**values)
        values = self.inputs()
        values["codex_profiles"] = {"builder", "admin"}
        with self.assertRaisesRegex(coverage.evaluation.OutcomeError, "profile registry"):
            coverage.build_coverage(**values)

    def test_researcher_rehearsal_becomes_surrogate_if_execution_profile_is_downgraded(self):
        values = self.inputs()
        battery = json.loads(values["battery_payload"])
        task = next(item for item in battery["tasks"] if item["taskId"] == "trial-cited-research-rehearsal")
        task["profile"] = "builder"
        task["workspace"]["verify.py"] = "print('ok')\n"
        task["verify"] = {"command": ["python3", "verify.py"], "expectStdout": "ok\n"}
        values["battery_payload"] = json.dumps(battery).encode("utf-8")
        result = coverage.build_coverage(**values)
        indexed = {item["trialJourneyId"]: item for item in result["journeys"]}
        for trial_id in ("deep-research-live-cited", "local-knowledge-research", "historical-deep-dive"):
            self.assertEqual(indexed[trial_id]["status"], "surrogate-profile-rehearsal-only")
            self.assertEqual(indexed[trial_id]["profileMatchedRehearsalTaskIds"], [])
        self.assertEqual(result["summary"]["formalProductProofReady"], 0)


if __name__ == "__main__":
    unittest.main()
