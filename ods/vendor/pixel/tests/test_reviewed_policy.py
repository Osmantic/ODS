import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICY_SPEC = importlib.util.spec_from_file_location(
    "pixel_reviewed_policy", ROOT / "security-evals/assurance/reviewed_policy.py",
)
REVIEWED = importlib.util.module_from_spec(POLICY_SPEC)
assert POLICY_SPEC.loader is not None
POLICY_SPEC.loader.exec_module(REVIEWED)

AUDIT_SPEC = importlib.util.spec_from_file_location(
    "pixel_audit_source", ROOT / "security-evals/assurance/audit_source.py",
)
AUDIT = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC.loader is not None
AUDIT_SPEC.loader.exec_module(AUDIT)

REVIEWED_BLOBS = ROOT / "security-evals/historical-secret-closure/reviewed-blobs.json"
REMOTE_REF_POLICY = ROOT / "security-evals/assurance/remote-ref-policy.json"


class ReviewedPolicyTests(unittest.TestCase):
    def test_committed_policy_is_strict_v2_and_bound_into_remote_ref_policy(self):
        policy = REVIEWED.load(REVIEWED_BLOBS)
        self.assertEqual(policy["schemaVersion"], 2)
        self.assertEqual(policy["repository"], "Osmantic/Pixel")
        self.assertEqual(len(policy["reviewedBlobs"]), 5)
        remote = json.loads(REMOTE_REF_POLICY.read_text(encoding="utf-8"))
        self.assertEqual(remote["repository"], "Osmantic/Pixel")
        self.assertEqual(remote["reviewedBlobsSha256"], REVIEWED.policy_sha256(REVIEWED_BLOBS))

    def test_all_five_entries_are_exact_and_acknowledged_digests_match_audit_source(self):
        policy = REVIEWED.load(REVIEWED_BLOBS)
        acknowledged = {entry["blobSha1"] for entry in REVIEWED.acknowledged_entries(policy)}
        self.assertEqual(len(acknowledged), 3)
        for entry in policy["reviewedBlobs"]:
            payload = subprocess.run(
                ["git", "cat-file", "blob", entry["blobSha1"]], cwd=ROOT,
                capture_output=True, check=True,
            ).stdout
            self.assertEqual(hashlib_sha256(payload), entry["payloadSha256"])
            if entry["blobSha1"] in acknowledged:
                self.assertEqual(
                    sorted(AUDIT.labels_for(payload)), entry["auditSourceLabels"],
                )
            else:
                self.assertEqual(entry["auditSourceLabels"], [])

    def test_policy_digest_drifts_when_data_changes(self):
        original = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        forged = json.loads(json.dumps(original))
        forged["reviewedBlobs"][0]["payloadSha256"] = "f" * 64
        self.assertNotEqual(REVIEWED.policy_sha256(REVIEWED_BLOBS), REVIEWED.canonical_sha256(forged))

    def test_loader_rejects_duplicate_blob_sha1(self):
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["reviewedBlobs"].append(dict(value["reviewedBlobs"][0]))
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)

    def test_loader_rejects_repository_identity_drift(self):
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["repository"] = "substitute/repository"
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)

    def test_loader_rejects_unsorted_audit_source_labels_and_bad_digest(self):
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["reviewedBlobs"][1]["auditSourceLabels"] = ["github-token", "aws-access-key"]
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["reviewedBlobs"][0]["payloadSha256"] = "nope"
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)





    def test_strict_json_loads_rejects_duplicate_keys_at_every_depth(self):
        with self.assertRaises(REVIEWED.ReviewedPolicyError):
            REVIEWED.strict_json_loads('{"a": 1, "a": 2}')
        with self.assertRaises(REVIEWED.ReviewedPolicyError):
            REVIEWED.strict_json_loads('{"outer": {"a": 1, "a": 2}}')
        with self.assertRaises(REVIEWED.ReviewedPolicyError):
            REVIEWED.strict_json_loads('[{"a": 1, "a": 2}]')

    def test_loader_requires_exact_v2_top_level_shape_and_constants(self):
        def check(mutator):
            value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
            mutator(value)
            with tempfile.TemporaryDirectory() as t:
                path = Path(t) / "p.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(REVIEWED.ReviewedPolicyError):
                    REVIEWED.load(path)
        check(lambda v: v.__setitem__("extraTopLevel", 1))
        check(lambda v: v.pop("note"))
        check(lambda v: v.__setitem__("$schema", "https://example.com/not-the-v2.schema.json"))
        check(lambda v: v.__setitem__("operation", "wrong-operation"))
        check(lambda v: v.__setitem__("repository", "substitute/repository"))

    def test_loader_rejects_v1_policy(self):
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["schemaVersion"] = 1
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)

    def test_loader_rejects_extra_entry_field(self):
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["reviewedBlobs"][0]["extraField"] = "x"
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)

    def test_loader_rejects_duplicate_human_labels(self):
        value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
        value["reviewedBlobs"][0]["labels"] = ["duplicate", "duplicate"]
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(path)

    def test_policy_sha256_strict_parses_and_validates_before_hashing(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "p.json"
            path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.policy_sha256(path)
            path.write_text(REVIEWED_BLOBS.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(REVIEWED.policy_sha256(path), REVIEWED.policy_sha256(REVIEWED_BLOBS))

    def test_load_carries_the_documents_own_canonical_sha256(self):
        policy = REVIEWED.load(REVIEWED_BLOBS)
        self.assertEqual(policy["policySha256"], REVIEWED.policy_sha256(REVIEWED_BLOBS))
        self.assertEqual(policy["policySha256"], REVIEWED.canonical_sha256(json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))))

    def test_loader_enforces_v2_schema_bound_maxima(self):
        def check(mutator):
            value = json.loads(REVIEWED_BLOBS.read_text(encoding="utf-8"))
            mutator(value)
            with tempfile.TemporaryDirectory() as t:
                path = Path(t) / "p.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(REVIEWED.ReviewedPolicyError):
                    REVIEWED.load(path)
        check(lambda v: v.__setitem__("note", "x" * 2001))
        check(lambda v: v.__setitem__("reviewedBlobs", v["reviewedBlobs"] * 60))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("path", "p" * 401))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("labels", ["ab"]))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("labels", ["l" * 65]))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("labels", [f"lab{i:02d}" for i in range(17)]))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("auditSourceLabels", ["ab"]))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("auditSourceLabels", ["l" * 65]))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("auditSourceLabels", [f"aud{i:02d}" for i in range(17)]))
        check(lambda v: v["reviewedBlobs"][0].__setitem__("reason", "r" * 601))

    def test_loader_fails_closed_on_missing_and_invalid_utf8_files(self):
        with tempfile.TemporaryDirectory() as t:
            missing = Path(t) / "missing.json"
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(missing)
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.policy_sha256(missing)
            bad = Path(t) / "bad.json"
            bad.write_bytes(b"\xff\xfe not utf-8")
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.load(bad)
            with self.assertRaises(REVIEWED.ReviewedPolicyError):
                REVIEWED.policy_sha256(bad)

def hashlib_sha256(payload):
    import hashlib
    return hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    unittest.main()
