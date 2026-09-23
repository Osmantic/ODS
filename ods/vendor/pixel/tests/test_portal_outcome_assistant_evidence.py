import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_assistant_evidence as assistant_evidence
from tests.test_control_server import AuditedRunner, control_module


PRIVATE_MESSAGE = "Summarize my private calendar without exposing it."
PRIVATE_PROVIDER_ID = "private-provider-event-42"
JOURNAL_BOUNDARY = (
    "Content-free append-only custody for one bounded external action. It proves local state transitions and "
    "retry suppression, not provider acceptance, semantic correctness, operator approval, or completion without "
    "a terminal provider-bound observation."
)


def journal_chain():
    common = {
        "schemaVersion": 1, "kind": "pixel-external-action-journal-event",
        "actionId": "calendar-1786690000000-a1b2c3d4", "connector": "google-calendar", "operation": "update",
        "proposalSha256": "1" * 64, "idempotencyKeySha256": "2" * 64,
        "idempotencyMode": "internal-nonreplay", "providerTargetSha256": "3" * 64,
        "attempt": 1, "boundary": JOURNAL_BOUNDARY,
    }
    specs = [
        ("proposed", "proposal-bound", None),
        ("submitting", "provider-submit-begun", None),
        ("succeeded", "provider-result-bound", "4" * 64),
    ]
    events = []
    previous = None
    for sequence, (state, reason, observation) in enumerate(specs):
        event = {
            **common, "sequence": sequence, "previousEventSha256": previous, "state": state,
            "recordedAt": f"2026-08-13T12:00:0{sequence}.000Z", "reasonCode": reason,
            "observationSha256": observation, "retryAllowed": state == "proposed",
        }
        payload = (json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        previous = hashlib.sha256(payload).hexdigest()
        events.append(event)
    return {"actionId": common["actionId"], "events": events, "headSha256": previous}


def model_proxy_receipts(*, model="assistant-model", input_tokens=120, output_tokens=30, tool_count=3):
    common = {
        "schemaVersion": 1, "jobId": "work-1786690000000-a1b2c3d4e5f6",
        "claimId": "workclaim-1786690000000-a1b2c3d4e5f6", "planSha256": "1" * 64,
        "qualificationId": "modelqual-1786689900000-a1b2c3d4e5f6",
        "qualificationReceiptSha256": "2" * 64, "inferencePolicySha256": "3" * 64,
        "modelIdSha256": hashlib.sha256(model.encode()).hexdigest(), "proxyConfigSha256": "4" * 64,
        "allowedToolsSha256": "5" * 64, "startedAt": "2026-08-13T12:00:00.000Z",
        "contentStored": False, "credentialsExposed": False, "arbitraryNetwork": False,
        "externalEffects": False,
    }
    initial = {
        **common, "modelRequests": 0, "inputTokens": 0, "outputTokens": 0, "networkBytes": 0,
        "deniedRequests": 0, "backendFailures": 0, "activeInference": False,
        "lastFailureCode": None, "lastRequestToolCount": None, "lastRequestToolsSha256": None,
    }
    final = {
        **common, "modelRequests": 1, "inputTokens": input_tokens, "outputTokens": output_tokens,
        "networkBytes": 4096, "deniedRequests": 0, "backendFailures": 0, "activeInference": False,
        "lastFailureCode": None, "lastRequestToolCount": tool_count, "lastRequestToolsSha256": "6" * 64,
    }
    return initial, final


class PortalOutcomeAssistantEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state = control_module.ControlState(
            ROOT, self.base / "state", self.base / "private" / "onboarding.json",
        )
        binary = self.base / "bin" / "openclaw"
        binary.parent.mkdir(mode=0o700)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o700)
        home = self.base / "openclaw-home"
        home.mkdir(mode=0o700)
        control_module.atomic_json(home / "openclaw.json", {"gateway": {"mode": "local"}}, 0o600)
        configured = control_module.merge_onboarding({}, control_module.default_onboarding())
        configured.update({"openclawBin": str(binary), "openclawHome": str(home), "agentId": "pixel"})
        control_module.atomic_json(self.state.onboarding_path, configured, 0o600)

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, runner, *, message=PRIVATE_MESSAGE, chains=None):
        self.state.runner = runner
        self.state.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "a" * 32,
            "conversationHandle": None, "message": message,
        })
        conversation = json.loads(next(self.state.chat_conversations.glob("conversation-*.json")).read_text(encoding="utf-8"))
        turn = conversation["turns"][0]
        proxy_initial, proxy_final = model_proxy_receipts(
            input_tokens=turn["modelReceipt"]["inputTokens"],
            output_tokens=turn["modelReceipt"]["outputTokens"],
        )
        bundle_path = self.state.chat_tool_receipts / f"{turn['turnId']}.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.exists() else None
        return {
            "schemaVersion": 1, "operation": "pixel-portal-assistant-private-evidence",
            "conversation": conversation, "conversationSha256": control_module.digest(conversation),
            "turnId": turn["turnId"], "toolReceiptBundle": bundle,
            "toolReceiptBundleSha256": None if bundle is None else control_module.digest(bundle),
            "actionJournalChains": chains or [], "handoffReceipt": None, "independentVerification": None,
            "modelProxyInitialReceipt": proxy_initial, "modelProxyFinalReceipt": proxy_final,
            "privacy": {
                "conversationTextExported": False, "toolNamesExported": False, "argumentsExported": False,
                "resultsExported": False, "pathsExported": False, "credentialsExported": False,
                "providerIdentifiersExported": False,
            },
            "boundary": assistant_evidence.ASSISTANT_EVIDENCE_BOUNDARY,
        }

    def emit(self, envelope, required, message=PRIVATE_MESSAGE, expected_model="assistant-model", source_sha256=None):
        output = self.base / f"run-{len(list(self.base.glob('run-*')))}"
        output.mkdir(mode=0o700)
        payload = message.encode()
        evidence = assistant_evidence.validate_and_emit(
            root=ROOT, run_dir=output, assistant_evidence=envelope, request_payload=payload,
            request_sha256=hashlib.sha256(payload).hexdigest(), required_evidence=required,
            expected_provider="local", expected_model=expected_model, source_sha256=source_sha256,
        )
        return output, evidence

    def test_read_only_portal_turn_emits_exact_content_free_source_and_route_evidence(self):
        observed = {
            "sourceKind": "calendar-projection", "observedAt": "2026-08-13T12:00:00.000Z",
            "sourceSha256": hashlib.sha256(b"result-0").hexdigest(), "stale": False,
            "privacyRoute": "owner-local-projection",
        }
        runner = AuditedRunner(["pixel_calendar_list"], [{
            "brokerKind": "local-projection", "status": "observed", "correlationIdSha256": None,
            "approvalRequired": False, "externalEffectOccurred": False, "autoWithinPolicy": True,
            "ambiguous": False, "observation": observed, "action": None,
        }], text="Private calendar summarized with its observation time.")
        envelope = self.envelope(runner)
        output, evidence = self.emit(envelope, {
            "exact-source", "source-provenance", "observation-time", "privacy-route", "typed-call",
        }, source_sha256="7" * 64)
        self.assertEqual({item["type"] for item in evidence}, {
            "exact-source", "source-provenance", "observation-time", "privacy-route", "typed-call",
        })
        encoded = "\n".join(path.read_text(encoding="utf-8") for path in (output / "evidence").iterdir())
        for secret in (PRIVATE_MESSAGE, "pixel_calendar_list", "Private calendar summarized"):
            self.assertNotIn(secret, encoded)
        observation = json.loads((output / "evidence" / "assistant-observation-time.json").read_text(encoding="utf-8"))
        self.assertFalse(observation["freshnessLabelsVerified"])

    def test_terminal_calendar_action_requires_and_binds_the_real_journal_chain(self):
        chain = journal_chain()
        action = {
            "providerIdentifierSha256": hashlib.sha256(PRIVATE_PROVIDER_ID.encode()).hexdigest(),
            "actionJournalHeadSha256": chain["headSha256"], "actionJournalState": "succeeded",
        }
        runner = AuditedRunner(["pixel_calendar_propose_update"], [{
            "brokerKind": "calendar-action", "status": "applied-bounded-direct", "correlationIdSha256": "5" * 64,
            "approvalRequired": False, "externalEffectOccurred": True, "autoWithinPolicy": True,
            "ambiguous": False, "observation": None, "action": action,
        }], text="The exact bounded action settled once.")
        envelope = self.envelope(runner, chains=[chain])
        output, evidence = self.emit(envelope, {"typed-call", "provider-identifier", "action-journal"})
        self.assertEqual({item["type"] for item in evidence}, {"typed-call", "provider-identifier", "action-journal"})
        encoded = "\n".join(path.read_text(encoding="utf-8") for path in (output / "evidence").iterdir())
        self.assertNotIn(PRIVATE_PROVIDER_ID, encoded)
        journal = json.loads((output / "evidence" / "assistant-action-journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["actions"][0]["actionJournalHeadSha256"], chain["headSha256"])
        self.assertFalse(journal["retryAuthorized"])

        tampered = json.loads(json.dumps(envelope))
        tampered["actionJournalChains"][0]["events"][1]["reasonCode"] = "substituted"
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "journal chain"):
            self.emit(tampered, {"provider-identifier", "action-journal"})

    def test_request_drift_and_unexpected_journal_fail_closed(self):
        runner = AuditedRunner([], [], text="No tools needed.")
        envelope = self.envelope(runner)
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "model identity"):
            self.emit(envelope, {"exact-source"}, expected_model="DeepSeek-V4-Flash-0731", source_sha256="7" * 64)
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "exact admitted request"):
            self.emit(envelope, {"exact-source"}, message="different request", source_sha256="7" * 64)
        substituted = json.loads(json.dumps(envelope))
        substituted["modelProxyFinalReceipt"]["inputTokens"] += 1
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "accounting differs"):
            self.emit(substituted, {"exact-source"}, source_sha256="7" * 64)
        reused = json.loads(json.dumps(envelope))
        reused["modelProxyInitialReceipt"]["modelRequests"] = 1
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "not fresh"):
            self.emit(reused, {"exact-source"}, source_sha256="7" * 64)
        envelope["actionJournalChains"] = [journal_chain()]
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "unexpected action journal"):
            self.emit(envelope, {"exact-source"}, source_sha256="7" * 64)

    def test_checkpoint_and_independent_verification_bind_real_private_lineage_without_exporting_text(self):
        runner = AuditedRunner([], [], text="Verified without tools.")
        envelope = self.envelope(runner)
        source_sha = "7" * 64
        envelope["independentVerification"] = {
            "schemaVersion": 1, "format": "pixel-neutral-independent-verification-v1",
            "sourceSnapshotSha256": source_sha, "candidateSha256": "8" * 64, "status": "pass",
            "checks": [{"id": "patch", "kind": "patch-integrity", "status": "pass"}],
            "criteria": [{"index": 0, "status": "pass", "checkIds": ["patch"]}],
            "network": "none", "workerSelectedChecks": False, "externalEffects": False,
            "immutablePathViolation": False, "aggregateRuntimeMilliseconds": 0,
            "aggregateOutputBytes": 0, "aggregateWithinBounds": True, "cleanupVerified": True,
            "boundary": assistant_evidence.INDEPENDENT_BOUNDARY,
        }
        output, evidence = self.emit(
            envelope, {"exact-source", "checkpoint-lineage", "independent-verifier"},
            source_sha256=source_sha,
        )
        self.assertEqual({item["type"] for item in evidence}, {
            "exact-source", "checkpoint-lineage", "independent-verifier",
        })
        lineage = json.loads((output / "evidence" / "assistant-checkpoint-lineage.json").read_text(encoding="utf-8"))
        self.assertTrue(lineage["lineageValidated"])
        encoded = "\n".join(path.read_text(encoding="utf-8") for path in (output / "evidence").iterdir())
        self.assertNotIn(PRIVATE_MESSAGE, encoded)
        tampered = json.loads(json.dumps(envelope)); tampered["independentVerification"]["network"] = "bridge"
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "invalid or exceeds"):
            self.emit(tampered, {"independent-verifier"}, source_sha256=source_sha)


if __name__ == "__main__":
    unittest.main()
