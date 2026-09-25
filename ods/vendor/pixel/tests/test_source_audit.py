import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "security-evals" / "assurance" / "audit_source.py"
SPEC = importlib.util.spec_from_file_location("pixel_audit_source", MODULE_PATH)
auditor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(auditor)

REMOTE_MODULE_PATH = Path(__file__).resolve().parents[1] / "security-evals" / "assurance" / "audit_remote_refs.py"
REMOTE_SPEC = importlib.util.spec_from_file_location("pixel_audit_remote_refs", REMOTE_MODULE_PATH)
remote_auditor = importlib.util.module_from_spec(REMOTE_SPEC)
assert REMOTE_SPEC.loader is not None
REMOTE_SPEC.loader.exec_module(remote_auditor)


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Pixel Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "pixel-test@example.invalid"], cwd=self.root, check=True)
        for directory in ("plugin", "plugin-ops"):
            path = self.root / directory
            path.mkdir()
            (path / "package.json").write_text(json.dumps({"dependencies": {"typebox": "1.3.9"}}), encoding="utf-8")
            (path / "package-lock.json").write_text(json.dumps({"packages": {
                "": {"dependencies": {"typebox": "1.3.9"}},
                "node_modules/typebox": {"version": "1.3.9", "integrity": "sha512-fixture"},
            }}), encoding="utf-8")
        requirements = self.root / "deploy" / "web-courier"
        requirements.mkdir(parents=True)
        self.requirements = requirements / "requirements.lock"
        self.requirements.write_text(
            "example==1.2.3 \\\n"
            "  --hash=sha256:" + ("a" * 64) + "\n", encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def commit(self, message):
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)

    def policy(self, lineage_root, repository, legacy_refs=None):
        path = self.root / "remote-ref-policy.json"
        path.write_text(json.dumps({
            "schemaVersion": 1,
            "repository": str(Path(repository).resolve()),
            "lineageRootCommit": lineage_root,
            "reviewedBlobsSha256": remote_auditor.REVIEWED_POLICY.policy_sha256(),
            "legacyRefs": legacy_refs or [],
        }), encoding="utf-8")
        return path

    def test_clean_current_tree_passes(self):
        self.commit("clean")
        report = auditor.audit(self.root, False)
        self.assertEqual(report["current"]["findings"], [])
        self.assertEqual(report["dependencies"]["findings"], [])

    def test_values_are_never_emitted(self):
        canary = "ghp_" + ("A" * 36)
        (self.root / "unsafe.txt").write_text(canary, encoding="utf-8")
        report = auditor.audit(self.root, False)
        rendered = json.dumps(report)
        self.assertNotIn(canary, rendered)
        self.assertEqual(report["current"]["findings"][0]["labels"], ["github-token"])

    def test_history_finds_removed_secret_without_emitting_it(self):
        canary = "OPENCLAW_" + "GATEWAY_TOKEN=" + "history_canary_value_123"
        path = self.root / "runtime.env"
        path.write_text(canary + "\n", encoding="utf-8")
        self.commit("unsafe")
        path.write_text("OPENCLAW_" + "GATEWAY_TOKEN=CHANGEME\n", encoding="utf-8")
        self.commit("sanitize")
        report = auditor.audit(self.root, True)
        self.assertEqual(report["current"]["findings"], [])
        self.assertTrue(any("gateway-token" in finding["labels"] for finding in report["history"]["findings"]))
        self.assertNotIn("history_canary_value_123", json.dumps(report))

    def test_source_template_token_is_not_misclassified_as_a_secret(self):
        path = self.root / "service-template.py"
        path.write_text('line = f"OPENCLAW_" + "GATEWAY_TOKEN={token}"\n', encoding="utf-8")
        self.commit("source template")
        report = auditor.audit(self.root, True)
        self.assertEqual(report["current"]["findings"], [])
        self.assertEqual(report["history"]["findings"], [])

    def test_dependency_ranges_are_refused(self):
        package = self.root / "plugin" / "package.json"
        package.write_text(json.dumps({"dependencies": {"typebox": "^1.3.9"}}), encoding="utf-8")
        labels = [finding["label"] for finding in auditor.dependency_findings(self.root)]
        self.assertTrue(any("non-exact dependency" in label for label in labels))

    def test_unhashed_python_requirement_is_refused(self):
        self.requirements.write_text("example==1.2.3\n", encoding="utf-8")
        labels = [finding["label"] for finding in auditor.dependency_findings(self.root)]
        self.assertTrue(any("fully SHA-256 locked" in label for label in labels))

    def test_duplicate_or_weak_python_hash_is_refused(self):
        digest = "b" * 64
        self.requirements.write_text(
            f"example==1.2.3 \\\n"
            f"  --hash=sha256:{digest} \\\n"
            f"  --hash=sha256:{digest}\n", encoding="utf-8",
        )
        labels = [finding["label"] for finding in auditor.dependency_findings(self.root)]
        self.assertTrue(any("duplicate wheel hash" in label for label in labels))

    def test_remote_audit_finds_secret_reachable_only_from_pull_ref(self):
        self.commit("clean main")
        lineage_root = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        with tempfile.TemporaryDirectory() as remote_temporary:
            remote = Path(remote_temporary) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "push", "-q", str(remote), "HEAD:refs/heads/main"],
                cwd=self.root, check=True,
            )
            secret = self.root / "retired-runtime.env"
            secret.write_text(
                "OPENCLAW_" + "GATEWAY_TOKEN=remote_pull_ref_canary_123\n",
                encoding="utf-8",
            )
            self.commit("hidden pull ref")
            subprocess.run(
                ["git", "push", "-q", str(remote), "HEAD:refs/pull/1/head"],
                cwd=self.root, check=True,
            )
            report = remote_auditor.audit_remote_refs(
                self.root, str(remote), self.policy(lineage_root, remote), enforce_missing=False,
            )
        self.assertEqual(report["remoteRefCount"], 2)
        self.assertEqual(report["releaseGate"]["status"], "fail")
        self.assertTrue(any(
            "gateway-token" in finding["labels"]
            for finding in report["releaseGate"]["history"]["findings"]
        ))
        self.assertNotIn("remote_pull_ref_canary_123", json.dumps(report))

    def test_remote_audit_passes_clean_refs(self):
        self.commit("clean remote")
        lineage_root = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        with tempfile.TemporaryDirectory() as remote_temporary:
            remote = Path(remote_temporary) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "push", "-q", str(remote), "HEAD:refs/heads/main"],
                cwd=self.root, check=True,
            )
            report = remote_auditor.audit_remote_refs(
                self.root, str(remote), self.policy(lineage_root, remote), enforce_missing=False,
            )
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["releaseGate"]["sourceHeadAdvertised"])
        self.assertEqual(report["releaseGate"]["history"]["findings"], [])

    def test_exact_legacy_ref_findings_are_reported_without_blocking_pixel(self):
        self.commit("clean Pixel root")
        lineage_root = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        secret = self.root / "retired-runtime.env"
        secret.write_text(
            "OPENCLAW_" + "GATEWAY_TOKEN=acknowledged_legacy_canary_123\n",
            encoding="utf-8",
        )
        self.commit("legacy ref fixture")
        legacy_object = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        subprocess.run(["git", "checkout", "-q", "--detach", lineage_root], cwd=self.root, check=True)
        with tempfile.TemporaryDirectory() as remote_temporary:
            remote = Path(remote_temporary) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "push", "-q", str(remote), "HEAD:refs/heads/main"],
                cwd=self.root, check=True,
            )
            subprocess.run(
                ["git", "push", "-q", str(remote), f"{legacy_object}:refs/pull/1/head"],
                cwd=self.root, check=True,
            )
            policy = self.policy(lineage_root, remote, [{
                "ref": "refs/pull/1/head",
                "object": legacy_object,
                "reason": "exact acknowledged fixture",
            }])
            report = remote_auditor.audit_remote_refs(self.root, str(remote), policy, enforce_missing=False)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["releaseGate"]["history"]["findings"], [])
        self.assertTrue(any(
            "gateway-token" in finding["labels"]
            for finding in report["legacyHistory"]["history"]["findings"]
        ))
        self.assertNotIn("acknowledged_legacy_canary_123", json.dumps(report))

    def test_real_repo_acknowledged_fixtures_are_visible_and_policy_passes(self):
        report = auditor.audit(ROOT, True)
        self.assertEqual(report["policyViolations"], [])
        self.assertEqual(report["history"]["findings"], [])
        fixtures = report["history"]["acknowledgedFixtures"]
        self.assertEqual(len(fixtures), 3)
        self.assertEqual({f["object"] for f in fixtures}, {
            "708540b18b96bc13efa4389f8a4b6c17a958938a",
            "753d940eacb5af96667a164120e5735d45da0ed3",
            "99336e9a0a6ff88af71f1d6103b1f8e6012b3b3c",
        })

    def test_current_tree_finding_is_never_acknowledged(self):
        canary = "AKIA" + "A" * 16
        (self.root / "unsafe.env").write_text("AWS_KEY=" + canary + "\n", encoding="utf-8")
        report = auditor.audit(self.root, True)
        self.assertTrue(any(f["path"] == "unsafe.env" for f in report["current"]["findings"]))
        self.assertEqual(report["history"]["acknowledgedFixtures"], [])

    def test_changed_or_unacknowledged_non_lineage_refs_fail_closed(self):
        self.commit("pre-lineage history")
        pre_lineage = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        (self.root / "pixel.txt").write_text("Pixel begins\n", encoding="utf-8")
        self.commit("Pixel lineage root")
        lineage_root = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        with tempfile.TemporaryDirectory() as remote_temporary:
            remote = Path(remote_temporary) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "push", "-q", str(remote), "HEAD:refs/heads/main"],
                cwd=self.root, check=True,
            )
            subprocess.run(
                ["git", "push", "-q", str(remote), f"{pre_lineage}:refs/pull/1/head"],
                cwd=self.root, check=True,
            )
            unknown = remote_auditor.audit_remote_refs(
                self.root, str(remote), self.policy(lineage_root, remote), enforce_missing=False,
            )
            drifted = remote_auditor.audit_remote_refs(
                self.root, str(remote), self.policy(lineage_root, remote, [{
                    "ref": "refs/pull/1/head",
                    "object": "f" * 40,
                    "reason": "wrong immutable identity",
                }]), enforce_missing=False,
            )
            wrong_remote = remote_auditor.audit_remote_refs(
                self.root, str(remote), self.policy(lineage_root, remote.parent / "other.git", [{
                    "ref": "refs/pull/1/head",
                    "object": pre_lineage,
                    "reason": "exact acknowledged fixture",
                }]), enforce_missing=False,
            )
        self.assertEqual(unknown["status"], "fail")
        self.assertIn(
            "unacknowledged-non-lineage-ref",
            {item["code"] for item in unknown["releaseGate"]["policyViolations"]},
        )
        self.assertEqual(drifted["status"], "fail")
        self.assertIn(
            "legacy-ref-drift",
            {item["code"] for item in drifted["releaseGate"]["policyViolations"]},
        )
        self.assertEqual(wrong_remote["status"], "fail")
        self.assertIn(
            "remote-identity-mismatch",
            {item["code"] for item in wrong_remote["releaseGate"]["policyViolations"]},
        )


    def test_missing_reviewed_object_fails_closed(self):
        self.commit("clean")
        report = auditor.audit(self.root, False)
        self.assertTrue(report["policyViolations"])
        self.assertTrue(any("not reachable in history" in violation for violation in report["policyViolations"]))

    def test_remote_audit_rejects_missing_reviewed_object(self):
        self.commit("clean remote")
        lineage_root = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        with tempfile.TemporaryDirectory() as remote_temporary:
            remote = Path(remote_temporary) / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(
                ["git", "push", "-q", str(remote), "HEAD:refs/heads/main"],
                cwd=self.root, check=True,
            )
            report = remote_auditor.audit_remote_refs(
                self.root, str(remote), self.policy(lineage_root, remote),
            )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(
            item.get("code") == "reviewed-policy-violation"
            for item in report["releaseGate"]["policyViolations"]
        ))

    def test_remote_ref_policy_missing_and_invalid_utf8_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            missing = Path(t) / "missing.json"
            with self.assertRaises(remote_auditor.RemoteAuditError):
                remote_auditor.load_policy(missing)
            bad = Path(t) / "bad.json"
            bad.write_bytes(b"\xff\xfe not utf-8")
            with self.assertRaises(remote_auditor.RemoteAuditError):
                remote_auditor.load_policy(bad)

if __name__ == "__main__":
    unittest.main()
