from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_promotion_readiness", ROOT / "scripts/promotion_readiness.py",
)
readiness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(readiness)


class PromotionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.commit = "a" * 40
        self.tree = "b" * 40
        self.now = datetime(2026, 8, 9, 16, 0, tzinfo=timezone.utc)

    def claims(self, status="pass", passes=2):
        return {
            "schemaVersion": 1,
            "consecutivePasses": passes,
            "gates": {
                gate: {
                    "status": status,
                    "evidenceSha256": "c" * 64 if status == "pass" else None,
                }
                for gate in readiness.EXTERNAL_GATES
            },
        }

    def build(self, claims=None):
        return readiness.build_readiness(
            ROOT, claims,
            now=lambda: self.now,
            source_identity=(self.commit, self.tree),
        )

    def test_matrix_matches_release_doctor_profiles_and_no_provider_automation(self):
        matrix = readiness.validate_matrix(ROOT)
        self.assertEqual(matrix["supportedHosts"], ["Ubuntu 24.04 LTS", "Debian 12"])
        self.assertEqual(
            {(lane["host"], lane["environment"]) for lane in matrix["hostLanes"]},
            {
                ("Ubuntu 24.04 LTS", "github-hosted"),
                ("Debian 12", "manifest-pinned-container"),
                ("Ubuntu 24.04 LTS", "deployment-owned-systemd"),
                ("Debian 12", "deployment-owned-systemd"),
            },
        )
        self.assertTrue(all(lane["requiredForPromotion"] for lane in matrix["hostLanes"]))
        self.assertTrue(all(not lane["providerCallsAllowed"] for lane in matrix["hostLanes"]))
        self.assertTrue(all(not row["fitIsGuaranteed"] for row in matrix["modelCapacity"]))
        self.assertEqual(
            [
                (row["memoryCapacityGiB"], row["localModelClass"], row["contextGuidance"])
                for row in matrix["modelCapacity"]
            ],
            list(readiness.MODEL_ROWS),
        )

    def test_default_status_is_blocked_content_free_and_marks_contract_only(self):
        result = self.build()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["consecutivePasses"], 0)
        states = {gate["id"]: gate for gate in result["gates"]}
        self.assertEqual(states["model-capability-contract"]["status"], "pass")
        self.assertRegex(states["model-capability-contract"]["evidenceSha256"], r"^[a-f0-9]{64}$")
        for gate in readiness.EXTERNAL_GATES:
            self.assertEqual(states[gate], {
                "id": gate, "status": "blocked", "evidenceSha256": None,
            })
        encoded = json.dumps(result)
        for forbidden in (
            str(ROOT), "PRIVATE_HOST", "PRIVATE_MODEL", "PRIVATE_PROVIDER", "PRIVATE_CREDENTIAL",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertFalse(any(result["privacy"].values()))

    def test_all_exact_external_evidence_and_two_passes_produce_pass(self):
        result = self.build(self.claims())
        self.assertEqual(result["status"], "pass")
        self.assertEqual([gate["id"] for gate in result["gates"]], list(readiness.GATES))
        self.assertTrue(all(gate["status"] == "pass" for gate in result["gates"]))
        self.assertTrue(all(gate["evidenceSha256"] is not None for gate in result["gates"]))

    def test_one_blocker_or_one_pass_is_fail_closed(self):
        one_pass = self.build(self.claims(passes=1))
        self.assertEqual(one_pass["status"], "blocked")
        blocked = self.claims()
        blocked["gates"]["frontier-live-qualification"] = {
            "status": "blocked", "evidenceSha256": None,
        }
        self.assertEqual(self.build(blocked)["status"], "blocked")

    def test_unknown_missing_malformed_and_unbound_claims_are_rejected(self):
        cases = []
        unknown = self.claims()
        unknown["unexpected"] = True
        cases.append(unknown)
        missing = self.claims()
        del missing["gates"]["distribution-license"]
        cases.append(missing)
        missing_usability = self.claims()
        del missing_usability["gates"]["owner-usability"]
        cases.append(missing_usability)
        missing_upgrade = self.claims()
        del missing_upgrade["gates"]["signed-release-lifecycle"]
        cases.append(missing_upgrade)
        missing_parity = self.claims()
        del missing_parity["gates"]["outcome-parity"]
        cases.append(missing_parity)
        missing_provider = self.claims()
        del missing_provider["gates"]["multi-provider-local-first"]
        cases.append(missing_provider)
        bool_passes = self.claims()
        bool_passes["consecutivePasses"] = True
        cases.append(bool_passes)
        no_hash = self.claims()
        no_hash["gates"]["frontier-live-qualification"]["evidenceSha256"] = None
        cases.append(no_hash)
        blocked_hash = self.claims()
        blocked_hash["gates"]["frontier-live-qualification"] = {
            "status": "blocked", "evidenceSha256": "d" * 64,
        }
        cases.append(blocked_hash)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(readiness.ReadinessError):
                    self.build(value)

    def test_output_is_new_owner_only_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            output = parent / "promotion-readiness.json"
            payload = json.dumps(self.build(), sort_keys=True).encode("utf-8") + b"\n"
            readiness.write_new_private(output, payload)
            self.assertEqual(output.read_bytes(), payload)
            if readiness.os.name != "nt":
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(readiness.ReadinessError, "already exists"):
                readiness.write_new_private(output, b"replacement")
            self.assertEqual(output.read_bytes(), payload)

    def test_private_claim_reader_rejects_duplicates_links_and_broad_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            claims = parent / "claims.json"
            claims.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
            if readiness.os.name != "nt":
                claims.chmod(0o600)
            with self.assertRaisesRegex(readiness.ReadinessError, "duplicate"):
                readiness.read_json(claims, "private claims", private=True)
            claims.write_text(json.dumps(self.claims()), encoding="utf-8")
            if readiness.os.name != "nt":
                claims.chmod(0o640)
                with self.assertRaisesRegex(readiness.ReadinessError, "0600"):
                    readiness.read_json(claims, "private claims", private=True)
                claims.chmod(0o600)
            linked = parent / "linked.json"
            try:
                linked.symlink_to(claims)
            except OSError:
                return
            with self.assertRaises(readiness.ReadinessError):
                readiness.read_json(linked, "private claims", private=True)

    def test_git_identity_requires_a_clean_exact_commit_and_tree(self):
        result = mock.Mock(returncode=0, stdout="", stderr="")
        commit = mock.Mock(returncode=0, stdout=self.commit + "\n", stderr="")
        tree = mock.Mock(returncode=0, stdout=self.tree + "\n", stderr="")
        with mock.patch.object(readiness.subprocess, "run", side_effect=[result, commit, tree]) as runner:
            self.assertEqual(readiness.git_identity(ROOT), (self.commit, self.tree))
            self.assertEqual(runner.call_count, 3)
        dirty = mock.Mock(returncode=0, stdout="?? private-evidence.json\n", stderr="")
        with mock.patch.object(readiness.subprocess, "run", return_value=dirty):
            with self.assertRaisesRegex(readiness.ReadinessError, "clean source"):
                readiness.git_identity(ROOT)


if __name__ == "__main__":
    unittest.main()
