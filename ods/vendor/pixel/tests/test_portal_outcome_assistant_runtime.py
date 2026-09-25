import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_assistant_evidence as assistant_evidence
import portal_outcome_assistant_runtime as assistant_runtime
from tests.test_control_server import AuditedRunner, control_module
from tests.test_portal_outcome_assistant_evidence import journal_chain, model_proxy_receipts


MESSAGE = "Inspect the exact private projection and report only verified state."


class PortalOutcomeAssistantRuntimeTests(unittest.TestCase):
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

    def run_turn(self, runner, message=MESSAGE):
        self.state.runner = runner
        self.state.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + hashlib.sha256(message.encode()).hexdigest()[:32],
            "conversationHandle": None, "message": message,
        })

    @staticmethod
    def observation():
        return {
            "sourceKind": "operations-inventory", "observedAt": "2026-08-13T12:10:00.000Z",
            "sourceSha256": hashlib.sha256(b"result-0").hexdigest(), "stale": False,
            "privacyRoute": "typed-operations-broker",
        }

    def test_collects_the_real_control_turn_and_emits_no_private_content(self):
        runner = AuditedRunner(["pixel_ops_inventory"], [{
            "brokerKind": "operations-broker", "status": "observed", "correlationIdSha256": None,
            "approvalRequired": False, "externalEffectOccurred": False, "autoWithinPolicy": True,
            "ambiguous": False, "observation": self.observation(), "action": None,
        }], text="The managed inventory was observed through its typed adapter.")
        self.run_turn(runner)
        payload = MESSAGE.encode()
        proxy_initial, proxy_final = model_proxy_receipts()
        envelope = assistant_runtime.collect_private_turn(
            control_state=self.state, request_payload=payload,
            request_sha256=hashlib.sha256(payload).hexdigest(),
            model_proxy_initial_receipt=proxy_initial, model_proxy_final_receipt=proxy_final,
        )
        self.assertEqual(envelope["conversation"]["turns"][0]["userText"], MESSAGE)
        output = self.base / "output"
        output.mkdir(mode=0o700)
        emitted = assistant_evidence.validate_and_emit(
            root=ROOT, run_dir=output, assistant_evidence=envelope, request_payload=payload,
            request_sha256=hashlib.sha256(payload).hexdigest(),
            required_evidence={"source-provenance", "observation-time", "privacy-route", "typed-call"},
            expected_provider="local", expected_model="assistant-model",
        )
        self.assertEqual({item["type"] for item in emitted}, {
            "source-provenance", "observation-time", "privacy-route", "typed-call",
        })
        exported = "\n".join(path.read_text(encoding="utf-8") for path in (output / "evidence").iterdir())
        for private in (MESSAGE, "pixel_ops_inventory", "managed inventory"):
            self.assertNotIn(private, exported)

    def write_journal(self, chain):
        root = self.base / "action-journals"
        root.mkdir(mode=0o700)
        action = root / chain["actionId"]
        action.mkdir(mode=0o700)
        lock = action / ".lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        for event in chain["events"]:
            payload = (json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
            event_path = action / f"{event['sequence']:06d}.json"
            event_path.write_bytes(payload)
            event_path.chmod(0o600)
        head = {"eventSha256": chain["headSha256"], "sequence": len(chain["events"]) - 1}
        head_path = action / ".head"
        head_path.write_bytes(
            (json.dumps(head, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        )
        head_path.chmod(0o600)
        return root

    def test_collects_only_the_action_chain_bound_to_the_trusted_tool_result(self):
        chain = journal_chain()
        action = {
            "providerIdentifierSha256": "6" * 64,
            "actionJournalHeadSha256": chain["headSha256"], "actionJournalState": "succeeded",
        }
        runner = AuditedRunner(["pixel_calendar_propose_update"], [{
            "brokerKind": "calendar-action", "status": "applied-bounded-direct",
            "correlationIdSha256": "7" * 64, "approvalRequired": False,
            "externalEffectOccurred": True, "autoWithinPolicy": True, "ambiguous": False,
            "observation": None, "action": action,
        }], text="The bounded update settled exactly once.")
        self.run_turn(runner)
        root = self.write_journal(chain)
        payload = MESSAGE.encode()
        proxy_initial, proxy_final = model_proxy_receipts()
        envelope = assistant_runtime.collect_private_turn(
            control_state=self.state, request_payload=payload,
            request_sha256=hashlib.sha256(payload).hexdigest(), action_journal_roots=[root],
            model_proxy_initial_receipt=proxy_initial, model_proxy_final_receipt=proxy_final,
        )
        self.assertEqual([item["headSha256"] for item in envelope["actionJournalChains"]], [chain["headSha256"]])

        (root / chain["actionId"] / ".head").write_text(
            json.dumps({"eventSha256": "8" * 64, "sequence": 2}, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(assistant_runtime.evaluation.OutcomeError, "durable head"):
            assistant_runtime.collect_private_turn(
                control_state=self.state, request_payload=payload,
                request_sha256=hashlib.sha256(payload).hexdigest(), action_journal_roots=[root],
                model_proxy_initial_receipt=proxy_initial, model_proxy_final_receipt=proxy_final,
            )

    def test_request_drift_and_missing_action_custody_fail_closed(self):
        self.run_turn(AuditedRunner([], [], text="No tool was needed."))
        proxy_initial, proxy_final = model_proxy_receipts()
        with self.assertRaisesRegex(assistant_runtime.evaluation.OutcomeError, "request binding"):
            assistant_runtime.collect_private_turn(
                control_state=self.state, request_payload=MESSAGE.encode(), request_sha256="0" * 64,
                model_proxy_initial_receipt=proxy_initial, model_proxy_final_receipt=proxy_final,
            )

        other = "A different request."
        with self.assertRaisesRegex(assistant_runtime.evaluation.OutcomeError, "does not select"):
            assistant_runtime.collect_private_turn(
                control_state=self.state, request_payload=other.encode(),
                request_sha256=hashlib.sha256(other.encode()).hexdigest(),
                model_proxy_initial_receipt=proxy_initial, model_proxy_final_receipt=proxy_final,
            )


if __name__ == "__main__":
    unittest.main()
