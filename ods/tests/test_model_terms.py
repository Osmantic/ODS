"""Source binding and unresolved-license regressions; no network or weights."""
import hashlib
import importlib.util
import json
import copy
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions/services/dashboard-api"))
from model_terms import project_terms, validate_terms  # noqa: E402

spec = importlib.util.spec_from_file_location("backfill_model_terms", ROOT / "scripts/backfill-model-terms.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def fixture():
    repo, revision = "Publisher/Model", "a" * 40
    url = f"https://huggingface.co/{repo}/resolve/main/model.gguf"
    model = {"id": "model", "name": "Example", "gguf_file": "model.gguf",
             "gguf_url": url, "gguf_sha256": "b" * 64, "context_length": 4096}
    catalog = json.dumps({"models": [model]}).encode()
    evidence = {"schema_version": 1, "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
                "retrieved_at": "2026-09-23T00:00:00Z",
                "repositories": [{"repo": repo, "revision": revision, "private": False,
                                  "gated": False, "api_response": {"status": 200},
                                  "card_metadata": {"license": "apache-2.0"}}],
                "entries": [{"id": "model", "artifacts": [{"repo": repo, "revision": "main",
                             "path": "model.gguf", "path_present_at_reviewed_revision": True}],
                             "review": {"artifact_repository": repo,
                                        "artifact_reviewed_revision": revision,
                                        "artifact_license_declaration": {"license": "apache-2.0"},
                                        "declared_base_repositories": [], "reviewed_ancestry": [],
                                        "retrieved_terms_documents": [],
                                        "gap_codes": ["CATALOG_FIELDS_ABSENT", "MUTABLE_ARTIFACT_REF"]}}]}
    return catalog, evidence


class ModelTermsTests(unittest.TestCase):
    def test_migration_pins_observed_source_and_preserves_download_integrity(self):
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        self.assertIn("/resolve/" + "a" * 40 + "/", model["gguf_url"])
        self.assertEqual(model["gguf_sha256"], "b" * 64)
        self.assertEqual(model["context_length"], 4096)
        self.assertEqual(validate_terms(model), [])
        self.assertFalse(project_terms(model)["releaseReady"])
        self.assertEqual(model["terms"]["commercial_use"], "not_assessed")

    def test_changed_catalog_or_incomplete_inventory_cannot_be_backfilled(self):
        catalog, evidence = fixture()
        with self.assertRaisesRegex(ValueError, "Catalog changed"):
            migration.migrate(catalog + b" ", json.dumps(evidence).encode())
        evidence["entries"] = []
        with self.assertRaisesRegex(ValueError, "coverage"):
            migration.migrate(catalog, json.dumps(evidence).encode())

    def test_another_artifact_or_private_source_is_rejected(self):
        catalog, evidence = fixture()
        evidence["entries"][0]["artifacts"][0]["path"] = "different.gguf"
        with self.assertRaisesRegex(ValueError, "does not match"):
            migration.migrate(catalog, json.dumps(evidence).encode())
        catalog, evidence = fixture()
        evidence["repositories"][0]["private"] = True
        with self.assertRaisesRegex(ValueError, "publicly verified"):
            migration.migrate(catalog, json.dumps(evidence).encode())

    def test_missing_artifact_is_not_silently_pinned_or_cleared(self):
        catalog, evidence = fixture()
        evidence["entries"][0]["artifacts"][0]["path_present_at_reviewed_revision"] = False
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        self.assertIn("/main/", model["gguf_url"])
        self.assertIn("ARTIFACT_IDENTITY_REQUIRES_REPAIR", model["terms"]["issues"])
        self.assertFalse(project_terms(model)["releaseReady"])

    def test_download_change_invalidates_terms_binding_and_digest(self):
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        before = project_terms(model)["termsDigest"]
        model["gguf_sha256"] = "c" * 64
        self.assertNotEqual(before, project_terms(model)["termsDigest"])
        model["gguf_url"] = model["gguf_url"].replace("/model.gguf", "/other.gguf")
        self.assertFalse(project_terms(model)["recordValid"])

    def test_unsafe_links_missing_records_and_claimed_approval_are_not_clean(self):
        self.assertFalse(project_terms({"id": "missing"})["recordValid"])
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        model["terms"]["sources"][0]["declaration_url"] = "javascript:alert(1)"
        self.assertFalse(project_terms(model)["recordValid"])
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        model["terms"].update(review_status="reviewed", commercial_use="permitted_with_conditions", upstream_acceptance="not_required")
        self.assertFalse(project_terms(model)["releaseReady"])

    def test_real_catalog_has_complete_observations_without_implicit_approval(self):
        catalog = json.loads((ROOT / "config/model-library.json").read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["models"]), 57)
        for model in catalog["models"]:
            with self.subTest(model=model["id"]):
                self.assertEqual(validate_terms(model), [])
                self.assertFalse(project_terms(model)["releaseReady"])
                self.assertEqual(model["terms"]["review_status"], "not_assessed")

    def test_malformed_observations_return_errors_instead_of_crashing(self):
        catalog, evidence = fixture()
        for artifacts, issues in [([None], []), ([None, {}], None), ([], "wrong")]:
            with self.subTest(artifacts=artifacts, issues=issues):
                model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
                model["terms"].update(artifacts=artifacts, issues=issues)
                self.assertFalse(project_terms(model)["recordValid"])

    def test_publisher_and_artifact_urls_must_match_the_observed_identity(self):
        catalog, evidence = fixture()
        for field in ("url", "declaration_url"):
            model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
            model["terms"]["sources"][0][field] = "https://example.com/unrelated"
            self.assertFalse(project_terms(model)["recordValid"])
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        model["gguf_url"] = model["gguf_url"].replace("huggingface.co", "example.com")
        model["terms"]["artifacts"][0]["url"] = model["gguf_url"]
        self.assertFalse(project_terms(model)["recordValid"])

    def test_observed_artifact_is_bound_to_the_decoded_file_path(self):
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        model["gguf_url"] = model["gguf_url"].replace("/model.gguf", "/other.gguf")
        model["terms"]["artifacts"][0]["url"] = model["gguf_url"]
        self.assertFalse(project_terms(model)["recordValid"])
        model["gguf_url"] = model["gguf_url"].replace("/other.gguf", "/model%20file.gguf")
        model["terms"]["artifacts"][0].update(url=model["gguf_url"], path="model file.gguf")
        self.assertEqual(validate_terms(model), [])
        model["gguf_url"] = model["gguf_url"].replace("/model%20file.gguf", "/%2e%2e/other.gguf")
        model["terms"]["artifacts"][0].update(url=model["gguf_url"], path="../other.gguf")
        self.assertFalse(project_terms(model)["recordValid"])

    def test_notices_are_bound_to_a_recorded_source_revision_and_path(self):
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        notice = {"repository": "Publisher/Model", "path": "NOTICE file",
                  "url": f"https://huggingface.co/Publisher/Model/blob/{'a' * 40}/NOTICE%20file",
                  "sha256": "c" * 64}
        model["terms"]["notice_documents"] = [notice]
        self.assertEqual(validate_terms(model), [])
        substitutions = [
            {"repository": "Other/Model"}, {"path": "different.txt"}, {"path": None},
            {"url": "https://unrelated.example/NOTICE"},
            {"url": notice["url"].replace("a" * 40, "b" * 40)},
            {"url": notice["url"].replace("NOTICE%20file", "%2e%2e/NOTICE"), "path": "../NOTICE"},
        ]
        for changes in substitutions:
            with self.subTest(changes=changes):
                model["terms"]["notice_documents"] = [{**notice, **changes}]
                self.assertFalse(project_terms(model)["recordValid"])

    def test_catalog_gate_rejects_empty_duplicate_and_unreviewed_release_inputs(self):
        catalog, evidence = fixture()
        valid = migration.migrate(catalog, json.dumps(evidence).encode())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            evidence_path = Path(temporary) / "evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            for payload, flags, exit_code in [
                ({"models": []}, [], 1),
                ({"models": [valid["models"][0]] * 2}, [], 1),
                (valid, [], 0),
                (valid, ["--release-ready"], 1),
            ]:
                with self.subTest(payload=payload, flags=flags):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-model-terms.py"),
                                             "--catalog", str(path), "--evidence", str(evidence_path), *flags],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)

    def test_catalog_gate_binds_external_license_urls_and_hashes_to_evidence(self):
        catalog, evidence = fixture()
        evidence["entries"][0]["review"]["retrieved_terms_documents"] = [{
            "repo": "Publisher/Model", "kind": "declared_license_link",
            "url": "https://publisher.example/model-terms", "sha256": "d" * 64,
        }]
        evidence_bytes = json.dumps(evidence).encode()
        valid = migration.migrate(catalog, evidence_bytes)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            evidence_path = Path(temporary) / "evidence.json"
            evidence_path.write_bytes(evidence_bytes)
            for changes in [None, {"url": "https://unrelated.example/terms"},
                            {"sha256": "e" * 64}, {"repo": "Other/Model"}]:
                with self.subTest(changes=changes):
                    candidate = copy.deepcopy(valid)
                    if changes:
                        candidate["models"][0]["terms"]["license_documents"][0].update(changes)
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-model-terms.py"),
                                             "--catalog", str(path), "--evidence", str(evidence_path)],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, int(changes is not None), result.stdout + result.stderr)
                    body = json.loads(result.stdout)
                    self.assertEqual(body["pendingReview"], ["model"])
                    if changes:
                        self.assertIn("License documents differ", body["evidenceErrors"][0]["errors"][0])

    def test_catalog_gate_rejects_changed_missing_or_ambiguous_evidence(self):
        catalog, evidence = fixture()
        evidence_bytes = json.dumps(evidence).encode()
        valid = migration.migrate(catalog, evidence_bytes)
        other_entry = copy.deepcopy(evidence)
        other_entry["entries"][0]["id"] = "other-model"
        duplicate = copy.deepcopy(evidence)
        duplicate["entries"] *= 2
        missing_documents = copy.deepcopy(evidence)
        del missing_documents["entries"][0]["review"]["retrieved_terms_documents"]
        cases = [
            (None, False, "evidenceError"),
            (b"{", False, "evidenceError"),
            (evidence_bytes + b"\n", False, "evidenceErrors"),
            (json.dumps(other_entry).encode(), True, "evidenceErrors"),
            (json.dumps(duplicate).encode(), True, "evidenceError"),
            (json.dumps(missing_documents).encode(), True, "evidenceError"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            for index, (payload, update_fingerprint, error_key) in enumerate(cases):
                with self.subTest(case=index):
                    evidence_path = Path(temporary) / f"evidence-{index}.json"
                    if payload is not None:
                        evidence_path.write_bytes(payload)
                    candidate = copy.deepcopy(valid)
                    if update_fingerprint:
                        candidate["models"][0]["terms"]["evidence_sha256"] = hashlib.sha256(payload).hexdigest()
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-model-terms.py"),
                                             "--catalog", str(path), "--evidence", str(evidence_path)],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertTrue(json.loads(result.stdout)[error_key])


if __name__ == "__main__":
    unittest.main()
