import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_product_path as product_path


class PortalOutcomeProductPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = json.loads((ROOT / "security-evals" / "portal-user-journeys" / "corpus-v1.json").read_text(encoding="utf-8"))
        cls.journeys = value["journeys"]

    @staticmethod
    def admission(journey, profile=None):
        return {"journeyId": journey["id"], "profile": profile or journey["profile"]}

    def test_every_product_journey_has_one_exact_non_surrogate_route(self):
        expected = {
            "assistant": "portal-assistant", "builder": "work-builder",
            "researcher": "work-researcher", "controller": "deep-work-controller",
        }
        observed = set()
        for journey in self.journeys:
            route = product_path.resolve_product_path(self.admission(journey), journey)
            self.assertEqual(route["engine"], expected[journey["profile"]])
            self.assertFalse(route["surrogateProfileAllowed"])
            self.assertEqual(route["requiredEvidence"], sorted(journey["requiredEvidence"]))
            self.assertTrue(route["components"])
            observed.add(route["engine"])
        self.assertEqual(observed, set(expected.values()))

    def test_profile_substitution_and_unknown_evidence_fail_closed(self):
        journey = next(item for item in self.journeys if item["profile"] == "researcher")
        with self.assertRaisesRegex(product_path.evaluation.OutcomeError, "differs from the admitted journey"):
            product_path.resolve_product_path(self.admission(journey, "builder"), journey)
        widened = dict(journey)
        widened["requiredEvidence"] = [*journey["requiredEvidence"], "narrative-self-grade"]
        with self.assertRaisesRegex(product_path.evaluation.OutcomeError, "known evidence"):
            product_path.resolve_product_path(self.admission(journey), widened)

    def test_engine_and_evidence_availability_are_separate_fail_closed_gates(self):
        journey = next(item for item in self.journeys if item["profile"] == "controller")
        route = product_path.resolve_product_path(self.admission(journey), journey)
        with self.assertRaisesRegex(product_path.evaluation.OutcomeError, "not implemented"):
            product_path.require_engine(route, ["work-builder"])
        with self.assertRaisesRegex(product_path.evaluation.OutcomeError, "incomplete"):
            product_path.require_evidence_emitter(route, ["exact-source", "independent-verifier"])

    def test_final_evidence_must_equal_the_journey_contract(self):
        journey = next(item for item in self.journeys if item["profile"] == "builder")
        route = product_path.resolve_product_path(self.admission(journey), journey)
        exact = [{"type": item} for item in route["requiredEvidence"]]
        product_path.require_exact_evidence(route, exact)
        with self.assertRaisesRegex(product_path.evaluation.OutcomeError, "differs from the exact"):
            product_path.require_exact_evidence(route, exact[:-1])
        with self.assertRaisesRegex(product_path.evaluation.OutcomeError, "differs from the exact"):
            product_path.require_exact_evidence(route, [*exact, {"type": exact[0]["type"]}])


if __name__ == "__main__":
    unittest.main()
