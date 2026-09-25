import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_evaluation as evaluation
import portal_outcome_pair as pair
import portal_outcome_pair_system as pair_system


def private_json(path: Path, value: dict) -> bytes:
    payload = json.dumps(value, indent=2).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    if os.name != "nt":
        path.chmod(0o600)
    return payload


class PairSystemBindingTests(unittest.TestCase):
    def fixture(self, parent: Path) -> dict:
        if os.name != "nt":
            parent.chmod(0o700)
        model = parent / "model"
        model.mkdir()
        current_pixel = parent / "pixel-current.json"
        new_pixel = parent / "pixel-enabled.json"
        private_json(current_pixel, {"version": "disabled"})
        new_pixel_value = {"version": "enabled", "nested": {"policy": "qualified"}}
        private_json(new_pixel, new_pixel_value)
        manifest = parent / "manifest.json"
        launch = parent / "launch.json"
        capability = parent / "capability.json"
        surface = parent / "surface.json"
        for path in (manifest, launch, capability, surface):
            private_json(path, {"fixture": path.stem})
        current_preflight = parent / "preflight-disabled.json"
        current = parent / "pair-current.json"
        private_json(current, {
            "$schema": pair.CONFIG_SCHEMA,
            "schemaVersion": 1,
            "candidateSourceArchiveSha256": "1" * 64,
            "pixelSystemConfigPath": str(current_pixel),
            "codexRunnerImage": "sha256:" + "2" * 64,
            "codexBoundaryImage": "sha256:" + "3" * 64,
            "codexSurfaceQualificationPath": str(surface),
            "modelArtifactPath": str(model),
            "modelArtifactManifestPath": str(manifest),
            "launchArgumentsPath": str(launch),
            "capabilityRetentionEvidencePath": str(capability),
            "preflightPath": str(current_preflight),
            "boundary": pair.CONFIG_BOUNDARY,
        })
        binding_receipt = parent / "pixel-binding-receipt.json"
        private_json(binding_receipt, {
            "schemaVersion": 1,
            "operation": "pixel-portal-outcome-system-policy-binding-apply",
            "status": "policy-bound-system-configuration-written",
            "operationSha256": "4" * 64,
            "proposedConfigurationSha256": evaluation.sha256(evaluation.canonical(new_pixel_value)),
            "enabledPolicySha256": "5" * 64,
            "qualificationReceiptSha256": "6" * 64,
            "enabledProfiles": ["assistant", "scout", "builder", "researcher"],
            "comparisonProfiles": ["assistant", "builder", "controller", "researcher"],
            "destinationName": new_pixel.name,
            "changes": dict(pair_system.SYSTEM_RECEIPT_CHANGES),
            "authority": dict(pair_system.SYSTEM_RECEIPT_AUTHORITY),
            "boundary": pair_system.SYSTEM_POLICY_BINDING_BOUNDARY,
        })
        return {
            "configuration_path": current,
            "pixel_system_path": new_pixel,
            "pixel_binding_receipt_path": binding_receipt,
            "candidate_path": parent / "pair-enabled.json",
            "preflight_path": parent / "preflight-enabled.json",
        }

    def test_review_and_apply_write_only_a_new_exact_pair_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            options = self.fixture(Path(temporary).resolve())
            review = pair_system.review_pair_system_binding(**options)
            self.assertEqual(review["status"], "confirmation-required")
            self.assertEqual(review["confirmation"]["sha256"], review["operationSha256"])
            self.assertEqual(review["changes"], pair_system.CHANGES)
            self.assertFalse(any(value for value in review["authority"].values()))
            encoded = json.dumps(review)
            for path in options.values():
                self.assertNotIn(str(path), encoded)
            with self.assertRaisesRegex(evaluation.OutcomeError, "confirmation differs"):
                pair_system.apply_pair_system_binding(confirmation="9" * 64, **options)
            self.assertFalse(options["candidate_path"].exists())
            result = pair_system.apply_pair_system_binding(
                confirmation=review["operationSha256"], **options,
            )
            self.assertEqual(result["status"], "pair-system-configuration-written")
            proposed, raw = pair.load_configuration_record(options["candidate_path"])
            current = pair.load_configuration(options["configuration_path"])
            self.assertEqual(proposed["pixelSystemConfigPath"], str(options["pixel_system_path"]))
            self.assertEqual(proposed["preflightPath"], str(options["preflight_path"]))
            for field in current:
                if field not in {"pixelSystemConfigPath", "preflightPath"}:
                    self.assertEqual(proposed[field], current[field])
            self.assertEqual(evaluation.sha256(raw), review["proposedConfigurationSha256"])
            self.assertFalse(options["preflight_path"].exists())
            with self.assertRaisesRegex(evaluation.OutcomeError, "must be a new private file"):
                pair_system.apply_pair_system_binding(confirmation=review["operationSha256"], **options)

    def test_changed_pixel_system_invalidates_reviewed_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            options = self.fixture(Path(temporary).resolve())
            review = pair_system.review_pair_system_binding(**options)
            changed = {"version": "enabled", "nested": {"policy": "different"}}
            private_json(options["pixel_system_path"], changed)
            with self.assertRaisesRegex(evaluation.OutcomeError, "differs from its successful"):
                pair_system.apply_pair_system_binding(
                    confirmation=review["operationSha256"], **options,
                )
            self.assertFalse(options["candidate_path"].exists())

    def test_forged_receipt_and_reused_preflight_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            options = self.fixture(Path(temporary).resolve())
            receipt = json.loads(options["pixel_binding_receipt_path"].read_text(encoding="utf-8"))
            receipt["changes"]["startsModel"] = True
            private_json(options["pixel_binding_receipt_path"], receipt)
            with self.assertRaisesRegex(evaluation.OutcomeError, "not the exact successful safe transition"):
                pair_system.review_pair_system_binding(**options)
            self.assertFalse(options["candidate_path"].exists())

        with tempfile.TemporaryDirectory() as temporary:
            options = self.fixture(Path(temporary).resolve())
            private_json(options["preflight_path"], {"stale": True})
            with self.assertRaisesRegex(evaluation.OutcomeError, "must be a new private file"):
                pair_system.review_pair_system_binding(**options)


if __name__ == "__main__":
    unittest.main()
