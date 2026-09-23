"""Offline, stdlib regressions for stale inputs and provenance overclaims."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("extension_provenance", PROJECT / "scripts/audit-extension-provenance.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.recipe = Path(temporary.name) / "aider"
        shutil.copytree(PROJECT / "extensions/library/services/aider", self.recipe)

    def update(self, mutator):
        path = self.recipe / "upstream.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        mutator(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_all_34_records_have_reviewable_evidence(self):
        self.assertEqual(len(AUDIT.LEGACY_RECIPES), 34)
        for name in AUDIT.LEGACY_RECIPES:
            with self.subTest(recipe=name):
                self.assertEqual(AUDIT.validate_record(PROJECT / "extensions/library/services" / name), [])

    def test_missing_record_is_not_implicitly_accepted(self):
        (self.recipe / "upstream.json").unlink()
        self.assertTrue(AUDIT.validate_record(self.recipe))

    def test_runtime_input_drift_requires_review(self):
        path = self.recipe / "compose.yaml"
        original = path.read_bytes()
        path.write_text(path.read_text().replace("v0.86.2", "latest"))
        self.assertTrue(any("inputs changed" in error for error in AUDIT.validate_record(self.recipe)))
        path.write_bytes(original)
        overlay = self.recipe / "compose.amd.yaml"
        overlay.write_text("services: {}\n")
        self.assertTrue(any("inputs changed" in error for error in AUDIT.validate_record(self.recipe)))
        overlay.unlink()
        path.unlink()
        self.assertTrue(any("inputs changed" in error for error in AUDIT.validate_record(self.recipe)))

    def test_windows_checkout_newlines_preserve_evidence(self):
        path = self.recipe / "compose.yaml"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.assertEqual(AUDIT.validate_record(self.recipe), [])

    def test_incomplete_or_overstated_evidence_fails(self):
        path = self.recipe / "upstream.json"
        original = path.read_bytes()
        for field, value in [
            ("source_revision", "main"), ("source_revision_scope", "verified_image_source"),
            ("source_url", "https://github.com/unrelated/project/tree/" + "a" * 40),
            ("license_documents", []), ("license_scope", ""), ("notice_urls", ["file:///private"]),
            ("gaps", []), ("review_status", "legally_cleared"), ("model_terms", {}),
        ]:
            with self.subTest(field=field):
                path.write_bytes(original)
                self.update(lambda data: data.update({field: value}))
                self.assertTrue(AUDIT.validate_record(self.recipe))

    def test_image_revision_claim_needs_matching_label(self):
        self.update(lambda data: data.update(source_revision_scope="image_declared_revision"))
        self.assertTrue(any("every observed platform" in error for error in AUDIT.validate_record(self.recipe)))

    def test_image_evidence_cannot_name_another_artifact(self):
        self.update(lambda data: data["deployment"]["images"][0].update(image="unrelated:latest"))
        self.assertTrue(any("literal compose/build" in error for error in AUDIT.validate_record(self.recipe)))


if __name__ == "__main__":
    unittest.main()
