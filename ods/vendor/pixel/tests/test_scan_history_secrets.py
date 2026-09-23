import hashlib
import importlib.util
import gc
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pixel_scan_history_secrets", ROOT / "scripts/scan-history-secrets.py")
scanner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scanner)


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def git_blob(root, object_id):
    return subprocess.run(
        ["git", "cat-file", "blob", object_id],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def make_repo(files):
    d = Path(tempfile.mkdtemp())
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@example.com")
    git(d, "config", "user.name", "t")
    for name, content in files.items():
        (d / name).write_text(content, encoding="utf-8")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "x")
    return d


def v2_policy(entries):
    return {
        "schemaVersion": 2,
        "repository": "Osmantic/Pixel",
        "reviewedBlobs": entries,
        "byBlob": {entry["blobSha1"]: entry for entry in entries},
    }


class HistorySecretScanTests(unittest.TestCase):
    def test_clean_history_is_closed(self):
        d = make_repo({"a.py": "x = 1\n", "README.md": "hello\n"})
        try:
            result = scanner.scan(d, {})
            self.assertEqual(result["findingsCount"], 0)
            self.assertEqual(result["status"], "closed")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_planted_secret_trips_the_gate(self):
        d = make_repo({"leak.env": "AWS_KEY=" + "AKIA" + "A" * 16 + "\n"})
        try:
            result = scanner.scan(d, {})
            self.assertGreaterEqual(result["unreviewedCount"], 1)
            self.assertEqual(result["status"], "open")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_allowlisting_the_reviewed_blob_closes_it(self):
        d = make_repo({"leak.env": "AWS_KEY=" + "AKIA" + "A" * 16 + "\n"})
        try:
            first = scanner.scan(d, {})
            oid = first["findings"][0].split(":", 1)[0]
            payload = git_blob(d, oid)
            policy = v2_policy([{
                "blobSha1": oid,
                "payloadSha256": hashlib.sha256(payload).hexdigest(),
                "path": "leak.env",
                "labels": ["AWS access key"],
                "auditSourceLabels": ["aws-access-key"],
                "reason": "planted synthetic test fixture for scanner discrimination",
            }])
            result = scanner.scan(d, policy)
            self.assertEqual(result["unreviewedCount"], 0)
            self.assertEqual(result["policyViolationCount"], 0)
            self.assertEqual(result["status"], "closed")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_history_object_stream_is_closed_without_resource_warning(self):
        d = make_repo({"a.py": "x = 1\n"})
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                scanner.scan(d, {})
                gc.collect()
            self.assertFalse(
                [warning for warning in caught if issubclass(warning.category, ResourceWarning)],
                caught,
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_git_object_stream_failure_fails_the_gate(self):
        d = make_repo({"a.py": "x = 1\n"})
        real_popen = subprocess.Popen

        class FailedObjectStream:
            def __init__(self, args):
                self.args = args
                self.stdout = io.BytesIO(b"")
                self.returncode = None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.stdout.close()
                self.returncode = 19

        def popen(args, *positional, **keywords):
            if args == ["git", "cat-file", "--batch-all-objects", "--batch"]:
                return FailedObjectStream(args)
            return real_popen(args, *positional, **keywords)

        try:
            with mock.patch.object(scanner.subprocess, "Popen", side_effect=popen):
                with self.assertRaisesRegex(subprocess.CalledProcessError, "exit status 19"):
                    scanner.scan(d, {})
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_stale_allowlist_entry_fails_closed(self):
        d = make_repo({"a.py": "x = 1\n"})
        try:
            policy = v2_policy([{
                "blobSha1": "0" * 40,
                "payloadSha256": "0" * 64,
                "path": "a.py",
                "labels": ["AWS access key"],
                "auditSourceLabels": ["aws-access-key"],
                "reason": "an entry that matches no real finding",
            }])
            result = scanner.scan(d, policy)
            self.assertGreaterEqual(result["staleAllowlistCount"], 1)
            self.assertEqual(result["status"], "open")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_allowlist_loader_rejects_malformed_entries(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.json"
            p.write_text(json.dumps({"reviewedBlobs": [{"blobSha1": "nope", "reason": "x" * 20}]}))
            with self.assertRaises(SystemExit):
                scanner.load_allowlist(p)
            p.write_text(json.dumps({"reviewedBlobs": [{"blobSha1": "a" * 40, "reason": "too short"}]}))
            # reason >= 12 chars required
            p.write_text(json.dumps({"reviewedBlobs": [{"blobSha1": "a" * 40, "reason": "short"}]}))
            with self.assertRaises(SystemExit):
                scanner.load_allowlist(p)

    def test_reviewed_blob_payload_or_path_drift_fails_closed(self):
        d = make_repo({"leak.env": "AWS_KEY=" + "AKIA" + "A" * 16 + "\n"})
        try:
            first = scanner.scan(d, {})
            oid = first["findings"][0].split(":", 1)[0]
            payload = git_blob(d, oid)
            base = {
                "blobSha1": oid,
                "payloadSha256": hashlib.sha256(payload).hexdigest(),
                "path": "leak.env",
                "labels": ["AWS access key"],
                "auditSourceLabels": ["aws-access-key"],
                "reason": "planted synthetic test fixture for scanner discrimination",
            }
            drifted_payload = dict(base)
            drifted_payload["payloadSha256"] = "f" * 64
            result = scanner.scan(d, v2_policy([drifted_payload]))
            self.assertGreaterEqual(result["policyViolationCount"], 1)
            self.assertEqual(result["status"], "open")
            drifted_path = dict(base)
            drifted_path["path"] = "other.env"
            result = scanner.scan(d, v2_policy([drifted_path]))
            self.assertGreaterEqual(result["policyViolationCount"], 1)
            self.assertEqual(result["status"], "open")
            drifted_labels = dict(base)
            drifted_labels["labels"] = ["AWS access key", "extra"]
            result = scanner.scan(d, v2_policy([drifted_labels]))
            self.assertGreaterEqual(result["policyViolationCount"], 1)
            self.assertEqual(result["status"], "open")
            drifted_audit = dict(base)
            drifted_audit["auditSourceLabels"] = ["aws-access-key", "github-token"]
            result = scanner.scan(d, v2_policy([drifted_audit]))
            self.assertGreaterEqual(result["policyViolationCount"], 1)
            self.assertEqual(result["status"], "open")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_stale_nonempty_audit_source_fixture_entry_fails_closed(self):
        d = make_repo({"clean.py": "x = 1\n"})
        try:
            policy = v2_policy([{
                "blobSha1": "1" * 40,
                "payloadSha256": "1" * 64,
                "path": "clean.py",
                "labels": ["AWS access key"],
                "auditSourceLabels": ["aws-access-key"],
                "reason": "stale nonempty audit-source fixture entry",
            }])
            result = scanner.scan(d, policy)
            self.assertGreaterEqual(result["staleAllowlistCount"], 1)
            self.assertEqual(result["status"], "open")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_real_repo_history_is_closed_under_the_committed_allowlist(self):
        # The actual repository: every credential-shaped historical blob is a reviewed test
        # fixture, so with the committed allowlist the gate is genuinely closed.
        result = scanner.scan(ROOT, scanner.load_allowlist(ROOT / "security-evals/historical-secret-closure/reviewed-blobs.json"))
        self.assertEqual(result["unreviewedCount"], 0, f"unreviewed historical secrets: {result.get('unreviewed')}")
        self.assertEqual(result["staleAllowlistCount"], 0, f"stale allowlist entries: {result.get('staleAllowlist')}")
        self.assertEqual(result["status"], "closed")


    def test_custom_policy_digest_is_bound_to_the_loaded_policy(self):
        d = make_repo({"leak.env": "AWS_KEY=" + "AKIA" + "A" * 16 + "\n"})
        try:
            first = scanner.scan(d, {})
            oid = first["findings"][0].split(":", 1)[0]
            payload = git_blob(d, oid)
            policy = v2_policy([{
                "blobSha1": oid,
                "payloadSha256": hashlib.sha256(payload).hexdigest(),
                "path": "leak.env",
                "labels": ["AWS access key"],
                "auditSourceLabels": ["aws-access-key"],
                "reason": "planted synthetic test fixture for scanner discrimination",
            }])
            result = scanner.scan(d, policy)
            self.assertEqual(result["reviewedPolicySha256"], scanner._policy_sha256(policy))
            self.assertNotEqual(result["reviewedPolicySha256"], scanner.REVIEWED_POLICY.policy_sha256())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_allowlist_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            missing = Path(t) / "missing.json"
            with self.assertRaises(SystemExit):
                scanner.load_allowlist(missing)

    def test_reviewed_blob_at_extra_path_fails_closed(self):
        d = make_repo({
            "leak.env": "AWS_KEY=" + "AKIA" + "A" * 16 + "\n",
            "copy.env": "AWS_KEY=" + "AKIA" + "A" * 16 + "\n",
        })
        try:
            first = scanner.scan(d, {})
            oid = first["findings"][0].split(":", 1)[0]
            payload = git_blob(d, oid)
            policy = v2_policy([{
                "blobSha1": oid,
                "payloadSha256": hashlib.sha256(payload).hexdigest(),
                "path": "leak.env",
                "labels": ["AWS access key"],
                "auditSourceLabels": ["aws-access-key"],
                "reason": "planted synthetic test fixture for scanner discrimination",
            }])
            result = scanner.scan(d, policy)
            self.assertGreaterEqual(result["policyViolationCount"], 1)
            self.assertEqual(result["status"], "open")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_allowlist_loader_rejects_v1_policy(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "v1.json"
            p.write_text(json.dumps({
                "schemaVersion": 1,
                "reviewedBlobs": [{
                    "blobSha1": "a" * 40, "labels": ["AWS access key"], "reason": "x" * 20,
                }],
            }), encoding="utf-8")
            with self.assertRaises(SystemExit):
                scanner.load_allowlist(p)

    def test_allowlist_loader_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "dup.json"
            p.write_text('{"reviewedBlobs": [{"blobSha1": "' + "a" * 40 + '", "blobSha1": "' + "b" * 40 + '"}]}')
            with self.assertRaises(SystemExit):
                scanner.load_allowlist(p)

    def test_empty_audit_source_fixture_at_two_paths_fails_closed(self):
        # A reviewed scanner finding whose source auditor flags no labels must still
        # fail closed when the same blob is reachable at an additional path. The
        # scan-history-secrets scanner flags the token variable (non-placeholder in
        # its set) while audit_source treats "__pixel_*" as a placeholder and emits
        # no labels, so auditSourceLabels is [] and the old over-broad exception
        # would have skipped the extra-path rule. git rev-list reports each blob
        # once, so inject a second reachable path for the same reviewed blob to
        # exercise the scanner's fail-closed decision directly.
        content = "OPENCLAW_" + "GATEWAY_TOKEN=__pixel_reviewed_var\n"
        d = make_repo({"leak.env": content})
        try:
            first = scanner.scan(d, {})
            oid = first["findings"][0].split(":", 1)[0]
            self.assertEqual(first["findings"], [f"{oid}: non-placeholder gateway token"])
            payload = git_blob(d, oid)
            policy = v2_policy([{
                "blobSha1": oid,
                "payloadSha256": hashlib.sha256(payload).hexdigest(),
                "path": "leak.env",
                "labels": ["non-placeholder gateway token"],
                "auditSourceLabels": [],
                "reason": "planted synthetic test fixture for scanner discrimination",
            }])
            real_run = scanner.subprocess.run

            def patched_run(args, *positional, **keywords):
                result = real_run(args, *positional, **keywords)
                if args == ["git", "rev-list", "--objects", "--all"]:
                    wrapper = mock.Mock()
                    wrapper.stdout = result.stdout + f"{oid} leak-copy.env\n".encode()
                    wrapper.returncode = result.returncode
                    return wrapper
                return result

            with mock.patch.object(scanner.subprocess, "run", side_effect=patched_run):
                result = scanner.scan(d, policy)
            self.assertTrue(any(
                "reachable at an additional path" in violation
                for violation in result["policyViolations"]
            ))
            self.assertGreaterEqual(result["policyViolationCount"], 1)
            self.assertEqual(result["status"], "open")
        finally:
            shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
