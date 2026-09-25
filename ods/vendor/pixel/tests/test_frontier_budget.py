import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pixel_test_frontier_budget", ROOT / "scripts/frontier_budget.py")
budget_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(budget_module)


class FrontierBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.state = self.base / "state"
        self.private = self.base / "private"
        self.state.mkdir(mode=0o700)
        self.private.mkdir(mode=0o700)
        self.policy_path = self.private / "frontier-policy.json"
        self.onboarding_path = self.private / "onboarding.json"
        self.policy = json.loads(
            (ROOT / "deploy/frontier-broker/policy.chatgpt.example.json").read_text(encoding="utf-8")
        )
        self.policy["deployment"] = "budget-test"
        self.onboarding = {
            "frontierLimbEnabled": True,
            "frontierAuthMode": "chatgpt",
            "frontierBudgetProfile": "custom",
            "frontierPolicyFile": str(self.policy_path),
        }
        budget_module.atomic_json(self.policy_path, self.policy, replace=False)
        budget_module.atomic_json(self.onboarding_path, self.onboarding, replace=False)
        self.now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def clock(self):
        return self.now

    def request(self, **changes):
        value = {
            "schemaVersion": 1,
            "windowSeconds": 172800,
            "maxJobs": 10,
            "maxInputTokens": 100000,
            "maxOutputTokens": 20000,
            "maxFailures": 3,
            "maxEstimatedCostMicros": None,
        }
        value.update(changes)
        return value

    def proposal(self, **changes):
        return budget_module.create_proposal(
            ROOT, self.state, self.onboarding_path, self.request(**changes), now=self.clock,
        )

    def test_preview_is_private_bounded_hash_bound_and_has_no_apply_authority(self):
        proposal = self.proposal()
        encoded = json.dumps(proposal)
        self.assertNotIn(str(self.private), encoded)
        self.assertFalse(proposal["browserCanApply"])
        self.assertFalse(proposal["browserCanActivate"])
        self.assertFalse(proposal["increasesPotentialSpend"])
        self.assertEqual(proposal["billingBoundary"], "chatgpt-plan")
        self.assertEqual(proposal["activation"], "configure-plan-apply-required")
        record = self.state / "frontier-budget-proposals" / f"{proposal['proposalId']}.json"
        envelope = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(envelope["policyPath"], str(self.policy_path))
        self.assertEqual(envelope["public"], proposal)
        budget_module.validate_private_proposal(envelope, expected_id=proposal["proposalId"])
        if os.name != "nt":
            self.assertEqual(record.stat().st_mode & 0o777, 0o600)
            self.assertEqual(record.parent.stat().st_mode & 0o777, 0o700)

    def test_exact_apply_changes_only_budgets_and_creates_backup_and_pathless_receipt(self):
        proposal = self.proposal()
        before_bytes = self.policy_path.read_bytes()
        receipt = budget_module.apply_proposal(
            ROOT, self.state, self.onboarding_path, proposal["proposalId"], proposal["proposalHash"], now=self.clock,
        )
        updated = json.loads(self.policy_path.read_text(encoding="utf-8"))
        expected = copy.deepcopy(self.policy)
        expected["budgets"] = proposal["proposedBudgets"]
        self.assertEqual(updated, expected)
        backup = self.private / f"frontier-policy.json.before-{proposal['proposalId']}.json"
        self.assertEqual(backup.read_bytes(), before_bytes)
        self.assertFalse(receipt["activated"])
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["nextActionCode"], "configure-plan-apply")
        self.assertNotIn(str(self.private), json.dumps(receipt))
        stored = json.loads((self.state / "frontier-budget-applications" / f"{proposal['proposalId']}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, receipt)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "already applied"):
            budget_module.apply_proposal(
                ROOT, self.state, self.onboarding_path, proposal["proposalId"], proposal["proposalHash"], now=self.clock,
            )

    def test_wrong_hash_expiry_policy_drift_and_onboarding_drift_fail_closed(self):
        proposal = self.proposal()
        with self.assertRaisesRegex(budget_module.BudgetInputError, "does not match"):
            budget_module.apply_proposal(
                ROOT, self.state, self.onboarding_path, proposal["proposalId"], "0" * 64, now=self.clock,
            )
        with self.assertRaisesRegex(budget_module.BudgetInputError, "expired"):
            budget_module.apply_proposal(
                ROOT, self.state, self.onboarding_path, proposal["proposalId"], proposal["proposalHash"],
                now=lambda: self.now + timedelta(seconds=budget_module.PROPOSAL_TTL_SECONDS),
            )
        changed = copy.deepcopy(self.policy)
        changed["authority"]["defaultLevel"] = "preview"
        budget_module.atomic_json(self.policy_path, changed, replace=True)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "changed after proposal"):
            budget_module.apply_proposal(
                ROOT, self.state, self.onboarding_path, proposal["proposalId"], proposal["proposalHash"], now=self.clock,
            )
        budget_module.atomic_json(self.policy_path, self.policy, replace=True)
        other_policy = self.private / "other-policy.json"
        budget_module.atomic_json(other_policy, self.policy, replace=False)
        moved = dict(self.onboarding)
        moved["frontierPolicyFile"] = str(other_policy)
        budget_module.atomic_json(self.onboarding_path, moved, replace=True)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "path changed"):
            budget_module.apply_proposal(
                ROOT, self.state, self.onboarding_path, proposal["proposalId"], proposal["proposalHash"], now=self.clock,
            )

    def test_hidden_path_is_part_of_exact_proposal_hash(self):
        proposal = self.proposal()
        record = self.state / "frontier-budget-proposals" / f"{proposal['proposalId']}.json"
        envelope = json.loads(record.read_text(encoding="utf-8"))
        envelope["policyPath"] = str(self.private / "other-policy.json")
        with self.assertRaisesRegex(budget_module.BudgetError, "hash"):
            budget_module.validate_private_proposal(envelope)
        unsigned = dict(envelope["public"])
        unsigned.pop("proposalHash")
        envelope["public"]["proposalHash"] = budget_module.digest({
            "policyPath": envelope["policyPath"], "proposal": unsigned,
        })
        budget_module.atomic_json(record, envelope, replace=True)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "does not match"):
            budget_module.apply_proposal(
                ROOT, self.state, self.onboarding_path, proposal["proposalId"], proposal["proposalHash"], now=self.clock,
            )

    def test_durable_claim_recovers_before_policy_edit_even_after_expiry(self):
        proposal = self.proposal()
        original_atomic_bytes = budget_module.atomic_bytes

        def interrupt_policy_write(path, payload, *, replace):
            if path == self.policy_path and replace:
                raise budget_module.BudgetError("simulated interruption")
            return original_atomic_bytes(path, payload, replace=replace)

        with mock.patch.object(budget_module, "atomic_bytes", side_effect=interrupt_policy_write):
            with self.assertRaisesRegex(budget_module.BudgetError, "simulated interruption"):
                budget_module.apply_proposal(
                    ROOT, self.state, self.onboarding_path,
                    proposal["proposalId"], proposal["proposalHash"], now=self.clock,
                )
        claim_path = self.state / "frontier-budget-applications" / f"{proposal['proposalId']}.json"
        self.assertEqual(json.loads(claim_path.read_text(encoding="utf-8"))["status"], "applying")
        self.assertEqual(json.loads(self.policy_path.read_text(encoding="utf-8"))["budgets"], self.policy["budgets"])
        receipt = budget_module.apply_proposal(
            ROOT, self.state, self.onboarding_path,
            proposal["proposalId"], proposal["proposalHash"],
            now=lambda: self.now + timedelta(hours=1),
        )
        self.assertEqual(receipt["status"], "applied")

    def test_durable_claim_recovers_after_policy_edit_before_receipt(self):
        proposal = self.proposal()
        original_atomic_json = budget_module.atomic_json
        application_path = self.state / "frontier-budget-applications" / f"{proposal['proposalId']}.json"

        def interrupt_receipt(path, value, *, replace):
            if path == application_path and replace:
                raise budget_module.BudgetError("simulated receipt interruption")
            return original_atomic_json(path, value, replace=replace)

        with mock.patch.object(budget_module, "atomic_json", side_effect=interrupt_receipt):
            with self.assertRaisesRegex(budget_module.BudgetError, "receipt interruption"):
                budget_module.apply_proposal(
                    ROOT, self.state, self.onboarding_path,
                    proposal["proposalId"], proposal["proposalHash"], now=self.clock,
                )
        self.assertEqual(json.loads(application_path.read_text(encoding="utf-8"))["status"], "applying")
        self.assertEqual(
            json.loads(self.policy_path.read_text(encoding="utf-8"))["budgets"],
            proposal["proposedBudgets"],
        )
        receipt = budget_module.apply_proposal(
            ROOT, self.state, self.onboarding_path,
            proposal["proposalId"], proposal["proposalHash"],
            now=lambda: self.now + timedelta(hours=1),
        )
        self.assertEqual(receipt["status"], "applied")

    def test_input_shape_ranges_relations_noop_and_billing_confusion_are_rejected(self):
        hostile = [
            {**self.request(), "extra": 1},
            {**self.request(), "maxJobs": True},
            {**self.request(), "maxJobs": 0},
            {**self.request(), "maxOutputTokens": 100001},
            {**self.request(), "maxFailures": 11},
            {**self.request(), "maxEstimatedCostMicros": 1},
        ]
        for value in hostile:
            with self.subTest(value=value):
                with self.assertRaises(budget_module.BudgetInputError):
                    budget_module.create_proposal(ROOT, self.state, self.onboarding_path, value, now=self.clock)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "change"):
            budget_module.create_proposal(
                ROOT, self.state, self.onboarding_path,
                {"schemaVersion": 1, **self.policy["budgets"]}, now=self.clock,
            )

    def test_metered_api_requires_and_applies_an_explicit_cost_ceiling(self):
        policy = json.loads((ROOT / "deploy/frontier-broker/policy.example.json").read_text(encoding="utf-8"))
        policy["deployment"] = "metered-budget-test"
        policy["provider"]["cost"] = {
            "mode": "metered", "currency": "USD",
            "inputMicrosPerMillionTokens": 2000000,
            "outputMicrosPerMillionTokens": 8000000,
            "source": "owner-reviewed-price-sheet", "asOf": "2026-01-01",
        }
        policy["budgets"]["maxEstimatedCostMicros"] = 10000000
        onboarding = dict(self.onboarding)
        onboarding["frontierAuthMode"] = "api-key"
        budget_module.atomic_json(self.policy_path, policy, replace=True)
        budget_module.atomic_json(self.onboarding_path, onboarding, replace=True)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "explicit cost ceiling"):
            self.proposal()
        proposal = self.proposal(maxEstimatedCostMicros=5000000)
        self.assertEqual(proposal["billingBoundary"], "platform-api")
        self.assertEqual(proposal["proposedBudgets"]["maxEstimatedCostMicros"], 5000000)
        self.assertFalse(proposal["increasesPotentialSpend"])

    def test_unpriced_api_policy_is_refused_by_safe_editor(self):
        policy = json.loads((ROOT / "deploy/frontier-broker/policy.example.json").read_text(encoding="utf-8"))
        policy["deployment"] = "unpriced-budget-test"
        onboarding = dict(self.onboarding)
        onboarding["frontierAuthMode"] = "api-key"
        budget_module.atomic_json(self.policy_path, policy, replace=True)
        budget_module.atomic_json(self.onboarding_path, onboarding, replace=True)
        with self.assertRaisesRegex(budget_module.BudgetInputError, "metered policy"):
            self.proposal(maxEstimatedCostMicros=5000000)

    def test_cli_apply_requires_confirm_and_uses_the_explicit_onboarding(self):
        proposal = budget_module.create_proposal(
            ROOT, self.state, self.onboarding_path, self.request(), now=budget_module.utcnow,
        )
        command = [
            sys.executable, str(ROOT / "scripts/frontier_budget.py"),
            "--root", str(ROOT), "--state", str(self.state),
            "--onboarding", str(self.onboarding_path), "apply",
            "--proposal-id", proposal["proposalId"],
            "--proposal-hash", proposal["proposalHash"],
        ]
        denied = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(denied.returncode, 2)
        self.assertIn("requires --confirm", denied.stderr)
        self.assertEqual(json.loads(self.policy_path.read_text(encoding="utf-8"))["budgets"], self.policy["budgets"])
        applied = subprocess.run([*command, "--confirm"], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["status"], "applied")

    def test_retention_and_unrecognized_records_fail_closed_without_unbounded_loading(self):
        for _index in range(budget_module.MAX_PROPOSALS):
            self.proposal()
        with self.assertRaisesRegex(budget_module.BudgetError, "retention"):
            self.proposal()
        self.now += timedelta(hours=1)
        replacement = self.proposal()
        self.assertTrue(replacement["proposalId"].startswith("frontier-budget-"))
        proposals = self.state / "frontier-budget-proposals"
        for path in proposals.iterdir():
            path.unlink()
        (proposals / "unexpected").write_text("unsafe", encoding="utf-8")
        with self.assertRaisesRegex(budget_module.BudgetError, "unsafe record"):
            self.proposal()

    def test_duplicate_json_and_unsafe_private_records_are_rejected(self):
        self.onboarding_path.write_text(
            '{"frontierLimbEnabled":true,"frontierLimbEnabled":true,"frontierBudgetProfile":"custom"}',
            encoding="utf-8",
        )
        if os.name != "nt":
            self.onboarding_path.chmod(0o600)
        with self.assertRaisesRegex(budget_module.BudgetError, "duplicate"):
            self.proposal()

    @unittest.skipUnless(os.name == "posix", "POSIX link and mode semantics")
    def test_links_hardlinks_and_nonprivate_modes_are_rejected(self):
        target = self.private / "target.json"
        target.write_bytes(self.policy_path.read_bytes())
        target.chmod(0o600)
        linked = self.private / "linked-policy.json"
        linked.symlink_to(target)
        onboarding = dict(self.onboarding)
        onboarding["frontierPolicyFile"] = str(linked)
        budget_module.atomic_json(self.onboarding_path, onboarding, replace=True)
        with self.assertRaises(budget_module.BudgetError):
            self.proposal()
        linked.unlink()
        os.link(target, linked)
        with self.assertRaisesRegex(budget_module.BudgetError, "single-link"):
            self.proposal()
        onboarding["frontierPolicyFile"] = str(self.policy_path)
        budget_module.atomic_json(self.onboarding_path, onboarding, replace=True)
        self.policy_path.chmod(0o640)
        with self.assertRaisesRegex(budget_module.BudgetError, "0600"):
            self.proposal()


if __name__ == "__main__":
    unittest.main()
