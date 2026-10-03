import importlib.util
import json
import os
import stat
import tempfile
import unittest
from argparse import Namespace
from datetime import timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_frontier_live_qualification", ROOT / "scripts/frontier-live-qualify.py",
)
live = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(live)


class FrontierLiveQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "operator-state"
        self.previous_state = os.environ.get("PIXEL_FRONTIER_LIVE_QUALIFICATION_DIR")
        os.environ["PIXEL_FRONTIER_LIVE_QUALIFICATION_DIR"] = str(self.state)

    def tearDown(self):
        if self.previous_state is None:
            os.environ.pop("PIXEL_FRONTIER_LIVE_QUALIFICATION_DIR", None)
        else:
            os.environ["PIXEL_FRONTIER_LIVE_QUALIFICATION_DIR"] = self.previous_state
        self.temp.cleanup()

    def authorization(self, mode="chatgpt", maximum_cost=None):
        issued = live.now()
        return {
            "$schema": "./schemas/frontier-live-authorization-v1.schema.json",
            "schemaVersion": 1,
            "authorizationId": "liveauth-1786195551000-aabbccddeeff",
            "purpose": "pixel-frontier-live-qualification",
            "issuedAt": live.iso(issued),
            "expiresAt": live.iso(issued + timedelta(minutes=30)),
            "authMode": mode,
            "maxProviderCalls": 1,
            "maxInputTokens": 12000,
            "maxOutputTokens": 256,
            "maxEstimatedCostMicros": maximum_cost,
            "acknowledgements": {
                "syntheticOnly": True,
                "providerUsageAuthorized": True,
                "oneCallOnly": True,
                "outputIsUntrusted": True,
                "chatgptPlanOrCreditsAuthorized": mode == "chatgpt",
                "apiPlatformBillingAuthorized": mode == "api-key",
            },
        }

    def write_authorization(self, value=None):
        path = self.root / "authorization.json"
        live.atomic_json(path, value or self.authorization())
        return path

    def test_authorization_command_requires_explicit_billing_boundary_and_private_file(self):
        output = self.root / "chatgpt-authorization.json"
        args = Namespace(
            output=output,
            auth_mode="chatgpt",
            expires_minutes=30,
            max_estimated_cost_micros=None,
            authorize_one_synthetic_provider_call=True,
            authorize_chatgpt_plan_or_credits=True,
            authorize_api_billing=False,
        )
        projection = live.authorization_command(args)
        self.assertEqual(projection["status"], "authorization-created")
        self.assertEqual(projection["authMode"], "chatgpt")
        authorization, _ = live.load_authorization(output)
        self.assertTrue(authorization["acknowledgements"]["providerUsageAuthorized"])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        args.output = self.root / "wrong-boundary.json"
        args.authorize_chatgpt_plan_or_credits = False
        args.authorize_api_billing = True
        with self.assertRaisesRegex(live.QualificationError, "ChatGPT authorization"):
            live.authorization_command(args)

    def test_api_authorization_requires_a_small_explicit_platform_cost_ceiling(self):
        value = self.authorization("api-key", 100_000)
        self.assertEqual(live.validate_authorization(value), value)
        value["maxEstimatedCostMicros"] = None
        with self.assertRaisesRegex(live.QualificationError, "API authorization requires"):
            live.validate_authorization(value)
        value["maxEstimatedCostMicros"] = 100_001
        value["acknowledgements"]["chatgptPlanOrCreditsAuthorized"] = True
        with self.assertRaisesRegex(live.QualificationError, "separate Platform billing"):
            live.validate_authorization(value)

    def test_api_preflight_requires_consent_for_the_worst_case_metered_estimate(self):
        authorization = self.authorization("api-key", 20_000)
        preflight = {
            "schemaVersion": 1,
            "status": "ready",
            "providerAuthMode": "api-key",
            "billingBoundary": "platform-api",
            "policyHash": "a" * 64,
            "qualificationLimits": {
                "maxProviderCalls": 1,
                "maxInputTokens": 12000,
                "maxOutputTokens": 256,
                "maxEstimatedCostMicros": 1_000_000,
                "requiredMaxEstimatedCostMicros": 26_048,
            },
            "privacy": "content-free",
        }
        with self.assertRaisesRegex(live.QualificationError, "below the worst-case"):
            live.validate_preflight(preflight, authorization)

    def test_authorization_rejects_expiry_widening_unknown_fields_and_duplicate_json(self):
        value = self.authorization()
        value["expiresAt"] = live.iso(live.now() + timedelta(hours=25))
        with self.assertRaisesRegex(live.QualificationError, "exceeds 24 hours"):
            live.validate_authorization(value)
        value = self.authorization()
        value["credential"] = "must-never-be-accepted"
        with self.assertRaisesRegex(live.QualificationError, "unknown fields"):
            live.validate_authorization(value)
        path = self.root / "duplicate.json"
        path.write_text('{"schemaVersion":1,"schemaVersion":1}\n', encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaisesRegex(live.QualificationError, "duplicate JSON field"):
            live.safe_read_json(path, 1024, private=True)

    def test_synthetic_payload_is_byte_stable_and_contains_no_random_identifier(self):
        first = live.synthetic_request(
            "qualification-1786195551000-aabbccddeeff",
            "frontier-1786195551000-aabbccddeeff",
            "local-1786195551000-aabbccddeeff",
            "2026-08-09T12:00:00Z",
        )
        second = live.synthetic_request(
            "qualification-1786195551001-123456abcdef",
            "frontier-1786195551001-123456abcdef",
            "local-1786195551001-123456abcdef",
            "2026-08-09T12:00:01Z",
        )
        self.assertEqual(first["payload"], second["payload"])
        self.assertNotIn(first["jobId"], json.dumps(first["payload"]))
        self.assertNotIn("qualification-", json.dumps(first["payload"]))

    @unittest.skipIf(os.name == "nt", "POSIX private-file mode check")
    def test_authorization_rejects_broad_permissions_and_hardlinks(self):
        path = self.write_authorization()
        path.chmod(0o644)
        with self.assertRaisesRegex(live.QualificationError, "mode 0600"):
            live.load_authorization(path)
        path.chmod(0o600)
        os.link(path, self.root / "authorization-hardlink.json")
        with self.assertRaisesRegex(live.QualificationError, "unsafe or oversized"):
            live.load_authorization(path)

    def test_prepare_publishes_only_fixed_synthetic_data_and_requires_second_confirmation(self):
        authorization_path = self.write_authorization()
        request_dir = self.root / "requests"
        result_dir = self.root / "results"
        request_dir.mkdir()
        result_dir.mkdir()
        environment = {
            "PIXEL_FRONTIER_BROKER_USER": "pixel-frontier-broker",
            "PIXEL_FRONTIER_BROKER_INSTALL_DIR": str(self.root / "broker"),
            "PIXEL_FRONTIER_BROKER_STATE_DIR": str(self.root / "broker-state"),
            "PIXEL_FRONTIER_POLICY_PATH": str(self.root / "policy.json"),
            "PIXEL_FRONTIER_REQUEST_DIR": str(request_dir),
            "PIXEL_FRONTIER_RESULT_DIR": str(result_dir),
        }
        preflight = {
            "schemaVersion": 1,
            "status": "ready",
            "providerAuthMode": "chatgpt",
            "billingBoundary": "chatgpt-plan-or-credits",
            "policyHash": "a" * 64,
            "qualificationLimits": {
                "maxProviderCalls": 1,
                "maxInputTokens": 12000,
                "maxOutputTokens": 256,
                "maxEstimatedCostMicros": None,
                "requiredMaxEstimatedCostMicros": None,
            },
            "privacy": "content-free",
        }

        def prepared_result(_path, _timeout):
            request_path = next(request_dir.glob("frontier-*.json"))
            request = json.loads(request_path.read_text(encoding="utf-8"))
            return {
                "jobId": request["jobId"],
                "status": "awaiting-approval",
                "taskClass": "plan_review",
                "classification": "public",
                "dataCategories": ["structural"],
                "providerAuthMode": "chatgpt",
                "providerInvoked": False,
                "executionSource": "none",
                "maxOutputTokens": 256,
                "planHash": "b" * 64,
                "capsuleHash": "c" * 64,
                "estimatedInputTokens": 300,
                "routingReceipt": {"policyHash": "a" * 64, "decision": "propose", "localAttempt": request["routing"]},
                "sanitizedPreview": {"payload": request["payload"]},
                "costEstimate": {"mode": "subscription", "estimatedAmountMicros": None},
            }

        args = Namespace(authorization=authorization_path, timeout_seconds=2)
        with mock.patch.object(live, "required_environment", return_value=environment), \
             mock.patch.object(live, "broker_command", return_value=preflight), \
             mock.patch.object(live, "wait_for_result", side_effect=prepared_result):
            projection = live.prepare_command(args)
        self.assertEqual(projection["status"], "awaiting-confirmation")
        self.assertIn("--transmit", projection["confirmCommand"])
        request = json.loads(next(request_dir.glob("frontier-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(request["classification"], "public")
        self.assertEqual(request["dataCategories"], ["structural"])
        self.assertEqual(request["routing"]["reasonCodes"], ["security-review"])
        serialized = json.dumps(request).lower()
        for forbidden in ("credential", "customer", "private-key", "api key"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(live.prepare_command(args), projection)
        self.assertEqual(len(list(request_dir.glob("frontier-*.json"))), 1)

    def test_confirm_requires_exact_hash_and_transmit_then_persists_content_free_receipt(self):
        authorization_path = self.write_authorization()
        authorization, authorization_hash = live.load_authorization(authorization_path)
        root = live.state_root()
        claim = {
            "schemaVersion": 1,
            "qualificationId": "qualification-1786195551000-aabbccddeeff",
            "authorizationHash": authorization_hash,
            "authorizationExpiresAt": authorization["expiresAt"],
            "authMode": "chatgpt",
            "billingBoundary": "chatgpt-plan-or-credits",
            "jobId": "frontier-1786195551000-aabbccddeeff",
            "planHash": "a" * 64,
            "capsuleHash": "b" * 64,
            "policyHash": "c" * 64,
            "maxProviderCalls": 1,
            "maxInputTokens": 12000,
            "maxOutputTokens": 256,
            "maxEstimatedCostMicros": None,
            "preparedAt": live.iso(),
        }
        live.atomic_json(root / "claims" / f"{authorization_hash}.json", claim)
        confirmation_hash = live.digest(live.confirmation_binding(claim))
        args = Namespace(
            qualification_id=claim["qualificationId"],
            confirmation_hash=confirmation_hash,
            authorization=authorization_path,
            transmit=False,
        )
        with self.assertRaisesRegex(live.QualificationError, "requires --transmit"):
            live.confirm_command(args)
        args.transmit = True
        args.confirmation_hash = "d" * 64
        with self.assertRaisesRegex(live.QualificationError, "hash mismatch"):
            live.confirm_command(args)
        args.confirmation_hash = confirmation_hash
        receipt = {
            "schemaVersion": 1,
            "qualificationId": claim["qualificationId"],
            "status": "pass",
            "outcome": "provider-success",
            "checkedAt": live.iso(),
            "authMode": "chatgpt",
            "billingBoundary": "chatgpt-plan-or-credits",
            "syntheticOnly": True,
            "authorizationBound": True,
            "exactApprovalBound": True,
            "maxProviderCalls": 1,
            "providerCallsObserved": 1,
            "providerCallCeilingHeld": True,
            "usage": {"available": True, "inputTokens": 300, "outputTokens": 20},
            "cost": {"mode": "subscription", "currency": None, "estimatedAmountMicros": None},
            "privacy": "Content-free qualification evidence; no prompt, response, credential, account, job, provider, or model identifier.",
        }
        environment = {
            "PIXEL_FRONTIER_BROKER_USER": "pixel-frontier-broker",
            "PIXEL_FRONTIER_BROKER_INSTALL_DIR": str(self.root / "broker"),
            "PIXEL_FRONTIER_BROKER_STATE_DIR": str(self.root / "broker-state"),
            "PIXEL_FRONTIER_POLICY_PATH": str(self.root / "policy.json"),
            "PIXEL_FRONTIER_REQUEST_DIR": str(self.root / "requests"),
            "PIXEL_FRONTIER_RESULT_DIR": str(self.root / "results"),
        }
        with mock.patch.object(live, "required_environment", return_value=environment), \
             mock.patch.object(live, "broker_command", return_value=receipt) as provider:
            observed = live.confirm_command(args)
        self.assertEqual(observed, receipt)
        self.assertEqual(provider.call_count, 1)
        stored = live.safe_read_json(root / "receipts" / f"{claim['qualificationId']}.json", 65536, private=True)
        self.assertEqual(stored, receipt)
        self.assertEqual(live.show_command(Namespace(qualification_id=claim["qualificationId"])), receipt)
        serialized = json.dumps(stored).lower()
        self.assertNotIn(claim["jobId"], serialized)
        self.assertNotIn("modelid", serialized)
        stored["privacy"] = "tampered local receipt"
        receipt_path = root / "receipts" / f"{claim['qualificationId']}.json"
        receipt_path.write_text(json.dumps(stored), encoding="utf-8")
        if os.name != "nt":
            receipt_path.chmod(0o600)
        with self.assertRaisesRegex(live.QualificationError, "receipt is invalid"):
            live.show_command(Namespace(qualification_id=claim["qualificationId"]))

    def test_receipt_validator_rejects_identifier_or_payload_widening(self):
        receipt = {
            "schemaVersion": 1,
            "qualificationId": "qualification-1786195551000-aabbccddeeff",
            "status": "fail",
            "outcome": "provider-failed",
            "checkedAt": live.iso(),
            "authMode": "api-key",
            "billingBoundary": "platform-api",
            "syntheticOnly": True,
            "authorizationBound": True,
            "exactApprovalBound": True,
            "maxProviderCalls": 1,
            "providerCallsObserved": 1,
            "providerCallCeilingHeld": True,
            "usage": {"available": True, "inputTokens": 200, "outputTokens": 256},
            "cost": {"mode": "metered", "currency": "USD", "estimatedAmountMicros": 999},
            "privacy": "Content-free qualification evidence; no prompt, response, credential, account, job, provider, or model identifier.",
            "jobId": "frontier-1786195551000-aabbccddeeff",
        }
        with self.assertRaisesRegex(live.QualificationError, "unknown fields"):
            live.validate_receipt(receipt, receipt["qualificationId"], self.authorization("api-key", 100_000))

    def test_receipt_validator_enforces_usage_outcome_and_exact_billing_consent(self):
        authorization = self.authorization("api-key", 1_000)
        receipt = {
            "schemaVersion": 1,
            "qualificationId": "qualification-1786195551000-aabbccddeeff",
            "status": "pass",
            "outcome": "provider-success",
            "checkedAt": live.iso(),
            "authMode": "api-key",
            "billingBoundary": "platform-api",
            "syntheticOnly": True,
            "authorizationBound": True,
            "exactApprovalBound": True,
            "maxProviderCalls": 1,
            "providerCallsObserved": 1,
            "providerCallCeilingHeld": True,
            "usage": {"available": True, "inputTokens": 200, "outputTokens": 20},
            "cost": {"mode": "metered", "currency": "USD", "estimatedAmountMicros": 999},
            "privacy": "Content-free qualification evidence; no prompt, response, credential, account, job, provider, or model identifier.",
        }
        self.assertEqual(live.validate_receipt(receipt, receipt["qualificationId"], authorization), receipt)
        receipt["cost"]["estimatedAmountMicros"] = 1_001
        with self.assertRaisesRegex(live.QualificationError, "billing evidence"):
            live.validate_receipt(receipt, receipt["qualificationId"], authorization)
        receipt["cost"]["estimatedAmountMicros"] = 999
        receipt["usage"]["inputTokens"] = 12_001
        with self.assertRaisesRegex(live.QualificationError, "internally inconsistent"):
            live.validate_receipt(receipt, receipt["qualificationId"], authorization)
        receipt["usage"] = {"available": False, "inputTokens": None, "outputTokens": None}
        receipt["cost"]["estimatedAmountMicros"] = None
        with self.assertRaisesRegex(live.QualificationError, "internally inconsistent"):
            live.validate_receipt(receipt, receipt["qualificationId"], authorization)
        receipt["status"] = "inconclusive"
        receipt["outcome"] = "provider-failed"
        with self.assertRaisesRegex(live.QualificationError, "interrupted exact approval"):
            live.validate_receipt(receipt, receipt["qualificationId"], authorization)

    def test_local_claim_validation_rejects_unknown_or_authorization_mismatched_state(self):
        authorization = self.authorization()
        authorization_hash = live.digest(authorization)
        claim = {
            "schemaVersion": 1,
            "qualificationId": "qualification-1786195551000-aabbccddeeff",
            "authorizationHash": authorization_hash,
            "authorizationExpiresAt": authorization["expiresAt"],
            "authMode": "chatgpt",
            "billingBoundary": "chatgpt-plan-or-credits",
            "jobId": "frontier-1786195551000-aabbccddeeff",
            "planHash": "a" * 64,
            "capsuleHash": "b" * 64,
            "policyHash": "c" * 64,
            "maxProviderCalls": 1,
            "maxInputTokens": 12000,
            "maxOutputTokens": 256,
            "maxEstimatedCostMicros": None,
            "preparedAt": live.iso(),
        }
        self.assertEqual(live.validate_local_claim(
            claim, authorization=authorization, authorization_hash=authorization_hash,
        ), claim)
        claim["jobId"] = "frontier-invalid"
        with self.assertRaisesRegex(live.QualificationError, "binding is invalid"):
            live.validate_local_claim(claim, authorization=authorization, authorization_hash=authorization_hash)


if __name__ == "__main__":
    unittest.main()
