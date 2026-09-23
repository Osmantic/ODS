import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "deploy" / "github-broker" / "broker.py"
SPEC = importlib.util.spec_from_file_location("pixel_github_broker", SOURCE)
BROKER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BROKER)


def private_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


class GitHubBrokerTests(unittest.TestCase):
    action_id = "github-1786512000000-a1b2c3d4"

    def fixture(self, directory, operation="create-issue", values=None):
        root = Path(directory)
        policy = root / "policy.json"
        proposal = root / "proposal.json"
        token = root / "token"
        journal = root / "journal"
        result = root / "result.json"
        private_json(policy, {
            "schemaVersion": 1, "enabled": True, "allowedRepositories": ["Osmantic/Pixel"],
            "allowedOperations": ["create-issue", "create-pull-request", "comment"],
            "maxBodyBytes": 32768, "maxReconcilePages": 3, "apiVersion": BROKER.API_VERSION,
            "boundary": "Owner-private allowlist only. Policy grants no credentials, proposal approval, retries, merges, pushes, branch mutation, or repositories not named here.",
        })
        if values is None:
            values = {"title": "Release audit", "body": "PRIVATE ISSUE BODY", "labels": ["audit"]}
        private_json(proposal, {
            "schemaVersion": 1, "actionId": self.action_id, "source": "pixel-owner-conversation",
            "operation": operation, "status": "pending-operator-approval", "createdAt": "2026-08-12T12:00:00Z",
            "repository": "Osmantic/Pixel", "values": values, "boundary": BROKER.BOUNDARY,
        })
        token.write_text("github_pat_" + "A" * 40, encoding="utf-8")
        if os.name != "nt":
            token.chmod(0o600)
        return {"action_id": self.action_id, "proposal_path": proposal, "policy_path": policy, "token_path": token, "journal_root": journal, "result_path": result}

    def issue_response(self, body, number=42):
        return {"id": 101, "number": number, "title": "Release audit", "body": body, "labels": [{"name": "audit"}], "html_url": f"https://github.com/Osmantic/Pixel/issues/{number}"}

    def test_apply_binds_exact_marker_and_shared_journal_without_leaking_body(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            requests = []
            def request(token, method, path, body):
                requests.append((token, method, path, body))
                return self.issue_response(body["body"]), {}
            receipt = BROKER.apply_action(**arguments, request=request)
            self.assertEqual(receipt["providerObjectId"], 42)
            self.assertEqual(receipt["reconciliation"], "synchronous-provider-response")
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][1:3], ("POST", "/repos/Osmantic/Pixel/issues"))
            self.assertRegex(requests[0][3]["body"], r"<!-- pixel-action:[a-f0-9]{64} -->")
            status = BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["idempotencyMode"], "provider-reconciliation-marker")
            journal_bytes = b"".join(path.read_bytes() for path in (arguments["journal_root"] / self.action_id).glob("*.json"))
            self.assertNotIn(b"PRIVATE ISSUE BODY", journal_bytes)
            self.assertNotIn(b"github_pat_", journal_bytes)

    def test_apply_fails_closed_when_confirmed_hash_differs_from_operative_read(self):
        # Red-team finding (pass 2): main() confirmed --proposal-sha256 on an earlier read,
        # but apply_action/reconcile_action re-read the proposal and journaled/acted on THAT
        # read's hash without comparing it back to the confirmation -- a TOCTOU window where a
        # concurrent local writer could get the broker to act on unconfirmed bytes. The
        # operative read must now be bound to the confirmed hash and fail closed on mismatch,
        # before any journal proposal or provider call.
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            called = []
            def request(token, method, path, body):
                called.append((method, path))
                return self.issue_response(body["body"]), {}
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "differs from exact confirmation"):
                BROKER.apply_action(**arguments, request=request, expected_proposal_sha256="0" * 64)
            self.assertEqual(called, [])
            self.assertFalse((arguments["journal_root"] / self.action_id).exists())

    def test_apply_accepts_the_matching_confirmed_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            def request(token, method, path, body):
                return self.issue_response(body["body"]), {}
            correct = BROKER.journal_sha256(arguments["proposal_path"].read_bytes())
            receipt = BROKER.apply_action(**arguments, request=request, expected_proposal_sha256=correct)
            self.assertEqual(receipt["providerObjectId"], 42)
            self.assertEqual(BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)["state"], "succeeded")

    def test_proposal_with_a_vendor_secret_in_the_body_is_rejected(self):
        # Defense-in-depth: an injected/accidental vendor credential in a GitHub issue/PR/comment
        # body would egress to a (potentially public) issue even after owner approval. The broker
        # rejects such a proposal before any provider call. Only unambiguous vendor formats trip it.
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory, values={"title": "Release audit", "body": "leak ghp_" + "a" * 30 + " here", "labels": ["audit"]})
            called = []
            def request(token, method, path, body):
                called.append((method, path))
                return self.issue_response(body["body"]), {}
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "credential must not be published"):
                BROKER.apply_action(**arguments, request=request)
            self.assertEqual(called, [])
            self.assertFalse((arguments["journal_root"] / self.action_id).exists())
        # Prose that merely discusses credentials, a commit SHA, and a placeholder are NOT rejected.
        for benign in ["Set your API_KEY in .env; see commit " + "a" * 40, "Rotate DEPLOY_TOKEN safely", "Use Bearer YOUR_TOKEN_HERE header"]:
            self.assertIsNone(BROKER.reject_outbound_secrets({"title": "docs", "body": benign}), benign)

    def test_lost_response_blocks_retry_then_reconciles_without_second_post(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            posts = []
            marked = None
            def lose(token, method, path, body):
                nonlocal marked
                posts.append(path)
                marked = body["body"]
                raise TimeoutError("response lost after acceptance")
            with self.assertRaisesRegex(TimeoutError, "lost"):
                BROKER.apply_action(**arguments, request=lose)
            status = BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)
            self.assertEqual(status["state"], "unknown")
            self.assertFalse(status["retryAllowed"])
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "not retried"):
                BROKER.apply_action(**arguments, request=lose)
            self.assertEqual(len(posts), 1)
            def observe(token, method, path, body):
                self.assertEqual(method, "GET")
                return [self.issue_response(marked)], {}
            receipt = BROKER.reconcile_action(**arguments, request=observe)
            self.assertEqual(receipt["providerObjectId"], 42)
            self.assertEqual(receipt["reconciliation"], "provider-list-after-indeterminate-write")
            self.assertEqual(len(posts), 1)

    def test_eventual_consistency_and_duplicate_markers_never_become_false_success(self):
        for observed in ([], ["one", "two"]):
            with self.subTest(count=len(observed)), tempfile.TemporaryDirectory() as directory:
                arguments = self.fixture(directory)
                marked = None
                def lose(token, method, path, body):
                    nonlocal marked
                    marked = body["body"]
                    raise TimeoutError("unknown")
                with self.assertRaises(TimeoutError):
                    BROKER.apply_action(**arguments, request=lose)
                items = [self.issue_response(marked, 42 + index) for index, _item in enumerate(observed)]
                with self.assertRaisesRegex(BROKER.GitHubBrokerError, "remains indeterminate"):
                    BROKER.reconcile_action(**arguments, request=lambda *_args: (items, {}))
                self.assertFalse(arguments["result_path"].exists())
                status = BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)
                self.assertEqual(status["state"], "unknown")

    def test_result_fsync_crash_is_rebuilt_from_the_same_observed_object(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            marked = None
            original_write = BROKER._write_result
            def accepted(token, method, path, body):
                nonlocal marked
                marked = body["body"]
                return self.issue_response(marked), {}
            try:
                BROKER._write_result = lambda *_args: (_ for _ in ()).throw(OSError("result fsync crash"))
                with self.assertRaisesRegex(OSError, "fsync crash"):
                    BROKER.apply_action(**arguments, request=accepted)
            finally:
                BROKER._write_result = original_write
            self.assertEqual(BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)["state"], "succeeded")
            receipt = BROKER.reconcile_action(**arguments, request=lambda *_args: ([self.issue_response(marked)], {}))
            self.assertEqual(receipt["providerObjectId"], 42)

    def test_policy_scope_and_exact_proposal_hash_fail_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            proposal = json.loads(arguments["proposal_path"].read_text())
            proposal["repository"] = "Attacker/Other"
            private_json(arguments["proposal_path"], proposal)
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "allowlist"):
                BROKER.apply_action(**arguments, request=lambda *_args: self.fail("network must not run"))

    def test_existing_result_requires_the_matching_terminal_journal_and_blocks_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            captured = None
            def request(token, method, path, body):
                nonlocal captured
                captured = body["body"]
                return self.issue_response(captured), {}
            applied = BROKER.apply_action(**arguments, request=request)
            self.assertEqual(BROKER.reconcile_action(**arguments, request=lambda *_args: self.fail("settled result must not call GitHub")), applied)
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "already exists"):
                BROKER.apply_action(**arguments, request=lambda *_args: self.fail("duplicate apply must not call GitHub"))
            forged = json.loads(arguments["result_path"].read_text())
            forged["observationSha256"] = "f" * 64
            private_json(arguments["result_path"], forged)
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "terminal action journal"):
                BROKER.reconcile_action(**arguments, request=lambda *_args: self.fail("forged result must not call GitHub"))

    def test_concurrent_apply_has_one_provider_post(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            calls = []
            entered = threading.Event()
            release = threading.Event()
            outcomes = []
            def request(token, method, path, body):
                calls.append(path)
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                return self.issue_response(body["body"]), {}
            def run():
                try:
                    outcomes.append(BROKER.apply_action(**arguments, request=request)["status"])
                except Exception as error:
                    outcomes.append(str(error))
            first = threading.Thread(target=run)
            second = threading.Thread(target=run)
            first.start()
            self.assertTrue(entered.wait(timeout=5))
            second.start()
            second.join(timeout=5)
            release.set()
            first.join(timeout=10); second.join(timeout=10)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(len(calls), 1)
            self.assertEqual(outcomes.count("applied"), 1)
            self.assertTrue(any("processing" in outcome or "already exists" in outcome for outcome in outcomes if outcome != "applied"))

    def test_definite_rejection_is_terminal_but_pre_network_credential_failure_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            arguments["token_path"].write_text("invalid", encoding="utf-8")
            with self.assertRaisesRegex(BROKER.GitHubBrokerError, "credential format"):
                BROKER.apply_action(**arguments, request=lambda *_args: self.fail("network must not run"))
            self.assertTrue(BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)["retryAllowed"])
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.fixture(directory)
            rejected = BROKER.GitHubHTTPError(422, "e" * 64)
            with self.assertRaises(BROKER.GitHubHTTPError):
                BROKER.apply_action(**arguments, request=lambda *_args: (_ for _ in ()).throw(rejected))
            status = BROKER.ExternalActionJournal(arguments["journal_root"]).status(self.action_id)
            self.assertEqual(status["state"], "failed")
            self.assertFalse(status["retryAllowed"])

    def test_comment_and_pull_request_use_narrow_provider_paths(self):
        fixtures = [
            ("comment", {"issueNumber": 7, "body": "Review complete"}, "/repos/Osmantic/Pixel/issues/7/comments", {"id": 99, "body": None, "html_url": "https://github.com/Osmantic/Pixel/issues/7#issuecomment-99"}),
            ("create-pull-request", {"title": "Release", "body": "Ready", "head": "Osmantic:feature", "base": "main", "draft": True}, "/repos/Osmantic/Pixel/pulls", {"id": 100, "number": 8, "title": "Release", "body": None, "draft": True, "head": {"label": "Osmantic:feature", "ref": "feature"}, "base": {"ref": "main"}, "html_url": "https://github.com/Osmantic/Pixel/pull/8"}),
        ]
        for operation, values, expected_path, response in fixtures:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                arguments = self.fixture(directory, operation, values)
                def request(token, method, path, body):
                    self.assertEqual(path, expected_path)
                    return {**response, "body": body["body"]}, {}
                receipt = BROKER.apply_action(**arguments, request=request)
                self.assertEqual(receipt["operation"], operation)


if __name__ == "__main__":
    unittest.main()
