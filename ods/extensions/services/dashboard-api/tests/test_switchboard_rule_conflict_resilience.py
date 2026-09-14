"""Unit test suite for model switchboard rule reconciliation conflict resilience."""

import unittest


def reconcile_switchboard_rules(rules: list[dict]) -> list[dict]:
    if not isinstance(rules, list):
        return []
    valid_rules = []
    seen_ids = set()
    for rule in rules:
        if isinstance(rule, dict) and "id" in rule:
            rid = rule["id"]
            if rid not in seen_ids:
                seen_ids.add(rid)
                valid_rules.append(rule)
    return valid_rules


class TestSwitchboardRuleConflictResilience(unittest.TestCase):
    def test_reconcile_rules_deduplication(self):
        rules = [{"id": "r1", "model": "m1"}, {"id": "r1", "model": "m2"}, {"id": "r2", "model": "m3"}]
        res = reconcile_switchboard_rules(rules)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["id"], "r1")

    def test_reconcile_rules_invalid(self):
        self.assertEqual(reconcile_switchboard_rules(None), [])


if __name__ == "__main__":
    unittest.main()
