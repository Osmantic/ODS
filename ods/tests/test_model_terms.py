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
from model_terms import download_review_error, hub_source_observation, project_terms, validate_terms  # noqa: E402

spec = importlib.util.spec_from_file_location("backfill_model_terms", ROOT / "scripts/backfill-model-terms.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
gate_spec = importlib.util.spec_from_file_location("check_model_terms", ROOT / "scripts/check-model-terms.py")
gate = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(gate)


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
    def test_download_requires_explicit_review_of_the_current_artifact_and_terms(self):
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        digest = project_terms(model)["termsDigest"]
        for acknowledgement in (None, {}, {"acknowledged": "true", "termsDigest": digest},
                                {"acknowledged": False, "termsDigest": digest}):
            self.assertEqual(download_review_error(model, acknowledgement)["status"], 428)
        acknowledgement = {"acknowledged": True, "termsDigest": digest}
        self.assertIsNone(download_review_error(model, acknowledgement))
        self.assertFalse(project_terms(model)["releaseReady"])
        self.assertEqual(model["terms"]["review_status"], "not_assessed")
        model["gguf_sha256"] = "e" * 64
        self.assertEqual(download_review_error(model, acknowledgement)["code"], "model_terms_changed")
        model["terms"]["note"] = "Changed conditions"
        self.assertEqual(download_review_error(model, acknowledgement)["status"], 409)

    def test_publisher_acceptance_is_separate_and_cannot_be_implicitly_claimed(self):
        catalog, evidence = fixture()
        model = migration.migrate(catalog, json.dumps(evidence).encode())["models"][0]
        model["terms"]["upstream_acceptance"] = "required_by_observed_gating"
        acknowledgement = {"acknowledged": True, "termsDigest": project_terms(model)["termsDigest"]}
        self.assertEqual(download_review_error(model, acknowledgement)["code"], "model_upstream_acceptance_required")
        acknowledgement["upstreamAccepted"] = "true"
        self.assertEqual(download_review_error(model, acknowledgement)["status"], 428)
        acknowledgement["upstreamAccepted"] = True
        self.assertIsNone(download_review_error(model, acknowledgement))
        self.assertEqual(download_review_error({"id": "missing"}, acknowledgement)["status"], 412)

    def test_hub_declarations_are_bound_but_never_automatically_reviewed(self):
        model = {"id": "hub", "source_repo": "Publisher/Model", "source_revision": "a" * 40,
                 "gguf_file": "local.gguf", "gguf_sha256": "b" * 64,
                 "gguf_url": f"https://huggingface.co/Publisher/Model/resolve/{'a' * 40}/quant/model%20file.gguf"}
        model["terms"] = hub_source_observation(model, license_id="mit", gated=True,
                                               license_url="javascript:alert(1)", declared_bases=["Author/Base"])
        self.assertEqual(validate_terms(model), [])
        self.assertEqual(model["terms"]["artifacts"][0]["path"], "quant/model file.gguf")
        self.assertNotIn("license_url", model["terms"]["sources"][0])
        self.assertEqual(model["terms"]["declared_base_repositories"], ["Author/Base"])
        self.assertFalse(project_terms(model)["releaseReady"])
        self.assertEqual(model["terms"]["upstream_acceptance"], "required_by_observed_gating")

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
        fingerprint, entries = gate.load_license_review(ROOT / "docs/MODEL_LICENSE_REVIEWS.json", ROOT.parent)
        self.assertEqual(len(entries), 30)
        for model in catalog["models"]:
            with self.subTest(model=model["id"]):
                self.assertEqual(validate_terms(model), [])
                reviewed = model["id"] in entries
                self.assertEqual(project_terms(model)["releaseReady"], reviewed)
                self.assertEqual(model["terms"]["review_status"], "reviewed" if reviewed else "not_assessed")
                self.assertEqual(gate.license_review_errors(model, {fingerprint: entries}), [])
        self.assertEqual(sum(m["terms"]["commercial_use"] == "restricted" for m in catalog["models"]), 1)

    def test_review_cannot_be_reused_after_model_source_condition_or_artifact_changes(self):
        catalog = json.loads((ROOT / "config/model-library.json").read_text(encoding="utf-8"))
        fingerprint, entries = gate.load_license_review(ROOT / "docs/MODEL_LICENSE_REVIEWS.json", ROOT.parent)
        model = next(m for m in catalog["models"] if m["id"] in entries)
        snapshots = {fingerprint: entries}
        self.assertEqual(gate.license_review_errors(model, snapshots), [])
        for key, value in [("commercial_use", "restricted"), ("review_status", "not_assessed"),
                           ("upstream_acceptance", "required"), ("issues", ["CONFLICT"]),
                           ("note", "Modified"), ("conditions", []), ("license_documents", []),
                           ("notice_documents", []), ("local_notices", []), ("sources", []),
                           ("evidence_sha256", "c" * 64), ("license_review_evidence_sha256", "d" * 64)]:
            with self.subTest(key=key):
                changed = copy.deepcopy(model)
                changed["terms"][key] = value
                self.assertTrue(gate.license_review_errors(changed, snapshots))
        changed = copy.deepcopy(model)
        del changed["terms"]["license_review_evidence_sha256"]
        self.assertTrue(gate.license_review_errors(changed, snapshots))
        for key in ("gguf_file", "gguf_url", "gguf_sha256", "source_repo", "source_revision", "id"):
            with self.subTest(key=key):
                changed = copy.deepcopy(model)
                changed[key] = "changed"
                self.assertTrue(gate.license_review_errors(changed, snapshots))

    def test_retained_notices_bind_real_bytes_source_and_unambiguous_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            notices = repository / "ods/config/model-notices"
            notices.mkdir(parents=True)
            original = b"<html><pre>License &amp; conditions\n</pre></html>"
            extracted = b"License & conditions\n"
            raw_hash, text_hash = (hashlib.sha256(data).hexdigest() for data in (original, extracted))
            (notices / "source.html").write_bytes(original)
            (notices / "notice.txt").write_bytes(extracted)
            base = {"path": "ods/config/model-notices/source.html", "sha256": raw_hash,
                    "source_url": "https://publisher.example/license", "source_sha256": raw_hash,
                    "extraction": "identity"}
            text = {**base, "path": "ods/config/model-notices/notice.txt", "sha256": text_hash,
                    "extraction": "html_pre_text"}
            review = {"retrieved_terms_documents": [{"url": base["source_url"], "sha256": raw_hash}],
                      "notice_documents": [], "local_notices": [base, text]}
            self.assertEqual(gate.retained_notice_errors(review, repository), [])
            for changes in [{"sha256": "a" * 64}, {"source_url": "https://unrelated.example/terms"},
                            {"source_sha256": "b" * 64}, {"path": "ods/config/model-notices/../escape.txt"},
                            {"path": str(notices / "source.html")}, {"path": "ods/config/model-notices/missing"},
                            {"extraction": "invented"}, {"source_sha256": []}]:
                with self.subTest(changes=changes):
                    changed = copy.deepcopy(review)
                    changed["local_notices"][0].update(changes)
                    self.assertTrue(gate.retained_notice_errors(changed, repository))
            changed = copy.deepcopy(review)
            changed["local_notices"] = [text]
            self.assertTrue(gate.retained_notice_errors(changed, repository))
            changed = copy.deepcopy(review)
            changed["retrieved_terms_documents"].append({"url": "https://example.com/ancestor-license",
                                                       "sha256": "f" * 64})
            self.assertTrue(gate.retained_notice_errors(changed, repository))
            changed = copy.deepcopy(review)
            changed["notice_documents"].append({"url": "https://example.com/NOTICE", "sha256": "f" * 64})
            self.assertTrue(gate.retained_notice_errors(changed, repository))
            fragment = b"<pre>License &amp; conditions\n</pre>"
            fragment_hash = hashlib.sha256(fragment).hexdigest()
            (notices / "source.html").write_bytes(fragment)
            snippet = copy.deepcopy(review)
            snippet["local_notices"][0].update(sha256=fragment_hash, extraction="html_pre_fragment", source_block_count=1)
            snippet["local_notices"][1]["source_fragment_sha256"] = fragment_hash
            self.assertEqual(gate.retained_notice_errors(snippet, repository), [])
            for count in (0, 2, True):
                changed = copy.deepcopy(snippet)
                changed["local_notices"][0]["source_block_count"] = count
                self.assertTrue(gate.retained_notice_errors(changed, repository))
            changed = copy.deepcopy(snippet)
            changed["local_notices"][1]["source_fragment_sha256"] = "e" * 64
            self.assertTrue(gate.retained_notice_errors(changed, repository))
            (notices / "notice.txt").write_bytes(b"Changed notice")
            self.assertTrue(gate.retained_notice_errors(snippet, repository))

    def test_review_inputs_and_local_license_coverage_cannot_be_omitted(self):
        baseline = json.loads((ROOT / "docs/MODEL_LICENSE_REVIEWS.json").read_bytes())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "review.json"
            variants = []
            changed = copy.deepcopy(baseline)
            changed["input_evidence"] = changed["input_evidence"][1:]
            variants.append(changed)
            changed = copy.deepcopy(baseline)
            changed["entries"][0]["review"]["local_notices"].pop()
            variants.append(changed)
            changed = copy.deepcopy(baseline)
            changed["input_evidence"][0]["sha256"] = "c" * 64
            variants.append(changed)
            changed = copy.deepcopy(baseline)
            changed["input_evidence"][0]["path"] = "../private.txt"
            variants.append(changed)
            changed = copy.deepcopy(baseline)
            changed["entries"].append(changed["entries"][0])
            variants.append(changed)
            for conditions in (None, "conditions", [], [{"trigger": "redistribution", "requirement": None}]):
                changed = copy.deepcopy(baseline)
                changed["entries"][0]["review"]["conditions"] = conditions
                variants.append(changed)
            for index, changed in enumerate(variants):
                with self.subTest(index=index):
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        gate.load_license_review(path, ROOT.parent)

    def test_malformed_completed_review_is_not_presented_or_downloadable(self):
        catalog = json.loads((ROOT / "config/model-library.json").read_bytes())
        original = next(m for m in catalog["models"] if m["terms"]["review_status"] == "reviewed")
        for conditions in (None, "conditions", [], [{"trigger": "redistribution", "requirement": None}]):
            with self.subTest(conditions=conditions):
                model = copy.deepcopy(original)
                model["terms"]["conditions"] = conditions
                self.assertFalse(project_terms(model)["recordValid"])
                self.assertFalse(project_terms(model)["releaseReady"])
                self.assertEqual(download_review_error(model, {})["status"], 412)

    def test_full_catalog_review_gate_reports_pending_models_and_rejects_release(self):
        for flags, code in [([], 0), (["--release-ready"], 1)]:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/check-model-terms.py"), *flags],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            body = json.loads(result.stdout)
            self.assertEqual(body["models"], 57)
            self.assertEqual(body["invalidRecords"], [])
            self.assertEqual(body["evidenceErrors"], [])
            self.assertEqual(len(body["pendingReview"]), 27)

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

    def test_supplemental_artifact_evidence_is_required_and_binds_the_replacement(self):
        catalog, evidence = fixture()
        evidence_bytes = json.dumps(evidence).encode()
        original = migration.migrate(catalog, evidence_bytes)["models"][0]
        replacement = copy.deepcopy(original)
        replacement.update(id="replacement", size_bytes=1024, quantization="Q4_K_M")
        supplement = {"schema_version": 1, "entries": [{
            "id": "replacement", "review": {"retrieved_terms_documents": []},
            "verified_download": {key: replacement[key] for key in (
                "source_repo", "source_revision", "gguf_file", "gguf_url", "gguf_sha256",
                "size_bytes", "quantization")},
        }]}
        supplement_bytes = json.dumps(supplement).encode()
        replacement["terms"]["evidence_sha256"] = hashlib.sha256(supplement_bytes).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "catalog.json"
            original_path = directory / "original.json"
            supplement_path = directory / "supplement.json"
            ambiguous_path = directory / "ambiguous.json"
            original_path.write_bytes(evidence_bytes)
            supplement_path.write_bytes(supplement_bytes)
            ambiguous_path.write_bytes(supplement_bytes + b"\n")
            for snapshots, changes, expected_error in [
                ([original_path, supplement_path], {}, None),
                ([original_path], {}, "evidenceErrors"),
                ([original_path, supplement_path, supplement_path], {}, "evidenceError"),
                ([original_path, supplement_path, ambiguous_path], {}, "evidenceError"),
                ([original_path, supplement_path], {"gguf_sha256": "e" * 64}, "evidenceErrors"),
                ([original_path, supplement_path], {"quantization": "Q8_0"}, "evidenceErrors"),
                ([original_path, supplement_path], {"size_bytes": 2048}, "evidenceErrors"),
            ]:
                with self.subTest(snapshots=snapshots, changes=changes):
                    candidate = copy.deepcopy(replacement)
                    candidate.update(changes)
                    path.write_text(json.dumps({"models": [original, candidate]}), encoding="utf-8")
                    args = [sys.executable, str(ROOT / "scripts/check-model-terms.py"), "--catalog", str(path)]
                    for snapshot in snapshots:
                        args.extend(["--evidence", str(snapshot)])
                    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, int(expected_error is not None), result.stdout + result.stderr)
                    body = json.loads(result.stdout)
                    if expected_error:
                        self.assertTrue(body[expected_error])
                    else:
                        self.assertEqual(body["pendingReview"], ["model", "replacement"])

    def test_incomplete_verified_artifact_record_cannot_authorize_a_replacement(self):
        catalog, evidence = fixture()
        valid = migration.migrate(catalog, json.dumps(evidence).encode())
        model = valid["models"][0]
        identity = {key: model[key] for key in (
            "source_repo", "source_revision", "gguf_file", "gguf_url", "gguf_sha256")}
        identity.update(size_bytes=1024, quantization="Q4_K_M")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            evidence_path = Path(temporary) / "evidence.json"
            path.write_text(json.dumps(valid), encoding="utf-8")
            variants = [{key: value for key, value in identity.items() if key != missing} for missing in identity]
            variants.extend([{**identity, "size_bytes": True}, {**identity, "size_bytes": 0},
                             {**identity, "source_revision": "main"}, {**identity, "gguf_sha256": ""}])
            for incomplete in variants:
                with self.subTest(identity=incomplete):
                    candidate = copy.deepcopy(evidence)
                    candidate["entries"][0]["verified_download"] = incomplete
                    evidence_path.write_text(json.dumps(candidate), encoding="utf-8")
                    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-model-terms.py"),
                                             "--catalog", str(path), "--evidence", str(evidence_path)],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("complete immutable download identity", json.loads(result.stdout)["evidenceError"])

    def test_repaired_models_cannot_revert_to_the_obsolete_artifact_observation(self):
        catalog = json.loads((ROOT / "config/model-library.json").read_text(encoding="utf-8"))
        historical_hash = hashlib.sha256((ROOT / "docs/MODEL_TERMS_AUDIT.json").read_bytes()).hexdigest()
        repaired = {"gemma4-26b-a4b-q4", "gemma4-31b-q4"}
        for model in catalog["models"]:
            if model["id"] in repaired:
                # Both IDs exist in the historical snapshot with the same license
                # documents. Matching those fields cannot approve a replacement.
                model["terms"]["evidence_sha256"] = historical_hash
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/check-model-terms.py"),
                                     "--catalog", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            body = json.loads(result.stdout)
            self.assertEqual(body["invalidRecords"], [])
            self.assertEqual({record["modelId"] for record in body["evidenceErrors"]}, repaired)
            for record in body["evidenceErrors"]:
                self.assertIn("supplemental artifact replacement evidence", record["errors"][0])


if __name__ == "__main__":
    unittest.main()
