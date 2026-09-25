import http.client
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pixel_control_server", ROOT / "control/server.py")
control_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(control_module)


class RecordingRunner:
    def __init__(self, returncode=0, output=b"safe local output"):
        self.returncode = returncode
        self.output = output
        self.calls = []

    def __call__(self, command, root, timeout_seconds, environment):
        self.calls.append((command, root, timeout_seconds, environment))
        return self.returncode, self.output


def chat_meta(tool_summary=None, *, provider="local", model="assistant-model", **extra):
    meta = {
        "durationMs": 250,
        "agentMeta": {
            "sessionId": "fixture-session", "provider": provider, "model": model,
            "usage": {"input": 120, "output": 30, "cacheRead": 10, "cacheWrite": 0, "total": 150},
            "promptTokens": 100,
        },
        **extra,
    }
    if tool_summary is not None:
        meta["toolSummary"] = tool_summary
    return meta


def portal_tool_receipt(session_key, tool_name, offset, evidence, *, state="succeeded", classification=None, isolation=None):
    evidence = {"observation": None, "action": None, **evidence}
    receipt_id = f"tool-{1786690000000 + offset:013d}-{offset + 1:024x}"
    tool_call_id = f"call-{offset + 1}"
    classified = control_module._chat_tool_class(tool_name)
    classification = classification or {
        "capability": classified[0], "route": classified[1], "effect": classified[2],
        "brokerReceiptRequired": classified[3], "autoEligible": classified[4],
    }
    common = {
        "schemaVersion": 1, "operation": "pixel-portal-tool-receipt", "receiptId": receipt_id,
        "sessionKeySha256": hashlib.sha256(session_key.encode()).hexdigest(), "sessionIdSha256": None,
        "toolCallIdSha256": hashlib.sha256(tool_call_id.encode()).hexdigest(),
        "toolNameSha256": hashlib.sha256(tool_name.encode()).hexdigest(),
        "classification": classification, "isolation": isolation or {"sandboxed": True, "workspaceOnly": True},
        "startedAt": f"2026-08-13T12:00:{offset:02d}+00:00",
        "privacy": {
            "sessionKeyExposed": False, "toolNameExposed": False, "callIdExposed": False,
            "argumentsExposed": False, "resultExposed": False, "pathsExposed": False,
            "promptsExposed": False, "credentialsExposed": False, "providerIdentifiersExposed": False,
        },
        "authority": {
            "grantsExecution": False, "grantsProviderCall": False, "grantsCredentialUse": False,
            "grantsApproval": False, "grantsExternalEffect": False, "grantsCompletion": False,
            "grantsRetry": False, "grantsPolicyMutation": False,
        },
        "boundary": control_module.CHAT_TOOL_AUDIT_BOUNDARY,
    }
    started = {**common, "state": "started", "finishedAt": None, "startedReceiptSha256": None, "outcome": None}
    if state == "started":
        return started
    return {
        **common, "state": state, "finishedAt": f"2026-08-13T12:01:{offset:02d}+00:00",
        "startedReceiptSha256": control_module.digest(started),
        "outcome": {
            "resultSha256": hashlib.sha256(f"result-{offset}".encode()).hexdigest() if state == "succeeded" else None,
            "errorSha256": None if state == "succeeded" else hashlib.sha256(f"error-{offset}".encode()).hexdigest(),
            "evidence": evidence,
        },
    }


class AuditedRunner(RecordingRunner):
    def __init__(self, tools, evidence, *, receipt_tools=None, receipt_overrides=None, text="Broker work completed."):
        output = json.dumps({
            "status": "ok", "result": {
                "payloads": [{"text": text}],
                "meta": chat_meta({"calls": len(tools), "failures": 0, "tools": tools}),
            },
        }).encode()
        super().__init__(output=output)
        self.tools = receipt_tools if receipt_tools is not None else tools
        self.evidence = evidence
        self.receipt_overrides = receipt_overrides or {}

    def __call__(self, command, root, timeout_seconds, environment):
        self.calls.append((command, root, timeout_seconds, environment))
        session_key = command[command.index("--session-key") + 1]
        directory = Path(environment["PIXEL_CHAT_AUDIT_DIR"]) / hashlib.sha256(session_key.encode()).hexdigest()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for offset, tool_name in enumerate(self.tools):
            options = self.receipt_overrides.get(offset, {})
            receipt = portal_tool_receipt(session_key, tool_name, offset, self.evidence[offset], **options)
            control_module.atomic_json(directory / f"{receipt['receiptId']}.json", receipt, 0o600, replace=False)
        return self.returncode, self.output


class ControlServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state_path = self.base / "state"
        self.onboarding_path = self.base / "private" / "onboarding.json"
        self.runner = RecordingRunner()
        self.control = control_module.ControlState(
            ROOT, self.state_path, self.onboarding_path, runner=self.runner,
        )

    def tearDown(self):
        self.temp.cleanup()

    def save_default(self):
        current = self.control.onboarding()
        return self.control.save_onboarding({
            "schemaVersion": 1,
            "revision": current["revision"],
            "settings": current["settings"],
        })

    def configure_chat_runtime(self, runner=None):
        binary = self.base / "bin" / "openclaw"
        binary.parent.mkdir(mode=0o700)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o700)
        home = self.base / "openclaw-home"
        home.mkdir(mode=0o700)
        control_module.atomic_json(home / "openclaw.json", {"gateway": {"mode": "local"}}, 0o600)
        full = control_module.merge_onboarding({}, control_module.default_onboarding())
        full.update({"openclawBin": str(binary), "openclawHome": str(home), "agentId": "pixel"})
        control_module.atomic_json(self.onboarding_path, full, 0o600)
        if runner is not None:
            self.control.runner = runner
        return binary, home

    def test_private_chat_invokes_only_the_fixed_agent_and_projects_bounded_text(self):
        output = json.dumps({
            "status": "ok",
            "result": {
                "payloads": [{"text": "A real local response."}],
                "meta": chat_meta(
                    {"calls": 2, "failures": 0, "tools": ["private-tool-name"]},
                    privatePath="/private/session/path.jsonl",
                ),
            },
        }).encode()
        runner = RecordingRunner(output=output)
        binary, home = self.configure_chat_runtime(runner)
        initial = self.control.chat()
        self.assertEqual(initial["state"], "ready")
        self.assertEqual(initial["conversations"], [])
        request = {
            "schemaVersion": 1, "requestId": "chatreq-" + "1" * 32,
            "conversationHandle": None, "message": "Help with a private local task.",
        }
        result = self.control.chat_turn(request)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(len(result["conversations"]), 1)
        conversation = result["conversations"][0]
        self.assertEqual(result["activeHandle"], conversation["handle"])
        self.assertEqual(conversation["turns"][0]["userText"], request["message"])
        self.assertEqual(conversation["turns"][0]["assistantText"], "A real local response.")
        self.assertEqual(conversation["turns"][0]["toolCalls"], 2)
        self.assertEqual(conversation["turns"][0]["capability"], {
            "state": "unclassified", "routes": ["unknown"], "effects": ["unknown"],
            "autonomousWithinPolicy": False, "brokerReceiptRequired": True,
            "brokerReceiptSatisfied": False, "approvalRequired": False,
            "externalEffectOccurred": None, "ambiguousBrokerCalls": 0,
            "unclassifiedReportedTools": 1, "toolNamesExposed": False,
        })
        encoded = json.dumps(result)
        self.assertNotIn("private-tool-name", encoded)
        self.assertNotIn("/private/session", encoded)
        private_turn = self.control._chat_records()[0]["value"]["turns"][0]
        self.assertEqual(private_turn["modelReceipt"]["modelIdSha256"], hashlib.sha256(b"assistant-model").hexdigest())
        self.assertEqual(private_turn["modelReceipt"]["providerIdSha256"], hashlib.sha256(b"local").hexdigest())
        self.assertEqual(private_turn["modelReceipt"]["inputTokens"], 120)
        self.assertEqual(private_turn["modelReceipt"]["outputTokens"], 30)
        self.assertIsNone(private_turn["modelReceipt"]["modelRequests"])
        command, _root, timeout, environment = runner.calls[0]
        self.assertEqual(command[:5], [str(binary.resolve()), "agent", "--agent", "pixel", "--session-key"])
        self.assertIn("--message-file", command)
        self.assertNotIn(request["message"], command)
        self.assertEqual(timeout, 660)
        self.assertEqual(environment["OPENCLAW_STATE_DIR"], str(home))
        self.assertEqual(environment["OPENCLAW_CONFIG_PATH"], str(home / "openclaw.json"))
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertEqual(list(self.control.chat_inputs.iterdir()), [])
        replay = self.control.chat_turn(request)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(replay["activeHandle"], conversation["handle"])
        with self.assertRaisesRegex(control_module.PublicRejected, "reused with different content"):
            self.control.chat_turn({**request, "message": "Substitute a different task under the same request identity."})
        self.assertEqual(len(runner.calls), 1)

        followup = {
            "schemaVersion": 1, "requestId": "chatreq-" + "2" * 32,
            "conversationHandle": conversation["handle"], "message": "Continue from that exact context.",
        }
        continued = self.control.chat_turn(followup)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(len(continued["conversations"][0]["turns"]), 2)
        self.assertEqual(runner.calls[1][0][runner.calls[1][0].index("--session-key") + 1], runner.calls[0][0][runner.calls[0][0].index("--session-key") + 1])

    def test_private_chat_inherits_only_validated_credential_free_broker_routing(self):
        self.configure_chat_runtime()
        runner = RecordingRunner(output=json.dumps({
            "status": "ok", "result": {
                "payloads": [{"text": "Read the configured local projection."}],
                "meta": chat_meta({"calls": 0, "failures": 0, "tools": []}),
            },
        }).encode())
        routing = {
            "PIXEL_SOURCE_PROJECTION_DIR": str(self.base / "source-projection"),
            "PIXEL_ACTION_PROPOSAL_DIR": str(self.base / "source-proposals"),
            "PIXEL_LIMB_CALENDAR_ENABLED": "1",
            "PIXEL_SOURCE_STALE_AFTER_MS": "3600000",
            "SEARXNG_BASE_URL": "http://127.0.0.1:8890",
        }
        state = control_module.ControlState(
            ROOT, self.base / "routed-state", self.onboarding_path,
            runner=runner, chat_environment=routing,
        )
        state.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "a" * 32,
            "conversationHandle": None, "message": "Use the configured local source projection.",
        })
        environment = runner.calls[0][3]
        self.assertEqual({key: environment[key] for key in routing}, routing)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("ANTHROPIC_API_KEY", environment)

        inherited = control_module.chat_environment_from_process({
            **routing, "OPENAI_API_KEY": "must-not-cross", "UNRELATED_HOST_VALUE": "must-not-cross",
        })
        self.assertEqual(inherited, routing)
        with self.assertRaisesRegex(control_module.Rejected, "unsupported variable"):
            control_module.validate_chat_environment({"OPENAI_API_KEY": "must-not-cross"})
        with self.assertRaisesRegex(control_module.Rejected, "broker path"):
            control_module.validate_chat_environment({"PIXEL_SOURCE_PROJECTION_DIR": "relative/path"})
        with self.assertRaisesRegex(control_module.Rejected, "feature flag"):
            control_module.validate_chat_environment({"PIXEL_LIMB_CALENDAR_ENABLED": "yes"})
        with self.assertRaisesRegex(control_module.Rejected, "search origin"):
            control_module.validate_chat_environment({"SEARXNG_BASE_URL": "https://owner:secret@example.com"})

    def test_private_chat_distinguishes_broad_local_autonomy_from_consequential_broker_correlation(self):
        local_output = json.dumps({
            "status": "ok", "result": {
                "payloads": [{"text": "Built and verified locally."}],
                "meta": chat_meta({"calls": 8, "failures": 0, "tools": ["read", "search", "edit", "exec", "lsp"]}),
            },
        }).encode()
        runner = RecordingRunner(output=local_output)
        self.configure_chat_runtime(runner)
        local = self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "d" * 32,
            "conversationHandle": None, "message": "Inspect, patch, and test this local workspace.",
        })
        turn = local["conversations"][0]["turns"][0]
        self.assertEqual(turn["capability"]["state"], "complete-local")
        self.assertTrue(turn["capability"]["autonomousWithinPolicy"])
        self.assertFalse(turn["capability"]["brokerReceiptRequired"])
        self.assertEqual(turn["capability"]["routes"], ["local-only"])
        self.assertEqual(turn["capability"]["effects"], ["bounded-local-state-change", "read-only"])
        private_record = json.loads(next(self.control.chat_conversations.glob("conversation-*.json")).read_text(encoding="utf-8"))
        receipt = private_record["turns"][0]["capabilityReceipt"]
        self.assertEqual(receipt["evidenceState"], "complete-local")
        self.assertEqual(receipt["reportedToolNames"], ["edit", "exec", "lsp", "read", "search"])
        self.assertTrue(all(
            all(control_module.HASH_RE.fullmatch(item) for item in capability["toolNameSha256s"])
            for capability in receipt["classes"]
        ))

        broker_output = json.dumps({
            "status": "ok", "result": {
                "payloads": [{"text": "Prepared the exact calendar change for its broker boundary."}],
                "meta": chat_meta({"calls": 2, "failures": 0, "tools": ["pixel_calendar_get", "pixel_calendar_propose_update"]}),
            },
        }).encode()
        runner.output = broker_output
        continued = self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "e" * 32,
            "conversationHandle": local["activeHandle"], "message": "Update that event exactly as discussed.",
        })
        broker = continued["conversations"][0]["turns"][1]["capability"]
        self.assertEqual(broker["state"], "broker-correlation-required")
        self.assertFalse(broker["autonomousWithinPolicy"])
        self.assertTrue(broker["brokerReceiptRequired"])
        self.assertEqual(broker["routes"], ["authorized-provider", "owner-local-projection"])
        self.assertEqual(broker["effects"], ["external-write-capable", "read-only"])
        self.assertNotIn("pixel_calendar_propose_update", json.dumps(continued))

    def test_private_chat_policy_keeps_safe_research_and_status_tools_automatic(self):
        for name in (
            "read", "edit", "exec", "lsp", "debug", "web_search",
            "pixel_gmail_search", "pixel_ops_inventory", "pixel_ops_job_wait",
            "pixel_frontier_job_get", "pixel_frontier_usage", "pixel_frontier_finalize",
        ):
            with self.subTest(name=name):
                classification = control_module._chat_tool_class(name)
                self.assertFalse(classification[3])
                self.assertTrue(classification[4])
        for name in (
            "web_fetch", "browser", "pixel_calendar_propose_delete",
            "pixel_ops_run", "pixel_frontier_plan_review",
        ):
            with self.subTest(name=name):
                classification = control_module._chat_tool_class(name)
                self.assertTrue(classification[3])
                self.assertFalse(classification[4])

    def test_private_chat_proves_bounded_broker_effects_without_false_approval(self):
        read_evidence = {
            "brokerKind": "local-projection", "status": "observed", "correlationIdSha256": None,
            "approvalRequired": False, "externalEffectOccurred": False, "autoWithinPolicy": True,
            "ambiguous": False,
        }
        direct_evidence = {
            "brokerKind": "calendar-action", "status": "applied-bounded-direct",
            "correlationIdSha256": "a" * 64, "approvalRequired": False,
            "externalEffectOccurred": True, "autoWithinPolicy": True, "ambiguous": False,
        }
        runner = AuditedRunner(
            ["pixel_calendar_get", "pixel_calendar_propose_update"], [read_evidence, direct_evidence],
            text="The bounded reschedule was applied and verified.",
        )
        _binary, home = self.configure_chat_runtime(runner)
        result = self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "a" * 32,
            "conversationHandle": None, "message": "Move my private event by thirty minutes.",
        })
        turn = result["conversations"][0]["turns"][0]
        self.assertEqual(turn["state"], "succeeded")
        self.assertEqual(turn["capability"]["state"], "broker-correlated")
        self.assertTrue(turn["capability"]["brokerReceiptSatisfied"])
        self.assertTrue(turn["capability"]["autonomousWithinPolicy"])
        self.assertTrue(turn["capability"]["externalEffectOccurred"])
        self.assertFalse(turn["capability"]["approvalRequired"])
        bundle_path = next(self.control.chat_tool_receipts.glob("turn-*.json"))
        bundle_text = bundle_path.read_text(encoding="utf-8")
        self.assertNotIn("pixel_calendar", bundle_text)
        self.assertNotIn("Move my private event", bundle_text)
        self.assertEqual(list((home / "pixel-chat-audit").rglob("tool-*.json")), [])
        tampered = json.loads(bundle_text)
        tampered["receipts"][0]["outcome"]["resultSha256"] = "c" * 64
        control_module.atomic_json(bundle_path, tampered, 0o600)
        self.assertEqual(self.control.chat()["state"], "unavailable")

    def test_private_chat_surfaces_exact_broker_approval_inline_without_claiming_effect(self):
        pending = {
            "brokerKind": "calendar-proposal", "status": "approval-required",
            "correlationIdSha256": "b" * 64, "approvalRequired": True,
            "externalEffectOccurred": False, "autoWithinPolicy": False, "ambiguous": False,
        }
        runner = AuditedRunner(["pixel_calendar_propose_create"], [pending])
        self.configure_chat_runtime(runner)
        result = self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "b" * 32,
            "conversationHandle": None, "message": "Create an event that invites the client.",
        })
        capability = result["conversations"][0]["turns"][0]["capability"]
        self.assertEqual(capability["state"], "broker-approval-required")
        self.assertTrue(capability["brokerReceiptSatisfied"])
        self.assertTrue(capability["approvalRequired"])
        self.assertFalse(capability["externalEffectOccurred"])
        self.assertFalse(capability["autonomousWithinPolicy"])

    def test_private_chat_read_only_job_check_surfaces_underlying_approval(self):
        pending = {
            "brokerKind": "operations-state", "status": "approval-required",
            "correlationIdSha256": "e" * 64, "approvalRequired": True,
            "externalEffectOccurred": False, "autoWithinPolicy": False, "ambiguous": False,
        }
        runner = AuditedRunner(["pixel_ops_job_get"], [pending])
        self.configure_chat_runtime(runner)
        result = self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "e" * 32,
            "conversationHandle": None, "message": "Check the Operations job and tell me what it needs.",
        })
        capability = result["conversations"][0]["turns"][0]["capability"]
        self.assertEqual(capability["state"], "broker-approval-required")
        self.assertTrue(capability["approvalRequired"])
        self.assertFalse(capability["autonomousWithinPolicy"])
        self.assertTrue(capability["brokerReceiptSatisfied"])

    def test_private_chat_retains_ambiguous_broker_start_and_never_auto_retries_it(self):
        ambiguous = {
            "brokerKind": "calendar-action", "status": "unknown-ambiguous",
            "correlationIdSha256": None, "approvalRequired": False,
            "externalEffectOccurred": None, "autoWithinPolicy": False, "ambiguous": True,
        }
        runner = AuditedRunner(
            ["pixel_calendar_propose_update"], [ambiguous], receipt_overrides={0: {"state": "started"}},
        )
        _binary, home = self.configure_chat_runtime(runner)
        request = {
            "schemaVersion": 1, "requestId": "chatreq-" + "c" * 32,
            "conversationHandle": None, "message": "Apply the bounded event change once.",
        }
        result = self.control.chat_turn(request)
        capability = result["conversations"][0]["turns"][0]["capability"]
        self.assertEqual(capability["state"], "broker-ambiguous")
        self.assertEqual(capability["ambiguousBrokerCalls"], 1)
        self.assertFalse(capability["autonomousWithinPolicy"])
        self.assertEqual(len(list((home / "pixel-chat-audit").rglob("tool-*.json"))), 1)
        replay = self.control.chat_turn(request)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(replay["conversations"][0]["turns"][0]["capability"]["state"], "broker-ambiguous")

    def test_private_chat_rejects_receipt_classification_drift_and_extra_receipts(self):
        pending = {
            "brokerKind": "calendar-proposal", "status": "approval-required",
            "correlationIdSha256": "d" * 64, "approvalRequired": True,
            "externalEffectOccurred": False, "autoWithinPolicy": False, "ambiguous": False,
        }
        wrong = {
            "capability": "source-read", "route": "owner-local-projection", "effect": "read-only",
            "brokerReceiptRequired": False, "autoEligible": True,
        }
        variants = [
            AuditedRunner(["pixel_calendar_propose_create"], [pending], receipt_overrides={0: {"classification": wrong}}),
            AuditedRunner(
                ["pixel_calendar_propose_create"], [pending, pending],
                receipt_tools=["pixel_calendar_propose_create", "pixel_calendar_propose_update"],
            ),
        ]
        for index, runner in enumerate(variants):
            with self.subTest(index=index):
                state = tempfile.TemporaryDirectory()
                try:
                    base = Path(state.name)
                    control = control_module.ControlState(ROOT, base / "state", base / "private" / "onboarding.json")
                    binary = base / "bin" / "openclaw"
                    binary.parent.mkdir(mode=0o700)
                    binary.write_text("#!/bin/sh\n", encoding="utf-8")
                    binary.chmod(0o700)
                    home = base / "openclaw-home"
                    home.mkdir(mode=0o700)
                    control_module.atomic_json(home / "openclaw.json", {"gateway": {"mode": "local"}}, 0o600)
                    configured = control_module.merge_onboarding({}, control_module.default_onboarding())
                    configured.update({"openclawBin": str(binary), "openclawHome": str(home), "agentId": "pixel"})
                    control_module.atomic_json(control.onboarding_path, configured, 0o600)
                    control.runner = runner
                    result = control.chat_turn({
                        "schemaVersion": 1, "requestId": "chatreq-" + str(index + 1) * 32,
                        "conversationHandle": None, "message": "Reject mismatched broker evidence.",
                    })
                    self.assertEqual(result["conversations"][0]["turns"][0]["state"], "failed")
                    self.assertIsNone(result["conversations"][0]["turns"][0]["capability"])
                finally:
                    state.cleanup()

    def test_private_chat_refuses_impossible_or_malformed_tool_accounting(self):
        for summary in (
            {"calls": 1, "failures": 0, "tools": ["read", "search"]},
            {"calls": 1, "failures": 0, "tools": ["read", "read"]},
            {"calls": 1, "failures": 0, "tools": ["bad tool name"]},
        ):
            with self.subTest(summary=summary):
                state = tempfile.TemporaryDirectory()
                try:
                    base = Path(state.name)
                    control = control_module.ControlState(ROOT, base / "state", base / "private" / "onboarding.json")
                    binary = base / "bin" / "openclaw"
                    binary.parent.mkdir(mode=0o700)
                    binary.write_text("#!/bin/sh\n", encoding="utf-8")
                    binary.chmod(0o700)
                    home = base / "openclaw-home"
                    home.mkdir(mode=0o700)
                    control_module.atomic_json(home / "openclaw.json", {"gateway": {"mode": "local"}}, 0o600)
                    configured = control_module.merge_onboarding({}, control_module.default_onboarding())
                    configured.update({"openclawBin": str(binary), "openclawHome": str(home), "agentId": "pixel"})
                    control_module.atomic_json(control.onboarding_path, configured, 0o600)
                    control.runner = RecordingRunner(output=json.dumps({
                        "status": "ok", "result": {"payloads": [{"text": "Do not accept this receipt."}], "meta": chat_meta(summary)},
                    }).encode())
                    result = control.chat_turn({
                        "schemaVersion": 1, "requestId": "chatreq-" + "f" * 32,
                        "conversationHandle": None, "message": "Exercise malformed accounting.",
                    })
                    self.assertEqual(result["conversations"][0]["turns"][0]["state"], "failed")
                    self.assertIsNone(result["conversations"][0]["turns"][0]["capability"])
                finally:
                    state.cleanup()

    def test_private_chat_fails_closed_for_invalid_output_and_tampered_custody(self):
        runner = RecordingRunner(output=b'{"status":"ok","result":{"payloads":[]}}')
        self.configure_chat_runtime(runner)
        result = self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "3" * 32,
            "conversationHandle": None, "message": "Do not claim this succeeded.",
        })
        turn = result["conversations"][0]["turns"][0]
        self.assertEqual(turn["state"], "failed")
        self.assertIsNone(turn["assistantText"])
        self.assertIsNone(turn["toolCalls"])
        record_path = next(self.control.chat_conversations.glob("conversation-*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["turns"][0]["userText"] = "substituted private task"
        control_module.atomic_json(record_path, record, 0o600)
        unavailable = self.control.chat()
        self.assertEqual(unavailable["state"], "unavailable")
        self.assertEqual(unavailable["conversations"], [])

    def test_private_chat_fails_closed_for_model_substitution_or_missing_exact_usage(self):
        runner = RecordingRunner()
        self.configure_chat_runtime(runner)
        cases = (
            (chat_meta(provider="substituted-provider"), "provider substitution"),
            (chat_meta(model="substituted-model"), "model substitution"),
            ({"durationMs": 250, "agentMeta": {"provider": "local", "model": "assistant-model"}}, "missing usage"),
            (chat_meta(), "valid control"),
        )
        for index, (meta, label) in enumerate(cases):
            runner.output = json.dumps({
                "status": "ok", "result": {"payloads": [{"text": "Model-bound response."}], "meta": meta},
            }).encode()
            result = self.control.chat_turn({
                "schemaVersion": 1, "requestId": "chatreq-" + f"{index + 7:x}" * 32,
                "conversationHandle": None, "message": f"Exercise {label}.",
            })
            turn = result["conversations"][0]["turns"][0]
            if label == "valid control":
                self.assertEqual(turn["state"], "succeeded")
                self.assertEqual(turn["assistantText"], "Model-bound response.")
            else:
                self.assertEqual(turn["state"], "failed")
                self.assertIsNone(turn["assistantText"])

    def test_private_chat_rejects_rehashed_but_contradictory_task_activity(self):
        output = json.dumps({"status": "ok", "result": {"payloads": [{"text": "Verified once."}], "meta": chat_meta()}}).encode()
        self.configure_chat_runtime(RecordingRunner(output=output))
        self.control.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "c" * 32,
            "conversationHandle": None, "message": "Create exact task activity.",
        })
        record_path = next(self.control.chat_conversations.glob("conversation-*.json"))
        original = json.loads(record_path.read_text(encoding="utf-8"))
        conversation_id = original["conversationId"]
        variants = []
        wrong_terminal = copy.deepcopy(original)
        wrong_terminal["turns"][0]["activity"][-1]["code"] = "recovery-required"
        variants.append(wrong_terminal)
        reordered = copy.deepcopy(original)
        reordered["turns"][0]["activity"] = list(reversed(reordered["turns"][0]["activity"]))
        variants.append(reordered)
        leaked = copy.deepcopy(original)
        leaked["turns"][0]["activity"][0]["detail"] = "/private/tool/arguments"
        variants.append(leaked)
        weakened_capability = copy.deepcopy(original)
        weakened_capability["turns"][0]["capabilityReceipt"]["autonomy"]["eligibleWithoutApproval"] = False
        variants.append(weakened_capability)
        invalid_model_usage = copy.deepcopy(original)
        invalid_model_usage["turns"][0]["modelReceipt"]["inputTokens"] = -1
        variants.append(invalid_model_usage)
        for candidate in variants:
            turn = candidate["turns"][0]
            turn["recordSha256"] = control_module.digest({key: value for key, value in turn.items() if key != "recordSha256"})
            with self.subTest(activity=turn["activity"]):
                with self.assertRaisesRegex(control_module.Rejected, r"(task .*activity|capability receipt|model receipt)"):
                    control_module.validate_chat_conversation(candidate, conversation_id)

    def test_private_chat_is_disabled_without_exact_runtime_and_rejects_widened_requests(self):
        self.assertEqual(self.control.chat()["state"], "disabled")
        with self.assertRaisesRegex(control_module.PublicRejected, "shape"):
            self.control.chat_turn({"schemaVersion": 1, "requestId": "chatreq-" + "4" * 32, "conversationHandle": None, "message": "hello", "agent": "other"})
        with self.assertRaisesRegex(control_module.PublicRejected, "identity"):
            self.control.chat_turn({"schemaVersion": 1, "requestId": "bad", "conversationHandle": None, "message": "hello"})

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliably available on Windows")
    def test_private_chat_rejects_a_linked_launcher(self):
        binary, _home = self.configure_chat_runtime()
        linked = binary.with_name("openclaw-linked")
        linked.symlink_to(binary)
        full = json.loads(self.onboarding_path.read_text(encoding="utf-8"))
        full["openclawBin"] = str(linked)
        control_module.atomic_json(self.onboarding_path, full, 0o600)
        self.assertEqual(self.control.chat()["state"], "unavailable")

    def test_private_chat_serializes_turns_without_hiding_the_composer_contract(self):
        started = threading.Event()
        release = threading.Event()
        output = json.dumps({"status": "ok", "result": {"payloads": [{"text": "Finished once."}], "meta": chat_meta()}}).encode()

        def blocking_runner(command, root, timeout_seconds, environment):
            started.set()
            self.assertTrue(release.wait(5))
            return 0, output

        self.configure_chat_runtime(blocking_runner)
        first_result = []

        def run_first():
            first_result.append(self.control.chat_turn({
                "schemaVersion": 1, "requestId": "chatreq-" + "5" * 32,
                "conversationHandle": None, "message": "Run one exact turn.",
            }))

        worker = threading.Thread(target=run_first)
        worker.start()
        self.assertTrue(started.wait(5))
        with self.assertRaisesRegex(control_module.PublicRejected, "already running"):
            self.control.chat_turn({
                "schemaVersion": 1, "requestId": "chatreq-" + "6" * 32,
                "conversationHandle": None, "message": "Do not overlap execution.",
            })
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(first_result[0]["conversations"][0]["turns"][0]["state"], "succeeded")

    def test_private_chat_task_returns_durable_progress_before_the_agent_finishes(self):
        started = threading.Event()
        release = threading.Event()
        output = json.dumps({"status": "ok", "result": {"payloads": [{"text": "Durably finished."}], "meta": chat_meta({"calls": 1, "failures": 0})}}).encode()

        def blocking_runner(command, root, timeout_seconds, environment):
            started.set()
            self.assertTrue(release.wait(5))
            return 0, output

        self.configure_chat_runtime(blocking_runner)
        request = {
            "schemaVersion": 1, "requestId": "chatreq-" + "a" * 32,
            "conversationHandle": None, "message": "Run as a reconnectable private task.",
        }
        submitted = self.control.chat_task(request)
        task = submitted["conversations"][0]["turns"][0]
        self.assertEqual(task["state"], "running")
        self.assertEqual(task["phase"], "queued")
        self.assertRegex(task["taskHandle"], r"^chattask-[a-f0-9]{24}$")
        self.assertEqual([event["code"] for event in task["activity"]], ["accepted"])
        self.assertTrue(started.wait(5))

        live = self.control.chat()
        live_task = live["conversations"][0]["turns"][0]
        self.assertEqual(live_task["taskHandle"], task["taskHandle"])
        self.assertEqual(live_task["phase"], "executing")
        self.assertEqual([event["code"] for event in live_task["activity"]], ["accepted", "agent-started"])
        replay = self.control.chat_task(request)
        self.assertEqual(replay["conversations"][0]["turns"][0]["taskHandle"], task["taskHandle"])

        release.set()
        deadline = time.monotonic() + 5
        settled = None
        while time.monotonic() < deadline:
            settled = self.control.chat()["conversations"][0]["turns"][0]
            if settled["state"] != "running":
                break
            time.sleep(0.01)
        self.assertIsNotNone(settled)
        self.assertEqual(settled["state"], "succeeded")
        self.assertEqual(settled["phase"], "completed")
        self.assertEqual(settled["assistantText"], "Durably finished.")
        self.assertEqual([event["code"] for event in settled["activity"]], ["accepted", "agent-started", "response-verified"])

    def test_private_chat_recovers_a_running_turn_as_interrupted(self):
        self.configure_chat_runtime()
        now = self.control.now()
        conversation_id = f"conversation-{int(now.timestamp() * 1000):013d}-abcdef123456"
        turn_id = f"turn-{int(now.timestamp() * 1000):013d}-abcdef123456"
        turn = {
            "turnId": turn_id, "requestId": "chatreq-" + "7" * 32,
            "createdAt": control_module.iso(now), "finishedAt": None,
            "userText": "Recover this exact turn.", "state": "running", "assistantText": None,
            "toolCalls": None, "toolFailures": None, "responseSha256": None,
            "previousSha256": "0" * 64,
        }
        turn["recordSha256"] = control_module.digest(turn)
        conversation = {
            "schemaVersion": 1, "conversationId": conversation_id,
            "createdAt": control_module.iso(now), "updatedAt": control_module.iso(now),
            "title": "Recover this exact turn.", "state": "active", "turns": [turn],
            "boundary": control_module.CHAT_CONVERSATION_BOUNDARY,
        }
        control_module.validate_chat_conversation(conversation, conversation_id)
        control_module.atomic_json(self.control.chat_conversations / f"{conversation_id}.json", conversation, 0o600)
        (self.control.chat_inputs / f"{turn_id}.txt").write_text("Recover this exact turn.", encoding="utf-8")
        (self.control.chat_inputs / f"{turn_id}.txt").chmod(0o600)

        recovered = control_module.ControlState(ROOT, self.state_path, self.onboarding_path, runner=self.runner)
        value = recovered.chat()
        self.assertEqual(value["state"], "ready")
        self.assertEqual(value["conversations"][0]["state"], "attention")
        self.assertEqual(value["conversations"][0]["turns"][0]["state"], "interrupted")
        self.assertEqual(value["conversations"][0]["turns"][0]["phase"], "interrupted")
        self.assertEqual([event["code"] for event in value["conversations"][0]["turns"][0]["activity"]], ["accepted", "recovery-required"])
        self.assertEqual(list(recovered.chat_inputs.iterdir()), [])

    def test_private_chat_recovery_preserves_started_broker_evidence_without_retry(self):
        self.configure_chat_runtime()
        now = self.control.now()
        conversation_id = f"conversation-{int(now.timestamp() * 1000):013d}-aaaabbbbcccc"
        turn_id = f"turn-{int(now.timestamp() * 1000):013d}-aaaabbbbcccc"
        turn = {
            "turnId": turn_id, "requestId": "chatreq-" + "9" * 32,
            "createdAt": control_module.iso(now), "finishedAt": None,
            "userText": "Recover one ambiguous bounded action.", "state": "running",
            "assistantText": None, "toolCalls": None, "toolFailures": None, "responseSha256": None,
            "capabilityReceipt": None, "brokerReceipt": None, "previousSha256": "0" * 64,
        }
        turn["recordSha256"] = control_module.digest(turn)
        conversation = {
            "schemaVersion": 1, "conversationId": conversation_id,
            "createdAt": control_module.iso(now), "updatedAt": control_module.iso(now),
            "title": "Recover one ambiguous bounded action.", "state": "active", "turns": [turn],
            "boundary": control_module.CHAT_CONVERSATION_BOUNDARY,
        }
        control_module.atomic_json(self.control.chat_conversations / f"{conversation_id}.json", conversation, 0o600)
        session_key = f"agent:pixel:portal-{conversation_id}"
        started = portal_tool_receipt(
            session_key, "pixel_calendar_propose_update", 0, {
                "brokerKind": "calendar-action", "status": "unknown-ambiguous",
                "correlationIdSha256": None, "approvalRequired": False,
                "externalEffectOccurred": None, "autoWithinPolicy": False, "ambiguous": True,
            }, state="started",
        )
        bundle = control_module.chat_tool_receipt_bundle(
            turn_id, hashlib.sha256(session_key.encode()).hexdigest(), [started], control_module.iso(now),
        )
        control_module.atomic_json(self.control.chat_tool_receipts / f"{turn_id}.json", bundle, 0o600)

        recovered = control_module.ControlState(ROOT, self.state_path, self.onboarding_path, runner=self.runner)
        recovered_turn = recovered.chat()["conversations"][0]["turns"][0]
        self.assertEqual(recovered_turn["state"], "interrupted")
        self.assertEqual(recovered_turn["phase"], "interrupted")
        self.assertEqual(self.runner.calls, [])
        retained = json.loads((recovered.chat_tool_receipts / f"{turn_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(retained["receipts"][0]["state"], "started")

    def test_private_chat_recovery_orders_terminal_activity_after_a_backward_clock_step(self):
        self.configure_chat_runtime()
        now = self.control.now()
        started = now + control_module.timedelta(seconds=5)
        conversation_id = f"conversation-{int(now.timestamp() * 1000):013d}-fedcba654321"
        turn_id = f"turn-{int(now.timestamp() * 1000):013d}-fedcba654321"
        turn = {
            "turnId": turn_id, "requestId": "chatreq-" + "8" * 32,
            "createdAt": control_module.iso(now), "finishedAt": None,
            "userText": "Recover after the clock moves backward.", "state": "running",
            "assistantText": None, "toolCalls": None, "toolFailures": None,
            "responseSha256": None, "previousSha256": "0" * 64,
            "activity": [
                {"code": "accepted", "at": control_module.iso(now)},
                {"code": "agent-started", "at": control_module.iso(started)},
            ],
        }
        turn["recordSha256"] = control_module.digest(turn)
        conversation = {
            "schemaVersion": 1, "conversationId": conversation_id,
            "createdAt": control_module.iso(now), "updatedAt": control_module.iso(started),
            "title": "Recover after the clock moves backward.", "state": "active", "turns": [turn],
            "boundary": control_module.CHAT_CONVERSATION_BOUNDARY,
        }
        control_module.validate_chat_conversation(conversation, conversation_id)
        control_module.atomic_json(self.control.chat_conversations / f"{conversation_id}.json", conversation, 0o600)

        recovered = control_module.ControlState(
            ROOT, self.state_path, self.onboarding_path, runner=self.runner, now=lambda: now,
        )
        recovered_turn = recovered.chat()["conversations"][0]["turns"][0]
        self.assertEqual(recovered_turn["state"], "interrupted")
        self.assertEqual(
            [event["code"] for event in recovered_turn["activity"]],
            ["accepted", "agent-started", "recovery-required"],
        )
        self.assertEqual(recovered_turn["activity"][-1]["at"], control_module.iso(started))

    def test_private_chat_http_requires_session_token_and_same_origin(self):
        output = json.dumps({
            "status": "ok",
            "result": {"payloads": [{"text": "Bounded HTTP response."}], "meta": chat_meta({"calls": 1, "failures": 0}, privatePath=str(self.base))},
        }).encode()
        runner = RecordingRunner(output=output)
        self.configure_chat_runtime(runner)
        server = control_module.ControlServer(("127.0.0.1", 0), self.control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        host = f"127.0.0.1:{server.server_port}"

        def call(method, path, *, cookie=None, token=None, body=None, origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            headers = {"Host": host}
            if cookie:
                headers["Cookie"] = cookie
            if token:
                headers["X-Pixel-Review-Token"] = token
            payload = None
            if body is not None:
                payload = json.dumps(body).encode()
                headers.update({
                    "Content-Type": "application/json", "Content-Length": str(len(payload)),
                    "Origin": origin or f"http://{host}", "Sec-Fetch-Site": "same-origin",
                })
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            response_headers = dict(response.getheaders())
            parsed = json.loads(data) if data and response_headers.get("Content-Type", "").startswith("application/json") else data
            result = (response.status, response_headers, parsed)
            connection.close()
            return result

        request = {
            "schemaVersion": 1, "requestId": "chatreq-" + "8" * 32,
            "conversationHandle": None, "message": "Run the fixed local agent.",
        }
        try:
            status, headers, _page = call("GET", "/")
            self.assertEqual(status, 200)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            status, _headers, denied = call("GET", "/api/v1/chat", cookie=cookie)
            self.assertEqual(status, 403)
            self.assertNotIn(server.review_token, json.dumps(denied))
            status, _headers, projection = call("GET", "/api/v1/chat", cookie=cookie, token=server.review_token)
            self.assertEqual(status, 200)
            self.assertEqual(projection["state"], "ready")
            status, _headers, denied = call("POST", "/api/v1/chat/turns", cookie=cookie, body=request)
            self.assertEqual(status, 403)
            status, _headers, denied = call(
                "POST", "/api/v1/chat/turns", cookie=cookie, token=server.review_token,
                body=request, origin="https://attacker.invalid",
            )
            self.assertEqual(status, 403)
            status, _headers, completed = call(
                "POST", "/api/v1/chat/turns", cookie=cookie, token=server.review_token, body=request,
            )
            self.assertEqual(status, 200)
            self.assertEqual(completed["conversations"][0]["turns"][0]["assistantText"], "Bounded HTTP response.")
            self.assertNotIn(str(self.base), json.dumps(completed))
            status, _headers, replay = call(
                "POST", "/api/v1/chat/turns", cookie=cookie, token=server.review_token, body=request,
            )
            self.assertEqual(status, 200)
            self.assertEqual(replay["activeHandle"], completed["activeHandle"])
            self.assertEqual(len(runner.calls), 1)
            task_request = {
                "schemaVersion": 1, "requestId": "chatreq-" + "b" * 32,
                "conversationHandle": completed["activeHandle"], "message": "Continue as an asynchronous task.",
            }
            status, _headers, denied = call("POST", "/api/v1/chat/tasks", cookie=cookie, body=task_request)
            self.assertEqual(status, 403)
            status, _headers, accepted = call(
                "POST", "/api/v1/chat/tasks", cookie=cookie, token=server.review_token, body=task_request,
            )
            self.assertEqual(status, 200)
            self.assertEqual(accepted["conversations"][0]["turns"][-1]["state"], "running")
            self.assertEqual(accepted["conversations"][0]["turns"][-1]["phase"], "queued")
            deadline = time.monotonic() + 3
            settled = None
            while time.monotonic() < deadline:
                status, _headers, settled = call("GET", "/api/v1/chat", cookie=cookie, token=server.review_token)
                self.assertEqual(status, 200)
                if settled["conversations"][0]["turns"][-1]["state"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(settled["conversations"][0]["turns"][-1]["state"], "succeeded")
            self.assertEqual(len(runner.calls), 2)
            widened = {**request, "requestId": "chatreq-" + "9" * 32, "command": "arbitrary"}
            status, _headers, rejected = call(
                "POST", "/api/v1/chat/turns", cookie=cookie, token=server.review_token, body=widened,
            )
            self.assertEqual(status, 400)
            self.assertNotIn("arbitrary", json.dumps(rejected))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def runtime_attestation_fixture(self, *, endpoint_checks="verified"):
        source = self.base / "attested-source"
        active = self.base / "pixel-install" / "current"
        openclaw_home = self.base / "openclaw-home"
        generated = source / ".generated"
        for directory in (active, openclaw_home, generated):
            directory.mkdir(parents=True, exist_ok=True)
        (source / "VERSION").write_text("4.1.0\n", encoding="ascii")
        (source / "pixel").write_text("#!/usr/bin/env bash\n", encoding="ascii")
        deployment_config = {
            "deploymentProfile": "prepared", "capabilityProfile": "chief-of-staff",
            "modelProvider": "local", "modelId": "PRIVATE_MODEL_CANARY",
            "modelContextWindow": 8192, "modelMaxTokens": 1024, "modelReasoning": True,
            "limbs": {"email": True, "calendar": True, "social": False, "web": True, "operations": False, "frontier": False},
        }
        control_module.atomic_json(generated / "deployment.json", deployment_config, 0o600)
        control_module.atomic_json(openclaw_home / "openclaw.json", {"private": "PRIVATE_CONFIG_CANARY"}, 0o600)
        identity = {
            "schemaVersion": 1, "kind": "pixel-release-source-identity", "pixel": "4.1.0",
            "source": {"state": "git-clean", "commit": "a" * 40, "tree": "b" * 40},
            "manifests": {"releaseSha256": "c" * 64, "compatibilitySha256": "d" * 64, "qualificationMatrixSha256": "e" * 64},
            "qualification": {"recordStatus": "candidate", "sourceCommit": "f" * 40, "qualifiedAt": "2026-08-11", "liveAudit": "PRIVATE_AUDIT_CANARY", "relationship": "qualified-ancestor"},
            "boundary": "fixture",
        }
        control_module.atomic_json(active / "release-identity.json", identity, 0o600)
        (active / "deployment-inputs.sha256").write_text(f"{'1' * 64}  fixture\n", encoding="ascii")
        version_hash = control_module.hashlib.sha256((source / "VERSION").read_bytes()).hexdigest()
        (active / "source-runtime.sha256").write_text(f"{version_hash}  VERSION\n", encoding="ascii")
        (active / "VERSION").write_text("4.1.0\n", encoding="ascii")

        def file_hash(path):
            return control_module.hashlib.sha256(path.read_bytes()).hexdigest()

        installed_names = ["VERSION", "deployment-inputs.sha256", "release-identity.json", "source-runtime.sha256"]
        (active / "install-manifest.sha256").write_text("".join(
            f"{file_hash(active / name)}  {name}\n" for name in sorted(installed_names)
        ), encoding="ascii")
        now = self.control.now()
        receipt = {
            "schemaVersion": 1, "kind": "pixel-runtime-attestation",
            "status": "verified" if endpoint_checks == "verified" else "limited",
            "verifiedAt": control_module.iso(now), "pixel": "4.1.0",
            "source": dict(identity["source"]),
            "qualification": {name: identity["qualification"][name] for name in ("recordStatus", "sourceCommit", "qualifiedAt", "relationship")},
            "release": {
                "sourceIdentitySha256": file_hash(active / "release-identity.json"),
                "deploymentInputsSha256": file_hash(active / "deployment-inputs.sha256"),
                "sourceRuntimeSha256": file_hash(active / "source-runtime.sha256"),
                "installManifestSha256": file_hash(active / "install-manifest.sha256"),
                "releaseManifestSha256": identity["manifests"]["releaseSha256"],
                "compatibilityManifestSha256": identity["manifests"]["compatibilitySha256"],
                "qualificationMatrixSha256": identity["manifests"]["qualificationMatrixSha256"],
            },
            "configuration": {
                "generatedDeploymentSha256": file_hash(generated / "deployment.json"),
                "activeOpenClawSha256": file_hash(openclaw_home / "openclaw.json"),
            },
            "runtime": {
                "state": "gateway-verified-model-unproven", "openclaw": "2026.6.33", "routeClass": "local",
                "providerIdSha256": "2" * 64, "modelIdSha256": "3" * 64, "contextWindow": 8192,
                "maxOutputTokens": 1024, "reasoning": True, "endpointChecks": endpoint_checks,
            },
            "profiles": {"deployment": "prepared", "capability": "chief-of-staff"},
            "connectors": [
                {"id": name, "state": "enabled-verified" if deployment_config["limbs"][name] else "disabled-verified"}
                for name in control_module.LIMBS
            ],
            "boundary": control_module.RUNTIME_ATTESTATION_BOUNDARY,
        }
        receipt_path = active.parent / "runtime-attestation.json"
        control_module.atomic_json(receipt_path, receipt, 0o600)
        state = control_module.ControlState(source, self.state_path / "attestation", self.onboarding_path.parent / "attestation.json", runner=self.runner)
        deployment = {"installDir": str(active.parent.resolve()), "openclawHome": str(openclaw_home.resolve())}
        return state, deployment, receipt, receipt_path, source, active, openclaw_home, now

    def install_control_policy(self, **enabled):
        policy = control_module.default_control_policy()
        policy["backup"] = {
            "directory": "/var/backups/pixel-control-test",
            "ageRecipient": "age1" + "q" * 58,
        }
        for name, value in enabled.items():
            policy["actions"][name] = value
        control_module.atomic_json(self.state_path / "policy.json", policy, 0o600)
        return policy

    def install_custom_frontier_policy(self):
        policy_path = self.onboarding_path.parent / "custom-frontier-policy.json"
        policy = json.loads((ROOT / "deploy/frontier-broker/policy.chatgpt.example.json").read_text(encoding="utf-8"))
        policy["deployment"] = "control-budget-test"
        private = control_module.merge_onboarding({}, control_module.default_onboarding())
        private.update({
            "frontierLimbEnabled": True,
            "frontierAuthMode": "chatgpt",
            "frontierBudgetProfile": "custom",
            "frontierPolicyFile": str(policy_path.resolve()),
        })
        control_module.atomic_json(policy_path, policy, 0o600)
        control_module.atomic_json(self.onboarding_path, private, 0o600)
        return policy_path

    def frontier_review_result(self, job_id="frontier-1786195551000-abcdef123456"):
        capsule = {
            "schemaVersion": 1,
            "taskClass": "plan_review",
            "classification": "confidential",
            "dataCategories": ["structural", "personal-identifiers"],
            "responseLimits": {"maxOutputTokens": 1024},
            "payload": {
                "objective": "Review <PIXEL_EMAIL_001> rollout",
                "assumptions": ["Local checks passed"],
                "constraints": ["No generic tools"],
                "localFindings": ["Independent review remains"],
                "acceptanceCriteria": ["All checks stay green"],
            },
            "instructions": list(control_module.FRONTIER_REVIEW_INSTRUCTIONS),
        }
        return {
            "schemaVersion": 1,
            "jobId": job_id,
            "status": "awaiting-approval",
            "planHash": "a" * 64,
            "capsuleHash": control_module.digest(capsule),
            "taskClass": capsule["taskClass"],
            "classification": capsule["classification"],
            "dataCategories": list(capsule["dataCategories"]),
            "providerAuthMode": "chatgpt",
            "estimatedInputTokens": 321,
            "maxOutputTokens": capsule["responseLimits"]["maxOutputTokens"],
            "placeholderCount": 1,
            "costEstimate": {"mode": "subscription", "estimatedAmountMicros": None, "currency": None},
            "sanitizedPreview": capsule,
            "privatePlanPath": "PRIVATE_FRONTIER_CANARY",
        }

    def deep_work_semantic_review(self):
        return {
            "$schema": control_module.WORK_SEMANTIC_REVIEW_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-control-work-semantic-review", "status": "waiting-authority",
            "profile": "scout", "dataClassification": "internal",
            "objective": "Confirm the retained local invariant.",
            "acceptanceCriteria": ["The quoted invariant supports the finding."],
            "reviewSha256": "a" * 64,
            "content": {
                "title": "Local invariant review", "overview": None, "methodology": None,
                "findings": [{
                    "statement": "The local invariant is present.", "material": True,
                    "evidence": [{"kind": "local-quote", "reference": "project:src/main.js", "excerpt": "const invariant = 42", "bytes": 20}],
                }],
                "limitations": "Presence does not establish semantic correctness.", "artifacts": [],
            },
            "verification": {
                "status": "evidence-pass", "method": "deterministic-local-evidence-presence",
                "independent": True, "semanticAccuracyVerified": False, "checkedItems": 1,
                "failedItems": 0, "explanation": "Pixel reopened the retained file and matched the exact quote; truth still requires human judgment.",
            },
            "completionEffect": "none-read-only",
            "privacy": {
                "privateContentIncluded": True, "relativeEvidenceReferencesIncluded": True,
                "hostAbsolutePathsIncluded": False, "credentialsIncluded": False, "contentLeavesHost": False,
            },
            "authority": {
                "grantsExecution": False, "grantsLease": False, "grantsReplay": False,
                "grantsAcceptance": False, "grantsCompletion": False, "grantsPublication": False,
                "grantsDeployment": False, "grantsExternalEffects": False, "grantsScopeExpansion": False,
            },
            "boundary": control_module.WORK_SEMANTIC_REVIEW_BOUNDARY,
        }

    def work_operator_status(self, generated_at=None):
        generated_at = generated_at or self.control.now()
        started = generated_at - control_module.timedelta(seconds=30)
        updated = generated_at - control_module.timedelta(seconds=1)
        return {
            "$schema": control_module.WORK_OPERATOR_SCHEMA, "schemaVersion": 1,
            "projectionId": f"workoperatorstatus-{int(generated_at.timestamp() * 1000):013d}-abcdef123456",
            "generatedAt": control_module.iso(generated_at), "controllerState": "busy",
            "goal": {
                "state": "running", "updatedAt": control_module.iso(updated),
                "progress": {"milestonesTotal": 4, "milestonesCompleted": 1, "jobsStarted": 2, "failures": 0},
                "usage": {"runtimeSeconds": 90, "modelRequests": 4, "inputTokens": 1200, "outputTokens": 240, "networkBytes": 0, "artifactBytes": 2048, "failures": 0},
                "budgets": {
                    "accounting": "settled-plus-active-observed",
                    "used": {"jobs": 2, "runtimeSeconds": 119, "modelRequests": 7, "inputTokens": 1500, "outputTokens": 300, "networkBytes": 0, "artifactBytes": 4096, "failures": 0},
                    "limits": {"jobs": 4, "runtimeSeconds": 600, "modelRequests": 10, "inputTokens": 10000, "outputTokens": 2000, "networkBytes": 1048576, "artifactBytes": 65536, "failures": 2},
                    "remaining": {"jobs": 2, "runtimeSeconds": 481, "modelRequests": 3, "inputTokens": 8500, "outputTokens": 1700, "networkBytes": 1048576, "artifactBytes": 61440, "failures": 2},
                },
                "continuity": {"checkpointSequence": 7, "restartSafe": True, "completionRequiresIndependentVerification": True, "progressModel": "durable-events", "watchdogRole": "liveness-only"},
                "nextAction": "recover-or-continue-child",
            },
            "sessions": [{
                "sessionHandle": "workdisplay-" + "a" * 24, "current": True, "mode": "builder", "state": "verifying",
                "startedAt": control_module.iso(started), "updatedAt": control_module.iso(updated),
                "progress": {"criteriaTotal": 3, "criteriaPassing": 2, "criteriaFailing": 1, "iteration": 2, "maxIterations": 5, "noProgressCount": 0},
                "usage": {"runtimeSeconds": 29, "modelRequests": 3, "inputTokens": 300, "outputTokens": 60, "networkBytes": 0, "artifactBytes": 2048, "failures": 0},
                "artifacts": {"count": 2, "totalBytes": 2048, "kinds": [{"kind": "patch", "count": 1}, {"kind": "test-evidence", "count": 1}]},
                "verification": "pending", "capability": {"state": "authorized", "toolCount": 2, "singleUseCalls": True, "networkAccess": False, "externalEffects": False},
                "boundaryState": {"state": "within-authority", "requestedExpansion": "none"},
                "activity": [{"eventId": "workevent-" + "b" * 24, "at": control_module.iso(updated), "category": "verification", "summaryCode": "verification-started", "outcome": "pending"}],
                "controls": {"browserCanPause": False, "browserCanCancel": False, "browserCanResume": False, "browserCanExpandBoundary": False, "browserCanOpenArtifacts": False},
            }],
            "services": [{"id": "controller", "state": "ready", "observedAt": control_module.iso(updated)}, {"id": "worker", "state": "ready", "observedAt": control_module.iso(updated)}, {"id": "verifier", "state": "busy", "observedAt": control_module.iso(updated)}, {"id": "capability-adapter", "state": "busy", "observedAt": control_module.iso(updated)}],
            "privacy": {"objectiveText": False, "promptText": False, "toolArguments": False, "artifactNames": False, "paths": False, "hashes": False, "credentials": False, "providerContent": False},
            "boundary": control_module.WORK_OPERATOR_BOUNDARY,
        }

    def work_authoring_control(self, *, max_drafts=4):
        private = self.base / "work-authoring-private"
        objects = private / "objects"
        drafts = private / "drafts"
        control_module.ensure_directory(objects)
        control_module.ensure_directory(drafts)
        policy_path = private / "work-policy.json"
        catalog_path = private / "catalog.json"
        config_path = private / "authoring.json"
        policy = {
            "$schema": "https://osmantic.com/pixel/schemas/work-policy-v1.schema.json", "schemaVersion": 1,
            "enabled": True,
            "profiles": {
                "scout": {"enabled": True}, "builder": {"enabled": True},
                "dataLab": {"enabled": True}, "researcher": {"enabled": True},
                "publicProject": {"enabled": False},
            },
        }
        content_hash = "a" * 64
        catalog = {
            "$schema": control_module.WORK_INPUT_CATALOG_SCHEMA, "schemaVersion": 1,
            "catalogId": "inputcatalog-1786424400000-abcdef123456", "createdAt": "2026-08-11T05:00:00.000Z",
            "entries": [{
                "id": "project", "kind": "repository-snapshot", "objectName": f"{content_hash}.tar",
                "contentSha256": content_hash, "bytes": 1048576, "classification": "internal", "mountMode": "read-only",
            }],
            "boundary": control_module.WORK_INPUT_CATALOG_BOUNDARY,
        }
        config = {
            "$schema": control_module.WORK_AUTHORING_CONFIG_SCHEMA, "schemaVersion": 1,
            "policyFile": str(policy_path.absolute()), "inputCatalogFile": str(catalog_path.absolute()),
            "objectStoreDirectory": str(objects.absolute()), "draftDirectory": str(drafts.absolute()),
            "maxDrafts": max_drafts, "boundary": control_module.WORK_AUTHORING_CONFIG_BOUNDARY,
        }
        control_module.atomic_json(policy_path, policy, 0o600)
        control_module.atomic_json(catalog_path, catalog, 0o600)
        control_module.atomic_json(config_path, config, 0o600)
        state_path = self.base / "work-authoring-control"
        state = control_module.ControlState(
            ROOT, state_path, self.onboarding_path, runner=self.runner,
            work_authoring_config_path=config_path,
        )
        control_policy = control_module.default_control_policy()
        control_policy["actions"]["deepWorkDraft"] = True
        control_module.atomic_json(state_path / "policy.json", control_policy, 0o600)
        return state, config, policy, catalog, catalog_path, drafts

    def test_deep_work_status_is_content_free_stale_aware_and_fails_closed(self):
        self.assertEqual(self.control.deep_work_status()["state"], "disabled")
        status_path = self.base / "private" / "work-status.json"
        now = self.control.now()
        control_module.atomic_json(status_path, self.work_operator_status(now), 0o600)
        control = control_module.ControlState(ROOT, self.base / "work-control", self.onboarding_path, runner=self.runner, now=lambda: now, work_status_path=status_path)
        projected = control.deep_work_status()
        self.assertEqual(projected["state"], "busy")
        self.assertEqual(projected["summary"], {"activeSessions": 1, "attentionSessions": 0, "artifactCount": 2, "degradedServices": 0})
        self.assertEqual(projected["goal"]["progress"]["milestonesCompleted"], 1)
        self.assertEqual(projected["goal"]["usage"]["inputTokens"], 1200)
        self.assertEqual(projected["goal"]["budgets"]["used"]["inputTokens"], 1500)
        self.assertEqual(projected["goal"]["budgets"]["remaining"]["inputTokens"], 8500)
        self.assertTrue(projected["goal"]["continuity"]["restartSafe"])
        self.assertEqual(projected["goal"]["continuity"]["progressModel"], "durable-events")
        self.assertEqual(projected["sessions"][0]["progress"]["criteriaPassing"], 2)
        self.assertEqual(projected["sessions"][0]["capability"]["state"], "authorized")
        self.assertFalse(projected["sessions"][0]["controls"]["browserCanExpandBoundary"])
        encoded = json.dumps(projected)
        for forbidden in ("projectionId", "sessionHandle", "eventId", "workdisplay-", "workevent-", "PRIVATE OBJECTIVE", "artifactPath"):
            self.assertNotIn(forbidden, encoded)

        stale_time = now - control_module.timedelta(seconds=121)
        control_module.atomic_json(status_path, self.work_operator_status(stale_time), 0o600)
        self.assertEqual(control.deep_work_status()["state"], "offline")
        hostile = self.work_operator_status(now)
        hostile["sessions"][0]["privateObjective"] = "PRIVATE OBJECTIVE"
        control_module.atomic_json(status_path, hostile, 0o600)
        self.assertEqual(control.deep_work_status()["state"], "unavailable")
        self.assertNotIn("PRIVATE OBJECTIVE", json.dumps(control.deep_work_status()))

        def set_nested(value, path, replacement):
            target = value
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = replacement

        hostile_cases = (
            (("privacy", "objectiveText"), True),
            (("goal", "continuity", "restartSafe"), False),
            (("goal", "continuity", "progressModel"), "hourly"),
            (("goal", "state"), "PRIVATE OBJECTIVE"),
            (("goal", "progress", "milestonesCompleted"), 5),
            (("goal", "budgets", "accounting"), "worker-reported"),
            (("goal", "budgets", "remaining", "inputTokens"), 8501),
            (("goal", "budgets", "used", "inputTokens"), 1199),
            (("goal", "state"), "failed"),
            (("sessions", 0, "controls", "browserCanPause"), True),
            (("sessions", 0, "capability", "networkAccess"), True),
            (("sessions", 0, "capability", "toolCount"), 0),
            (("sessions", 0, "current"), False),
            (("sessions", 0, "usage", "inputTokens"), 299),
            (("sessions", 0, "progress", "criteriaPassing"), 3),
            (("sessions", 0, "state"), "completed"),
            (("sessions", 0, "state"), "PRIVATE OBJECTIVE"),
            (("sessions", 0, "activity", 0, "category"), "PRIVATE OBJECTIVE"),
            (("services", 0, "id"), "PRIVATE OBJECTIVE"),
            (("services", 3, "state"), "ready"),
        )
        for path, replacement in hostile_cases:
            with self.subTest(path=path):
                hostile = self.work_operator_status(now)
                set_nested(hostile, path, replacement)
                control_module.atomic_json(status_path, hostile, 0o600)
                rejected = control.deep_work_status()
                self.assertEqual(rejected["state"], "unavailable")
                self.assertNotIn("PRIVATE OBJECTIVE", json.dumps(rejected))

        misleading_goal = self.work_operator_status(now)
        misleading_goal["goal"]["state"] = "failed"
        misleading_goal["goal"]["nextAction"] = "terminal"
        control_module.atomic_json(status_path, misleading_goal, 0o600)
        self.assertEqual(control.deep_work_status()["state"], "unavailable")

        future = self.work_operator_status(now + control_module.timedelta(seconds=6))
        control_module.atomic_json(status_path, future, 0o600)
        self.assertEqual(control.deep_work_status()["state"], "unavailable")

        wrong_projection_time = self.work_operator_status(now)
        wrong_projection_time["projectionId"] = "workoperatorstatus-1786377600000-abcdef123456"
        control_module.atomic_json(status_path, wrong_projection_time, 0o600)
        self.assertEqual(control.deep_work_status()["state"], "unavailable")

    def test_deep_work_recovery_states_are_visible_attention_without_new_authority(self):
        status_path = self.base / "private" / "work-status.json"
        now = self.control.now()
        for state in ("cleanup-failed", "recovery-inconclusive"):
            with self.subTest(state=state):
                value = self.work_operator_status(now)
                session = value["sessions"][0]
                session["state"] = state
                session["verification"] = "not-started"
                session["boundaryState"] = {"state": "blocked", "requestedExpansion": "none"}
                session["activity"][0].update({"category": "controller", "summaryCode": state, "outcome": "failed"})
                control_module.atomic_json(status_path, value, 0o600)
                control = control_module.ControlState(ROOT, self.base / "work-control", self.onboarding_path, runner=self.runner, now=lambda: now, work_status_path=status_path)
                projected = control.deep_work_status()
                self.assertEqual(projected["sessions"][0]["state"], state)
                self.assertEqual(projected["summary"]["attentionSessions"], 1)
                self.assertEqual(projected["summary"]["activeSessions"], 1 if state == "cleanup-failed" else 0)
                self.assertFalse(projected["sessions"][0]["controls"]["browserCanResume"])

        misleading = self.work_operator_status(now)
        misleading["sessions"][0]["state"] = "cleanup-failed"
        control_module.atomic_json(status_path, misleading, 0o600)
        control = control_module.ControlState(ROOT, self.base / "work-control", self.onboarding_path, runner=self.runner, now=lambda: now, work_status_path=status_path)
        self.assertEqual(control.deep_work_status()["state"], "unavailable")

    def test_default_onboarding_contract_contains_no_credential_field(self):
        value = self.control.onboarding()
        self.assertFalse(value["credentialsExposed"])
        self.assertEqual(value["settings"]["frontierAuthMode"], "chatgpt")
        self.assertEqual(value["settings"]["frontierBudgetProfile"], "starter")
        serialized = json.dumps(value)
        for forbidden in ("modelApiKey", "frontierCredentialFile", "private-key", "password"):
            self.assertNotIn(forbidden, serialized)

    def test_frontier_preferences_are_public_but_auth_material_remains_private(self):
        settings = self.control.onboarding()["settings"]
        settings["frontierAuthMode"] = "api-key"
        settings["frontierBudgetProfile"] = "expanded"
        validated = control_module.validate_onboarding(settings)
        self.assertEqual(validated["frontierAuthMode"], "api-key")
        self.assertEqual(validated["frontierBudgetProfile"], "expanded")

        legacy = dict(settings)
        legacy.pop("frontierAuthMode")
        legacy.pop("frontierBudgetProfile")
        self.assertEqual(control_module.validate_onboarding(legacy)["frontierAuthMode"], "chatgpt")
        partial = dict(legacy)
        partial["frontierAuthMode"] = "chatgpt"
        with self.assertRaisesRegex(control_module.PublicRejected, "missing or unknown"):
            control_module.validate_onboarding(partial)
        for field, hostile in (
            ("frontierAuthMode", "ambient-session"),
            ("frontierBudgetProfile", "unlimited"),
        ):
            invalid = dict(settings)
            invalid[field] = hostile
            with self.assertRaises(control_module.PublicRejected):
                control_module.validate_onboarding(invalid)

        legacy_private = control_module.merge_onboarding({}, control_module.default_onboarding())
        legacy_private.pop("frontierAuthMode")
        legacy_private.pop("frontierBudgetProfile")
        legacy_private["frontierPolicyFile"] = "/opt/pixel/deploy/frontier-broker/policy.example.json"
        projected = control_module.public_onboarding(legacy_private)
        self.assertEqual(projected["frontierAuthMode"], "api-key")
        self.assertEqual(projected["frontierBudgetProfile"], "balanced")
        legacy_private["frontierPolicyFile"] = "/opt/pixel/deploy/frontier-broker/policy.chatgpt.example.json"
        self.assertEqual(control_module.public_onboarding(legacy_private)["frontierAuthMode"], "chatgpt")
        legacy_private["frontierPolicyFile"] = "/secure/client/custom-frontier-policy.json"
        self.assertEqual(control_module.public_onboarding(legacy_private)["frontierBudgetProfile"], "custom")

        legacy_root = self.base / "legacy-frontier-root"
        (legacy_root / ".generated").mkdir(parents=True)
        (legacy_root / "pixel").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (legacy_root / "VERSION").write_text("3.3.0\n", encoding="utf-8")
        legacy_onboarding = self.base / "legacy-frontier-onboarding.json"
        control_module.atomic_json(legacy_onboarding, legacy_private, 0o600)
        control_module.atomic_json(legacy_root / ".generated" / "deployment.json", {
            "frontierBroker": {"authMode": "chatgpt", "budgetProfile": "custom"},
        }, 0o600)
        legacy_state = control_module.ControlState(
            legacy_root, self.base / "legacy-frontier-state", legacy_onboarding, runner=self.runner,
        )
        migrated = legacy_state.onboarding()["settings"]
        self.assertEqual(migrated["frontierAuthMode"], "chatgpt")
        self.assertEqual(migrated["frontierBudgetProfile"], "custom")

    def test_frontier_budget_preview_is_custom_only_private_and_cannot_apply(self):
        self.save_default()
        with self.assertRaisesRegex(control_module.PublicRejected, "custom Frontier budgets"):
            self.control.frontier_budget_preview({
                "schemaVersion": 1, "windowSeconds": 172800, "maxJobs": 10,
                "maxInputTokens": 100000, "maxOutputTokens": 20000,
                "maxFailures": 3, "maxEstimatedCostMicros": None,
            })
        policy_path = self.install_custom_frontier_policy()
        proposal = self.control.frontier_budget_preview({
            "schemaVersion": 1, "windowSeconds": 172800, "maxJobs": 10,
            "maxInputTokens": 100000, "maxOutputTokens": 20000,
            "maxFailures": 3, "maxEstimatedCostMicros": None,
        })
        encoded = json.dumps(proposal)
        self.assertNotIn(str(policy_path), encoded)
        self.assertFalse(proposal["browserCanApply"])
        self.assertFalse(proposal["browserCanActivate"])
        self.assertEqual(self.runner.calls, [])
        stored = list((self.state_path / "frontier-budget-proposals").glob("*.json"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(json.loads(stored[0].read_text(encoding="utf-8"))["policyPath"], str(policy_path))

    def test_frontier_budget_http_preview_enforces_normal_mutation_boundary(self):
        self.install_custom_frontier_policy()
        server = control_module.ControlServer(("127.0.0.1", 0), self.control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        host = f"127.0.0.1:{server.server_port}"
        request = json.dumps({
            "schemaVersion": 1, "windowSeconds": 172800, "maxJobs": 10,
            "maxInputTokens": 100000, "maxOutputTokens": 20000,
            "maxFailures": 3, "maxEstimatedCostMicros": None,
        })
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/", headers={"Host": host})
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            response.read()
            connection.request("POST", "/api/v1/frontier-budget/preview", body=request, headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin",
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            proposal = json.loads(response.read())
            self.assertFalse(proposal["browserCanApply"])
            self.assertNotIn(str(self.base), json.dumps(proposal))
            connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("POST", "/api/v1/frontier-budget/preview", body=request, headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Origin": "http://attacker.invalid",
            })
            try:
                response = connection.getresponse()
            except (ConnectionAbortedError, ConnectionResetError):
                if os.name != "nt":
                    raise
            else:
                self.assertEqual(response.status, 403)
                response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_safe_update_preserves_advanced_private_configuration(self):
        private = {
            **control_module.merge_onboarding({}, control_module.default_onboarding()),
            "modelApiKey": "fixture-private-model-credential",
            "frontierCredentialFile": "/secure/private/frontier-key",
            "gatewayExtensions": [{"id": "fixture", "path": "/opt/fixture", "sha256": "a" * 64}],
        }
        control_module.atomic_json(self.onboarding_path, private, 0o600)
        before = self.control.onboarding()
        settings = dict(before["settings"])
        settings["agentName"] = "My Private Pixel"
        saved = self.control.save_onboarding({"schemaVersion": 1, "revision": before["revision"], "settings": settings})
        stored = control_module.read_json(self.onboarding_path, private=True)
        self.assertEqual(stored["modelApiKey"], private["modelApiKey"])
        self.assertEqual(stored["frontierCredentialFile"], private["frontierCredentialFile"])
        self.assertEqual(stored["gatewayExtensions"], private["gatewayExtensions"])
        self.assertEqual(stored["agentName"], "My Private Pixel")
        self.assertNotIn(private["modelApiKey"], json.dumps(saved))

    def test_public_limbs_follow_profile_and_legacy_web_configuration(self):
        minimal = control_module.merge_onboarding({}, control_module.default_onboarding())
        minimal["capabilityProfile"] = "minimal"
        for name in control_module.LIMBS:
            minimal.pop(f"{name}LimbEnabled", None)
        minimal.pop("webCourierEnabled", None)
        control_module.atomic_json(self.onboarding_path, minimal, 0o600)
        self.assertEqual(self.control.onboarding()["settings"]["limbs"], {
            "email": False, "calendar": False, "social": False,
            "web": False, "operations": False, "frontier": False,
        })
        minimal["webCourierEnabled"] = True
        control_module.atomic_json(self.onboarding_path, minimal, 0o600)
        self.assertTrue(self.control.onboarding()["settings"]["limbs"]["web"])
        minimal["webLimbEnabled"] = False
        control_module.atomic_json(self.onboarding_path, minimal, 0o600)
        self.assertFalse(self.control.onboarding()["settings"]["limbs"]["web"])

    def test_control_capability_defaults_match_release_profiles(self):
        for name, expected in control_module.CAPABILITY_LIMBS.items():
            profile = json.loads((ROOT / "profiles" / "capabilities" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["limbs"], expected)

    def test_onboarding_rejects_unknown_or_credential_fields(self):
        current = self.control.onboarding()
        settings = dict(current["settings"])
        settings["modelApiKey"] = "must-not-enter-browser-contract"
        with self.assertRaisesRegex(control_module.Rejected, "unknown fields"):
            self.control.save_onboarding({"schemaVersion": 1, "revision": current["revision"], "settings": settings})

    def test_onboarding_uses_optimistic_revision_and_private_bounded_backups(self):
        first = self.save_default()
        stale = first["revision"]
        changed = dict(first["settings"])
        changed["agentName"] = "Changed once"
        second = self.control.save_onboarding({"schemaVersion": 1, "revision": stale, "settings": changed})
        changed["agentName"] = "Stale overwrite"
        with self.assertRaisesRegex(control_module.Rejected, "changed"):
            self.control.save_onboarding({"schemaVersion": 1, "revision": stale, "settings": changed})
        self.assertNotEqual(stale, second["revision"])
        for index in range(24):
            current = self.control.onboarding()
            value = dict(current["settings"])
            value["agentName"] = f"Pixel revision {index}"
            self.control.save_onboarding({"schemaVersion": 1, "revision": current["revision"], "settings": value})
        self.assertLessEqual(len(list(self.onboarding_path.parent.glob("onboarding.before-control-*.json"))), 20)

    def test_action_is_exact_hash_bound_allowlisted_and_single_use(self):
        self.save_default()
        forged_preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        forged_path = self.control.pending / f"{forged_preview['actionId']}.json"
        forged = control_module.read_json(forged_path, 65536, private=True)
        forged["effect"] = "same-user private-state forgery"
        forged.pop("actionHash")
        forged_hash = control_module.digest(forged)
        forged["actionHash"] = forged_hash
        control_module.atomic_json(forged_path, forged, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "hash does not match"):
            self.control.execute_action({"schemaVersion": 1, "actionId": forged_preview["actionId"], "actionHash": forged_hash})
        self.assertEqual(self.runner.calls, [])
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "configure"})
        result = self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(self.runner.calls), 1)
        command, root, _timeout, environment = self.runner.calls[0]
        self.assertEqual(command[:2], ["node", str(ROOT / "scripts/configure.mjs")])
        self.assertNotEqual(Path(command[3]), self.onboarding_path)
        self.assertFalse(Path(command[3]).exists())
        self.assertEqual(root, ROOT)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("PIXEL_FRONTIER_CREDENTIAL_PATH", environment)
        with self.assertRaises(FileNotFoundError):
            self.control.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })

    def test_owner_private_approval_inbox_is_exact_durable_and_safely_deniable(self):
        self.save_default()
        first = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        second = self.control.preview_action({"schemaVersion": 1, "kind": "configure"})
        inbox = self.control.approvals()
        self.assertEqual(inbox["$schema"], control_module.APPROVAL_INBOX_SCHEMA)
        self.assertEqual(inbox["state"], "ready")
        self.assertEqual([item["actionId"] for item in inbox["approvals"]], sorted([first["actionId"], second["actionId"]]))
        self.assertEqual(inbox["privacy"], {
            "parametersExposed": False, "credentialsExposed": False, "privateLogsExposed": False,
        })
        projected = json.dumps(inbox["approvals"])
        for private_name in ("parameters", "onboardingRevision", "controlPolicyRevision", "deploymentRevision"):
            self.assertNotIn(private_name, projected)

        with self.assertRaisesRegex(control_module.PublicRejected, "does not match"):
            self.control.cancel_action({"schemaVersion": 1, "actionId": first["actionId"], "actionHash": "0" * 64})
        self.assertEqual(len(self.control.approvals()["approvals"]), 2)
        denied = self.control.cancel_action({
            "schemaVersion": 1, "actionId": first["actionId"], "actionHash": first["actionHash"],
        })
        self.assertEqual(denied, {"schemaVersion": 1, "actionId": first["actionId"], "state": "denied"})
        self.assertEqual([item["actionId"] for item in self.control.approvals()["approvals"]], [second["actionId"]])
        self.assertEqual(self.runner.calls, [])
        with self.assertRaises(FileNotFoundError):
            self.control.cancel_action({
                "schemaVersion": 1, "actionId": first["actionId"], "actionHash": first["actionHash"],
            })

        path = self.control.pending / f"{second['actionId']}.json"
        tampered = control_module.read_json(path, 65536, private=True)
        tampered["effect"] = "PRIVATE_TAMPER_CANARY"
        control_module.atomic_json(path, tampered, 0o600)
        with self.assertRaisesRegex(control_module.Rejected, "hash"):
            self.control.approvals()

    def test_fixed_action_permissions_are_exact_policy_bounded_and_auto_executable(self):
        self.save_default()
        projection = self.control.permissions()
        self.assertEqual(projection["$schema"], control_module.PERMISSION_SETTINGS_SCHEMA)
        self.assertEqual(len(projection["settings"]), len(control_module.ACTION_SPECS))
        by_kind = {setting["kind"]: setting for setting in projection["settings"]}
        self.assertEqual(by_kind["verify"]["mode"], "always-ask")
        self.assertEqual(by_kind["verify"]["availableModes"], ["always-ask", "auto-within-policy", "never-allow"])
        self.assertEqual(by_kind["update-check"]["mode"], "never-allow")
        self.assertEqual(by_kind["update-check"]["availableModes"], ["never-allow"])

        invalid_modes = {setting["kind"]: setting["mode"] for setting in projection["settings"]}
        invalid_modes["update-check"] = "always-ask"
        with self.assertRaisesRegex(control_module.PublicRejected, "exceeds"):
            self.control.save_permissions({"schemaVersion": 1, "revision": projection["revision"], "modes": invalid_modes})

        pending = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        modes = {setting["kind"]: setting["mode"] for setting in projection["settings"]}
        modes["verify"] = "auto-within-policy"
        saved = self.control.save_permissions({"schemaVersion": 1, "revision": projection["revision"], "modes": modes})
        self.assertEqual({setting["kind"]: setting["mode"] for setting in saved["settings"]}["verify"], "auto-within-policy")
        self.assertFalse((self.control.pending / f"{pending['actionId']}.json").exists())
        with self.assertRaises(FileNotFoundError):
            self.control.execute_action({"schemaVersion": 1, "actionId": pending["actionId"], "actionHash": pending["actionHash"]})

        automatic = self.control.request_action({"schemaVersion": 1, "kind": "verify"})
        self.assertEqual(automatic["state"], "completed")
        self.assertEqual(automatic["result"]["status"], "succeeded")
        self.assertEqual(self.runner.calls[-1][0], ["bash", str(ROOT / "pixel"), "verify"])
        self.assertEqual(self.control.approvals()["approvals"], [])

        self.install_control_policy(updateCheck=True)
        latest = self.control.permissions()
        self.assertEqual(latest["source"], "policy-changed")
        self.assertEqual({setting["kind"]: setting["mode"] for setting in latest["settings"]}["verify"], "always-ask")
        denied_modes = {setting["kind"]: setting["mode"] for setting in latest["settings"]}
        denied_modes["verify"] = "never-allow"
        self.control.save_permissions({"schemaVersion": 1, "revision": latest["revision"], "modes": denied_modes})
        with self.assertRaisesRegex(control_module.PublicRejected, "owner permission"):
            self.control.request_action({"schemaVersion": 1, "kind": "verify"})

        self.install_control_policy(deepWorkResume=True)
        lifecycle = self.control.permissions()
        lifecycle_modes = {setting["kind"]: setting["mode"] for setting in lifecycle["settings"]}
        lifecycle_modes["deep-work-resume"] = "auto-within-policy"
        with self.assertRaisesRegex(control_module.PublicRejected, "exceeds"):
            self.control.save_permissions({"schemaVersion": 1, "revision": lifecycle["revision"], "modes": lifecycle_modes})

    def test_configure_uses_immutable_private_onboarding_snapshot(self):
        saved = self.save_default()
        captured = {}

        def changing_runner(command, root, timeout_seconds, environment):
            changed = control_module.merge_onboarding({}, control_module.default_onboarding())
            changed["agentName"] = "Externally changed after confirmation"
            control_module.atomic_json(self.onboarding_path, changed, 0o600)
            snapshot = Path(command[3])
            captured["snapshot"] = control_module.read_json(snapshot, private=True)
            return self.runner(command, root, timeout_seconds, environment)

        self.control.runner = changing_runner
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "configure"})
        result = self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(captured["snapshot"]["agentName"], saved["settings"]["agentName"])
        self.assertNotEqual(captured["snapshot"]["agentName"], "Externally changed after confirmation")

    def test_unexpected_runner_failure_is_content_free_and_cleans_claim(self):
        self.save_default()

        def broken_runner(*_args):
            raise RuntimeError("FIXTURE_PRIVATE_RUNNER_DETAIL")

        self.control.runner = broken_runner
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "configure"})
        result = self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("FIXTURE_PRIVATE_RUNNER_DETAIL", json.dumps(result))
        self.assertFalse((self.state_path / f"running-{preview['actionId']}.json").exists())
        self.assertFalse((self.state_path / f"input-{preview['actionId']}.json").exists())
        self.assertIn("FIXTURE_PRIVATE_RUNNER_DETAIL", (self.state_path / "logs" / f"{preview['actionId']}.log").read_text())

    def test_oversized_exception_evidence_is_truncated_before_hash_binding(self):
        self.save_default()

        def broken_runner(*_args):
            raise RuntimeError("PRIVATE_OVERSIZED_DIAGNOSTIC_" * 8)

        self.control.runner = broken_runner
        with mock.patch.object(control_module, "MAX_COMMAND_OUTPUT", 64):
            preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
            result = self.control.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })
            diagnostics = self.control.diagnostics()
        evidence = (self.state_path / "logs" / f"{preview['actionId']}.log").read_bytes()
        self.assertEqual(len(evidence), 64)
        self.assertEqual(result["privateLogSha256"], control_module.hashlib.sha256(evidence).hexdigest())
        self.assertEqual(diagnostics["summary"]["state"], "attention")
        self.assertEqual(diagnostics["incidents"][0]["failureMode"], "execution-error")
        self.assertTrue(diagnostics["incidents"][0]["privateEvidenceAvailable"])

    def test_invalid_runner_result_becomes_a_complete_execution_error_incident(self):
        self.save_default()

        def invalid_runner(*_args):
            return 0, "PRIVATE_NON_BYTE_RUNNER_OUTPUT"

        self.control.runner = invalid_runner
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        result = self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["exitCode"])
        self.assertFalse((self.state_path / f"running-{preview['actionId']}.json").exists())
        diagnostics = self.control.diagnostics()
        self.assertEqual(diagnostics["summary"]["state"], "attention")
        self.assertEqual(diagnostics["incidents"][0]["failureMode"], "execution-error")
        self.assertNotIn("PRIVATE_NON_BYTE_RUNNER_OUTPUT", json.dumps(diagnostics))

    def test_pending_action_queue_is_bounded(self):
        self.save_default()
        original = control_module.MAX_PENDING_ACTIONS
        control_module.MAX_PENDING_ACTIONS = 2
        try:
            self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
            self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
            with self.assertRaisesRegex(control_module.Rejected, "too many pending"):
                self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        finally:
            control_module.MAX_PENDING_ACTIONS = original

    def test_operator_actions_are_private_policy_gated_and_content_free(self):
        status = self.control.status()
        self.assertEqual(status["operatorActions"], {
            "updateCheck": False, "backupCreate": False, "operationsPause": False,
            "frontierPause": False, "deepWorkPause": False, "deepWorkResume": False,
            "deepWorkCancel": False, "deepWorkDraft": False, "deepWorkPrepare": False, "deepWorkStage": False,
            "deepWorkServiceRender": False,
            "resumeInBrowser": False, "restoreInBrowser": False,
        })
        for kind in ("update-check", "backup-create", "operations-pause", "frontier-pause", "deep-work-pause", "deep-work-resume", "deep-work-cancel"):
            value = {"schemaVersion": 1, "kind": kind}
            if kind in ("operations-pause", "frontier-pause"):
                value["reason"] = "Owner-requested containment"
            with self.assertRaisesRegex(control_module.PublicRejected, "disabled"):
                self.control.preview_action(value)

        policy = self.install_control_policy(updateCheck=True, backupCreate=True)
        update = self.control.preview_action({"schemaVersion": 1, "kind": "update-check"})
        update_result = self.control.execute_action({
            "schemaVersion": 1, "actionId": update["actionId"], "actionHash": update["actionHash"],
        })
        self.assertEqual(update_result["status"], "succeeded")
        self.assertEqual(self.runner.calls[-1][0], ["bash", str(ROOT / "pixel"), "upstream", "check"])

        backup = self.control.preview_action({"schemaVersion": 1, "kind": "backup-create"})
        self.assertNotIn(policy["backup"]["directory"], json.dumps(backup))
        self.assertNotIn(policy["backup"]["ageRecipient"], json.dumps(backup))
        backup_result = self.control.execute_action({
            "schemaVersion": 1, "actionId": backup["actionId"], "actionHash": backup["actionHash"],
        })
        self.assertEqual(backup_result["status"], "succeeded")
        self.assertEqual(self.runner.calls[-1][0], [
            "bash", str(ROOT / "pixel"), "backup", policy["backup"]["directory"], policy["backup"]["ageRecipient"],
        ])
        self.assertEqual(self.runner.calls[-1][3]["PIXEL_CONTROL_POLICY_PATH"], str(self.state_path / "policy.json"))
        self.assertNotIn(policy["backup"]["directory"], json.dumps(self.control.status()))

        stale = self.control.preview_action({"schemaVersion": 1, "kind": "update-check"})
        policy["actions"]["updateCheck"] = False
        control_module.atomic_json(self.state_path / "policy.json", policy, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "policy changed"):
            self.control.execute_action({
                "schemaVersion": 1, "actionId": stale["actionId"], "actionHash": stale["actionHash"],
            })

    def test_emergency_pause_is_reason_bound_one_way_and_limb_gated(self):
        fake_root = self.base / "root"
        (fake_root / ".generated").mkdir(parents=True)
        (fake_root / "VERSION").write_text("3.3.0\n", encoding="utf-8")
        (fake_root / "pixel").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        control_module.atomic_json(fake_root / ".generated" / "deployment.json", {
            "limbs": {"operations": True, "frontier": True},
        }, 0o600)
        pause_state = self.base / "pause-state"
        pause = control_module.ControlState(fake_root, pause_state, self.onboarding_path, runner=self.runner)
        policy = control_module.default_control_policy()
        policy["actions"]["operationsPause"] = True
        policy["actions"]["frontierPause"] = True
        control_module.atomic_json(pause_state / "policy.json", policy, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "requires one reason"):
            pause.preview_action({"schemaVersion": 1, "kind": "operations-pause"})
        with self.assertRaisesRegex(control_module.PublicRejected, "requires one reason"):
            pause.preview_action({"schemaVersion": 1, "kind": "operations-pause", "reason": "contain", "path": "/tmp"})
        for kind, command in (("operations-pause", "ops-pause"), ("frontier-pause", "frontier-pause")):
            preview = pause.preview_action({"schemaVersion": 1, "kind": kind, "reason": "Owner-requested containment"})
            self.assertIn("Owner-requested containment", preview["effect"])
            result = pause.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(self.runner.calls[-1][0], [
                "bash", str(fake_root / "pixel"), command, "Owner-requested containment", "--confirm",
            ])
        self.assertFalse(pause.status()["operatorActions"]["resumeInBrowser"])

    def test_deep_work_pause_is_fixed_config_bound_one_way_and_stale_safe(self):
        now = self.control.now()
        status_path = self.base / "private" / "deep-work-status.json"
        config_path = self.base / "private" / "deep-work-controller.json"
        control_module.atomic_json(status_path, self.work_operator_status(now), 0o600)
        control_module.atomic_json(config_path, {"schemaVersion": 1, "privateGoal": "PRIVATE GOAL CONFIGURATION"}, 0o600)
        pause_state = self.base / "deep-work-pause-state"
        pause = control_module.ControlState(
            ROOT, pause_state, self.onboarding_path, runner=self.runner, now=lambda: now,
            work_status_path=status_path, work_controller_config_path=config_path,
        )
        policy = control_module.default_control_policy()
        policy["actions"]["deepWorkPause"] = True
        control_module.atomic_json(pause_state / "policy.json", policy, 0o600)
        self.assertTrue(pause.status()["operatorActions"]["deepWorkPause"])
        with self.assertRaisesRegex(control_module.PublicRejected, "shape"):
            pause.preview_action({"schemaVersion": 1, "kind": "deep-work-pause", "reason": "PRIVATE REASON"})

        preview = pause.preview_action({"schemaVersion": 1, "kind": "deep-work-pause"})
        self.assertIn("bounded step that already started may finish", preview["effect"])
        self.assertNotIn(str(config_path), json.dumps(preview))
        self.assertNotIn("PRIVATE GOAL CONFIGURATION", json.dumps(preview))
        control_module.atomic_json(config_path, {"schemaVersion": 1, "privateGoal": "CHANGED PRIVATE GOAL"}, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "configuration changed"):
            pause.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })

        captured_snapshot = {}
        def snapshot_runner(command, root, timeout_seconds, environment):
            captured_snapshot["payload"] = Path(command[-1]).read_bytes()
            return self.runner(command, root, timeout_seconds, environment)
        pause.runner = snapshot_runner
        preview = pause.preview_action({"schemaVersion": 1, "kind": "deep-work-pause"})
        result = pause.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(result["status"], "succeeded")
        expected_snapshot = pause_state / f"input-{preview['actionId']}.json"
        self.assertEqual(self.runner.calls[-1][0], [
            "node", str(ROOT / "deploy" / "work-controller" / "goal-pause-cli.mjs"),
            "--config", str(expected_snapshot),
        ])
        self.assertEqual(captured_snapshot["payload"], config_path.read_bytes())
        self.assertFalse(expected_snapshot.exists())
        self.assertNotIn(str(config_path), json.dumps(pause.status()))
        self.assertFalse(pause.status()["operatorActions"]["resumeInBrowser"])

        self.runner.returncode = 1
        failed_preview = pause.preview_action({"schemaVersion": 1, "kind": "deep-work-pause"})
        failed = pause.execute_action({
            "schemaVersion": 1, "actionId": failed_preview["actionId"], "actionHash": failed_preview["actionHash"],
        })
        self.assertEqual(failed["status"], "failed")
        diagnostics = pause.diagnostics()
        self.assertEqual(diagnostics["summary"]["state"], "containment")
        self.assertEqual(diagnostics["incidents"][0]["category"], "deep-work-containment")
        self.assertEqual(diagnostics["incidents"][0]["nextActionCode"], "contain-deep-work-from-terminal")
        self.runner.returncode = 0

        paused = self.work_operator_status(now)
        paused["controllerState"] = "ready"
        paused["goal"]["state"] = "paused"
        paused["goal"]["nextAction"] = "paused"
        control_module.atomic_json(status_path, paused, 0o600)
        self.assertFalse(pause.status()["operatorActions"]["deepWorkPause"])
        with self.assertRaisesRegex(control_module.PublicRejected, "schedulable"):
            pause.preview_action({"schemaVersion": 1, "kind": "deep-work-pause"})

    def test_deep_work_lifecycle_reviews_are_exact_and_fail_content_free(self):
        config_path = self.base / "private" / "PRIVATE-LIFECYCLE-CONFIG.json"
        resume = {
            "schemaVersion": 1, "operation": "pixel-work-goal-resume-review",
            "status": "paused", "action": "confirmation-required",
            "checkpointSha256": "a" * 64, "sequence": 7,
            "transition": {"from": "paused", "to": "running", "preservesExactActiveChild": True},
            "confirmation": {"option": "--confirm-review-sha256", "sha256": "b" * 64},
            "schedulingEffect": "none-until-confirmed", "startsWorkImmediately": False,
            "authority": {
                "grantsExecution": False, "grantsLease": False, "grantsRetry": False,
                "grantsCancellation": False, "grantsScopeExpansion": False,
                "grantsExternalEffects": False, "grantsCompletion": False,
            },
            "boundary": control_module.WORK_RESUME_BOUNDARY,
        }
        cancel = {
            "schemaVersion": 1, "operation": "pixel-work-goal-cancel-review",
            "status": "running", "action": "confirmation-required",
            "goalCheckpointSha256": "c" * 64, "sequence": 8,
            "cancellation": {
                "mode": "supervised-child-after-cleanup", "recordsTerminalState": True,
                "childState": "failed",
            },
            "confirmation": {"option": "--confirm-review-sha256", "sha256": "d" * 64},
            "schedulingEffect": "none-until-confirmed", "stopsWorker": False,
            "authority": {
                "grantsExecution": False, "grantsLease": False, "grantsReplay": False,
                "grantsWorkerStop": False, "grantsScopeExpansion": False,
                "grantsExternalEffects": False, "grantsCompletion": False,
            },
            "boundary": control_module.WORK_CANCEL_BOUNDARY,
        }

        for kind, review, expected in (
            ("deep-work-resume", resume, {"reviewSha256": "b" * 64, "mode": "running"}),
            ("deep-work-cancel", cancel, {"reviewSha256": "d" * 64, "mode": "supervised-child-after-cleanup"}),
        ):
            self.runner.output = json.dumps(review).encode("utf-8")
            self.assertEqual(self.control._deep_work_lifecycle_review(kind, config_path), expected)
            self.assertEqual(self.runner.calls[-1][0], [
                "node", str(ROOT / "deploy" / "work-controller" / (
                    "goal-resume-cli.mjs" if kind == "deep-work-resume" else "goal-cancel-cli.mjs"
                )), "review", "--config", str(config_path),
            ])

        hostile_reviews = []
        widened_resume = copy.deepcopy(resume)
        widened_resume["authority"]["grantsExecution"] = True
        hostile_reviews.append(("deep-work-resume", widened_resume))
        launching_resume = copy.deepcopy(resume)
        launching_resume["startsWorkImmediately"] = True
        hostile_reviews.append(("deep-work-resume", launching_resume))
        stopping_cancel = copy.deepcopy(cancel)
        stopping_cancel["stopsWorker"] = True
        hostile_reviews.append(("deep-work-cancel", stopping_cancel))
        unclean_cancel = copy.deepcopy(cancel)
        unclean_cancel["cancellation"]["childState"] = "running"
        hostile_reviews.append(("deep-work-cancel", unclean_cancel))
        extra_field = copy.deepcopy(cancel)
        extra_field["private"] = "PRIVATE REVIEW CANARY"
        hostile_reviews.append(("deep-work-cancel", extra_field))
        for kind, review in hostile_reviews:
            self.runner.output = json.dumps(review).encode("utf-8")
            with self.assertRaises(control_module.PublicRejected) as rejected:
                self.control._deep_work_lifecycle_review(kind, config_path)
            self.assertNotIn("PRIVATE", str(rejected.exception))
            self.assertNotIn(str(config_path), str(rejected.exception))

    def test_deep_work_authoring_uses_opaque_inputs_and_creates_only_an_inert_private_draft(self):
        authoring, config, _policy, catalog, catalog_path, drafts = self.work_authoring_control()
        projection = authoring.work_authoring()
        self.assertEqual(projection["state"], "ready")
        self.assertEqual([profile["kind"] for profile in projection["profiles"] if profile["enabled"]], ["inspect", "build", "research", "analyze-data"])
        self.assertEqual(len(projection["inputs"]), 1)
        input_handle = projection["inputs"][0]["handle"]
        self.assertRegex(input_handle, r"^workinput-[a-f0-9]{24}$")
        public_projection = json.dumps(projection)
        for private_value in ("project", "a" * 64, str(catalog_path), config["objectStoreDirectory"], config["draftDirectory"]):
            self.assertNotIn(private_value, public_projection)
        self.assertFalse(projection["privacy"]["browserCanAdmitInputs"])
        self.assertFalse(projection["privacy"]["browserCanExecute"])
        request = {
            "schemaVersion": 1, "kind": "deep-work-draft", "authoringRevision": projection["revision"],
            "objective": "PRIVATE AUTHORING GOAL CANARY\nAcross restarts", "dataClassification": "internal",
            "milestones": [{
                "kind": "inspect", "objective": "PRIVATE AUTHORING STEP CANARY\nWith durable evidence",
                "doneWhen": ["Return an independently checked finding report"],
                "inputHandles": [input_handle], "effort": "standard",
            }],
        }
        preview = authoring.preview_action(request)
        self.assertEqual(preview["kind"], "deep-work-draft")
        self.assertIn("does not compile, stage, schedule, execute", preview["effect"])
        self.assertNotIn("PRIVATE AUTHORING", json.dumps(preview))
        pending = control_module.read_json(authoring.pending / f"{preview['actionId']}.json", 65536, private=True)
        self.assertEqual(pending["parameters"]["brief"]["milestones"][0]["inputIds"], ["project"])
        self.assertEqual(pending["parameters"]["brief"]["milestones"][0]["dependsOn"], [])

        stale_catalog = copy.deepcopy(catalog)
        stale_catalog["entries"][0]["bytes"] += 1
        control_module.atomic_json(catalog_path, stale_catalog, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "choices changed"):
            authoring.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]})
        control_module.atomic_json(catalog_path, catalog, 0o600)

        captured = {}
        def draft_runner(command, root, timeout_seconds, environment):
            control_module.atomic_json(Path(config["policyFile"]), {"tampered": True}, 0o600)
            control_module.atomic_json(catalog_path, {"tampered": True}, 0o600)
            captured["brief"] = json.loads(Path(command[command.index("--brief") + 1]).read_text(encoding="utf-8"))
            captured["policy"] = json.loads(Path(command[command.index("--policy") + 1]).read_text(encoding="utf-8"))
            captured["catalog"] = json.loads(Path(command[command.index("--input-catalog") + 1]).read_text(encoding="utf-8"))
            return self.runner(command, root, timeout_seconds, environment)
        authoring.runner = draft_runner
        projection = authoring.work_authoring()
        request["authoringRevision"] = projection["revision"]
        request["milestones"][0]["inputHandles"] = [projection["inputs"][0]["handle"]]
        preview = authoring.preview_action(request)
        result = authoring.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]})
        self.assertEqual(result["status"], "succeeded")
        command = self.runner.calls[-1][0]
        self.assertEqual(command[:2], ["node", str(ROOT / "deploy" / "work-controller" / "goal-draft-cli.mjs")])
        self.assertEqual(command[command.index("--policy") + 1], str(authoring.state / f"policy-{preview['actionId']}.json"))
        self.assertEqual(command[command.index("--input-catalog") + 1], str(authoring.state / f"catalog-{preview['actionId']}.json"))
        self.assertEqual(command[command.index("--object-store") + 1], config["objectStoreDirectory"])
        self.assertEqual(command[command.index("--output") + 1], str(drafts / preview["actionId"]))
        self.assertEqual(captured["brief"]["objective"], "PRIVATE AUTHORING GOAL CANARY\nAcross restarts")
        self.assertEqual(captured["policy"], _policy)
        self.assertEqual(captured["catalog"], catalog)
        for prefix in ("input", "policy", "catalog"):
            self.assertFalse((authoring.state / f"{prefix}-{preview['actionId']}.json").exists())
        public_evidence = json.dumps({"projection": authoring.work_authoring(), "preview": preview, "result": result, "status": authoring.status()})
        self.assertNotIn("PRIVATE AUTHORING", public_evidence)
        self.assertNotIn(str(self.base), public_evidence)

    def test_chat_to_deep_work_handoff_binds_one_settled_turn_to_one_exact_inert_draft(self):
        authoring, config, _policy, _catalog, _catalog_path, drafts = self.work_authoring_control()
        self.configure_chat_runtime()
        authoring.runner = RecordingRunner(output=json.dumps({
            "status": "ok", "result": {"payloads": [{"text": "This needs a durable local work plan."}], "meta": chat_meta()},
        }).encode("utf-8"))
        chat = authoring.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "9" * 32,
            "conversationHandle": None, "message": "Repair the private project and prove every change.",
        })
        source_task = chat["conversations"][0]["turns"][0]
        self.assertIsNone(source_task["handoff"])

        projection = authoring.work_authoring()
        request = {
            "schemaVersion": 1, "kind": "deep-work-draft", "authoringRevision": projection["revision"],
            "chatTaskHandle": source_task["taskHandle"],
            "objective": "Repair the private project and prove every change.", "dataClassification": "internal",
            "milestones": [{
                "kind": "inspect", "objective": "Inspect the admitted project",
                "doneWhen": ["Return independently checked findings"],
                "inputHandles": [projection["inputs"][0]["handle"]], "effort": "standard",
            }],
        }
        preview = authoring.preview_action(request)
        self.assertIn("immutable receipt", preview["effect"])
        self.assertNotIn(source_task["taskHandle"], json.dumps(preview))
        pending = control_module.read_json(authoring.pending / f"{preview['actionId']}.json", 65536, private=True)
        conversation_path = next(authoring.chat_conversations.glob("*.json"))
        conversation_record = json.loads(conversation_path.read_text(encoding="utf-8"))
        self.assertEqual(pending["parameters"]["handoff"]["turnRecordSha256"], conversation_record["turns"][0]["recordSha256"])
        substituted = copy.deepcopy(conversation_record)
        substituted["turns"][0]["userText"] = "Substituted private goal after preview."
        substituted["turns"][0]["recordSha256"] = control_module.digest({
            key: value for key, value in substituted["turns"][0].items() if key != "recordSha256"
        })
        control_module.atomic_json(conversation_path, substituted, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "source Pixel chat task changed"):
            authoring.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })
        control_module.atomic_json(conversation_path, conversation_record, 0o600)

        def draft_runner(command, _root, _timeout_seconds, _environment):
            brief = json.loads(Path(command[command.index("--brief") + 1]).read_text(encoding="utf-8"))
            policy = json.loads(Path(command[command.index("--policy") + 1]).read_text(encoding="utf-8"))
            catalog = json.loads(Path(command[command.index("--input-catalog") + 1]).read_text(encoding="utf-8"))
            output = Path(command[command.index("--output") + 1])
            output.mkdir(mode=0o700)
            (output / "input-manifests").mkdir(mode=0o700)
            declaration = {
                "$schema": control_module.WORK_GOAL_DECLARATION_SCHEMA, "schemaVersion": 1,
                "objective": brief["objective"], "dataClassification": brief["dataClassification"],
                "milestones": [{
                    "milestoneId": "step-1", "jobId": "work-1786550000000-aaaaaaaaaaaa", "dependsOn": [],
                }],
                "boundary": control_module.WORK_GOAL_DECLARATION_BOUNDARY,
            }
            budgets = {
                "maxRuntimeSeconds": 3600, "maxIterations": 8, "maxToolCalls": 200, "maxConcurrentSubagents": 2,
                "maxModelRequests": 40, "maxInputTokens": 200000, "maxOutputTokens": 40000,
                "maxCpuCores": 4, "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240,
                "maxArtifactBytes": 1073741824, "maxNetworkBytes": 0, "maxFailures": 4, "noProgressLimit": 3,
            }
            review = {
                "$schema": control_module.WORK_GOAL_DRAFT_SCHEMA, "schemaVersion": 1,
                "operation": "pixel-work-goal-draft", "draftId": "workdraft-1786550000000-bbbbbbbbbbbb",
                "createdAt": control_module.iso(authoring.now()),
                "goal": {"objective": brief["objective"], "dataClassification": brief["dataClassification"], "milestones": 1},
                "milestones": [{
                    "milestoneId": "step-1", "jobId": "work-1786550000000-aaaaaaaaaaaa", "profile": "scout",
                    "objective": brief["milestones"][0]["objective"], "doneWhen": brief["milestones"][0]["doneWhen"],
                    "dependsOn": [], "inputIds": ["project"], "effort": "standard", "budgets": budgets,
                    "capabilities": {"workspace": "read-only-workspace", "tools": ["read", "search"], "brokeredServices": ["local-model"], "modelRoute": "local-only"},
                    "verification": "independent-report-verification", "externalEffects": False,
                }],
                "bindings": {
                    "briefSha256": control_module.digest(brief), "policySha256": control_module.digest(policy),
                    "inputCatalogSha256": control_module.digest(catalog),
                    "declarationSha256": control_module.digest(declaration), "jobsSha256": "d" * 64,
                    "inputManifestsSha256": "e" * 64,
                },
                "authority": {
                    "grantsExecution": False, "grantsLease": False, "grantsRetry": False,
                    "grantsScheduling": False, "grantsCredentials": False, "grantsScopeExpansion": False,
                    "grantsExternalEffects": False, "grantsCompletion": False,
                },
                "nextStep": "Review this private draft. Compile its exact jobs and prepare its goal separately; staging and scheduling still require later explicit authority.",
                "boundary": "Private reviewable long-goal draft only. It derives least-authority local job requests from owner choices and private policy, but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.",
            }
            for name, value in (("goal-declaration.json", declaration), ("goal-draft.json", review), ("jobs.json", [])):
                control_module.atomic_json(output / name, value, 0o600)
            return 0, b'{"schemaVersion":1,"status":"drafted"}'

        authoring.runner = draft_runner
        result = authoring.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(result["status"], "succeeded")
        projected_turn = authoring.chat()["conversations"][0]["turns"][0]
        handoff = projected_turn["handoff"]
        self.assertEqual(handoff["state"], "draft-created")
        self.assertEqual(handoff["milestoneCount"], 1)
        self.assertEqual(handoff["profiles"], ["scout"])
        self.assertFalse(handoff["authority"]["grantsExecution"])
        self.assertEqual(handoff["draftHandle"], authoring.work_draft_reviews()["drafts"][0]["handle"])
        public = json.dumps(handoff)
        for private_value in ("conversation-", '"turnId"', "workdraft-", "goalDeclarationSha256", "receiptSha256"):
            self.assertNotIn(private_value, public)
        receipts = list(authoring.chat_handoffs.glob("handoff-*.json"))
        self.assertEqual(len(receipts), 1)
        private_receipt = control_module.read_json(receipts[0], 65536, private=True)
        control_module.validate_chat_handoff_receipt(private_receipt, private_receipt["handoffId"])
        with self.assertRaisesRegex(control_module.PublicRejected, "already has"):
            authoring.preview_action(request)

        receipts[0].unlink()
        (authoring.results / f"{preview['actionId']}.json").unlink()
        control_module.atomic_json(authoring.state / f"running-{preview['actionId']}.json", pending, 0o600)
        crash_snapshots = {
            "input": pending["parameters"]["brief"], "policy": _policy, "catalog": _catalog,
        }
        for prefix, value in crash_snapshots.items():
            control_module.atomic_json(authoring.state / f"{prefix}-{preview['actionId']}.json", value, 0o600)
        recovered = control_module.ControlState(
            ROOT, authoring.state, self.onboarding_path, runner=RecordingRunner(),
            work_authoring_config_path=authoring.work_authoring_config_path,
        )
        recovered_result = recovered.action_result(preview["actionId"])
        self.assertEqual(recovered_result["status"], "failed")
        self.assertIn("interrupted", recovered_result["message"])
        recovered_handoff = recovered.chat()["conversations"][0]["turns"][0]["handoff"]
        self.assertEqual(recovered_handoff["targetState"], "retained")
        self.assertIsNotNone(recovered_handoff["draftHandle"])
        for prefix in crash_snapshots:
            self.assertFalse((authoring.state / f"{prefix}-{preview['actionId']}.json").exists())
        private_receipt = control_module.read_json(receipts[0], 65536, private=True)

        restarted = control_module.ControlState(
            ROOT, authoring.state, self.onboarding_path, runner=RecordingRunner(),
            work_authoring_config_path=authoring.work_authoring_config_path,
        )
        restarted_turn = restarted.chat()["conversations"][0]["turns"][0]
        self.assertIsNotNone(restarted_turn["handoff"])
        restarted_reviews = restarted.work_draft_reviews()
        self.assertEqual(restarted_turn["handoff"]["draftHandle"], restarted_reviews["drafts"][0]["handle"])
        self.assertNotEqual(restarted_turn["taskHandle"], source_task["taskHandle"])
        restarted_authoring = restarted.work_authoring()
        stale_request = copy.deepcopy(request)
        stale_request["authoringRevision"] = restarted_authoring["revision"]
        stale_request["milestones"][0]["inputHandles"] = [restarted_authoring["inputs"][0]["handle"]]
        with self.assertRaisesRegex(control_module.PublicRejected, "selection changed"):
            restarted.preview_action(stale_request)

        changed_catalog = copy.deepcopy(_catalog)
        changed_catalog["entries"][0]["bytes"] += 1
        control_module.atomic_json(_catalog_path, changed_catalog, 0o600)
        changed_target = restarted.chat()["conversations"][0]["turns"][0]["handoff"]
        self.assertEqual(changed_target["targetState"], "configuration-changed")
        self.assertIsNone(changed_target["draftHandle"])
        control_module.atomic_json(_catalog_path, _catalog, 0o600)

        substituted_target = copy.deepcopy(private_receipt)
        substituted_target["target"]["goalDeclarationSha256"] = "f" * 64
        substituted_target["receiptSha256"] = control_module.digest({
            key: value for key, value in substituted_target.items() if key != "receiptSha256"
        })
        control_module.atomic_json(receipts[0], substituted_target, 0o600)
        unavailable_target = authoring.chat()["conversations"][0]["turns"][0]["handoff"]
        self.assertEqual(unavailable_target["targetState"], "unavailable")
        self.assertIsNone(unavailable_target["draftHandle"])

        hostile = copy.deepcopy(private_receipt)
        hostile["authority"]["grantsExecution"] = True
        hostile["receiptSha256"] = control_module.digest({key: value for key, value in hostile.items() if key != "receiptSha256"})
        control_module.atomic_json(receipts[0], hostile, 0o600)
        self.assertEqual(authoring.chat()["state"], "unavailable")
        self.assertEqual(authoring.chat()["conversations"], [])

    def test_chat_handoff_receipt_failure_cannot_be_reported_as_success(self):
        authoring, _config, _policy, _catalog, _catalog_path, _drafts = self.work_authoring_control()
        self.configure_chat_runtime()
        authoring.runner = RecordingRunner(output=json.dumps({
            "status": "ok", "result": {"payloads": [{"text": "Prepare durable work."}], "meta": chat_meta()},
        }).encode("utf-8"))
        chat = authoring.chat_turn({
            "schemaVersion": 1, "requestId": "chatreq-" + "8" * 32,
            "conversationHandle": None, "message": "Prepare durable work.",
        })
        projection = authoring.work_authoring()
        request = {
            "schemaVersion": 1, "kind": "deep-work-draft", "authoringRevision": projection["revision"],
            "chatTaskHandle": chat["conversations"][0]["turns"][0]["taskHandle"],
            "objective": "Prepare durable work.", "dataClassification": "internal",
            "milestones": [{
                "kind": "inspect", "objective": "Inspect the admitted project",
                "doneWhen": ["Return independently checked findings"],
                "inputHandles": [projection["inputs"][0]["handle"]], "effort": "standard",
            }],
        }
        preview = authoring.preview_action(request)
        authoring.runner = RecordingRunner(returncode=0, output=b'{"status":"drafted"}')
        with mock.patch.object(authoring, "_create_chat_handoff", side_effect=OSError("receipt custody failed")):
            result = authoring.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["exitCode"])
        self.assertIsNone(authoring.chat()["conversations"][0]["turns"][0]["handoff"])

    def test_deep_work_authoring_rejects_smuggling_private_research_stale_handles_and_missing_data(self):
        authoring, _config, _policy, _catalog, _catalog_path, _drafts = self.work_authoring_control()
        projection = authoring.work_authoring()
        handle = projection["inputs"][0]["handle"]
        base = {
            "schemaVersion": 1, "kind": "deep-work-draft", "authoringRevision": projection["revision"],
            "objective": "Bounded goal", "dataClassification": "internal",
            "milestones": [{"kind": "inspect", "objective": "Inspect", "doneWhen": ["Return evidence"], "inputHandles": [handle], "effort": "quick"}],
        }
        for mutation, message in (
            ({**base, "path": str(self.base)}, "shape"),
            ({**base, "milestones": [{**base["milestones"][0], "inputHandles": ["workinput-" + "0" * 24]}]}, "invalid or stale"),
            ({**base, "milestones": [{"kind": "research", "objective": "Research", "doneWhen": ["Return citations"], "inputHandles": [], "effort": "quick", "research": {"allowedDomains": [], "deniedDomains": [], "sourceTypes": ["web"]}}]}, "public research"),
            ({**base, "milestones": [{"kind": "analyze-data", "objective": "Analyze", "doneWhen": ["Return replayed output"], "inputHandles": [handle], "effort": "quick"}]}, "admitted dataset"),
        ):
            with self.assertRaisesRegex(control_module.PublicRejected, message):
                authoring.preview_action(mutation)
        self.assertEqual(list(authoring.pending.glob("*.json")), [])
        preview = authoring.preview_action(base)
        pending_path = authoring.pending / f"{preview['actionId']}.json"
        tampered = control_module.read_json(pending_path, 65536, private=True)
        tampered["parameters"]["brief"]["milestones"][0]["dependsOn"] = ["step-1"]
        tampered.pop("actionHash")
        tampered["actionHash"] = authoring._revision(control_module.canonical(tampered))
        control_module.atomic_json(pending_path, tampered, 0o600)
        with self.assertRaisesRegex(control_module.Rejected, "dependencies|server-derived structure"):
            authoring.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": tampered["actionHash"]})
        self.assertEqual(self.runner.calls, [])
        unsafe_catalog = copy.deepcopy(_catalog)
        unsafe_catalog["entries"][0]["kind"] = "dataset"
        unsafe_catalog["entries"][0]["datasets"] = [{
            "datasetId": "escape", "relativePath": "../private.csv", "format": "csv",
            "contentSha256": "b" * 64, "bytes": 32,
        }]
        control_module.atomic_json(_catalog_path, unsafe_catalog, 0o600)
        self.assertEqual(authoring.work_authoring()["state"], "unavailable")

    def test_deep_work_authoring_derives_a_bounded_branch_and_converge_graph(self):
        authoring, _config, _policy, _catalog, _catalog_path, _drafts = self.work_authoring_control()
        projection = authoring.work_authoring()
        handle = projection["inputs"][0]["handle"]
        milestone = lambda objective, dependencies: {
            "kind": "inspect", "objective": objective, "doneWhen": [f"Return independent evidence for {objective}"],
            "dependsOn": dependencies, "inputHandles": [handle], "effort": "quick",
        }
        request = {
            "schemaVersion": 1, "kind": "deep-work-draft", "authoringRevision": projection["revision"],
            "objective": "Investigate two independent paths and reconcile them", "dataClassification": "internal",
            "milestones": [
                milestone("Establish the baseline", []),
                milestone("Investigate path A", [1]),
                milestone("Investigate path B", [1]),
                milestone("Reconcile both paths", [2, 3]),
            ],
        }
        preview = authoring.preview_action(request)
        pending = control_module.read_json(authoring.pending / f"{preview['actionId']}.json", 65536, private=True)
        self.assertEqual(
            [entry["dependsOn"] for entry in pending["parameters"]["brief"]["milestones"]],
            [[], ["step-1"], ["step-1"], ["step-2", "step-3"]],
        )
        self.assertIn("4 milestones, 4 dependency links, and 1 starting milestone", preview["effect"])

        for dependencies in ([2], [1, 1], [2, 1], [True], [{}]):
            invalid = copy.deepcopy(request)
            invalid["milestones"][1]["dependsOn"] = dependencies
            with self.assertRaisesRegex(control_module.PublicRejected, "unique earlier milestone numbers"):
                authoring.preview_action(invalid)

    def test_deep_work_authoring_retention_and_private_state_fail_closed(self):
        authoring, config, _policy, _catalog, _catalog_path, drafts = self.work_authoring_control(max_drafts=1)
        unexpected = drafts / "unexpected"
        unexpected.mkdir(mode=0o700)
        self.assertEqual(authoring.work_authoring()["state"], "unavailable")
        unexpected.rmdir()
        (drafts / "control-1786424400000-abcdef123456").mkdir(mode=0o700)
        projection = authoring.work_authoring()
        self.assertEqual(projection["state"], "unavailable")
        self.assertEqual(projection["inputs"], [])
        self.assertIsNone(projection["revision"])
        private = json.loads(Path(authoring.work_authoring_config_path).read_text(encoding="utf-8"))
        private["draftDirectory"] = config["objectStoreDirectory"]
        control_module.atomic_json(authoring.work_authoring_config_path, private, 0o600)
        self.assertEqual(authoring.work_authoring()["state"], "unavailable")

    def test_deep_work_draft_reviews_project_exact_retained_work_without_paths_or_input_identities(self):
        authoring, _config, _policy, _catalog, _catalog_path, drafts = self.work_authoring_control()
        directory = drafts / "control-1786510800000-abcdef123456"
        control_module.ensure_directory(directory)
        control_module.ensure_directory(directory / "input-manifests")
        budgets = {
            "maxRuntimeSeconds": 3600, "maxIterations": 8, "maxToolCalls": 200, "maxConcurrentSubagents": 2,
            "maxModelRequests": 40, "maxInputTokens": 200000, "maxOutputTokens": 40000, "maxCpuCores": 4,
            "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240, "maxArtifactBytes": 1073741824,
            "maxNetworkBytes": 0, "maxFailures": 4, "noProgressLimit": 3,
        }
        authority = {
            "grantsExecution": False, "grantsLease": False, "grantsRetry": False, "grantsScheduling": False,
            "grantsCredentials": False, "grantsScopeExpansion": False, "grantsExternalEffects": False, "grantsCompletion": False,
        }
        review = {
            "$schema": control_module.WORK_GOAL_DRAFT_SCHEMA, "schemaVersion": 1, "operation": "pixel-work-goal-draft",
            "draftId": "workdraft-1786510800000-fedcba654321", "createdAt": "2026-08-12T05:00:00.000Z",
            "goal": {"objective": "PRIVATE RETAINED OWNER GOAL", "dataClassification": "internal", "milestones": 2},
            "milestones": [
                {
                    "milestoneId": "inspect", "jobId": "work-1786510800000-aaaaaaaaaaaa", "profile": "scout",
                    "objective": "PRIVATE RETAINED INSPECTION", "doneWhen": ["Return independently checked findings"],
                    "dependsOn": [], "inputIds": ["project"], "effort": "standard", "budgets": budgets,
                    "capabilities": {"workspace": "read-only-workspace", "tools": ["read", "search"], "brokeredServices": ["local-model"], "modelRoute": "local-only"},
                    "verification": "independent-report-verification", "externalEffects": False,
                },
                {
                    "milestoneId": "repair", "jobId": "work-1786510800000-bbbbbbbbbbbb", "profile": "builder",
                    "objective": "PRIVATE RETAINED REPAIR", "doneWhen": ["All declared tests pass"],
                    "dependsOn": ["inspect"], "inputIds": ["project"], "effort": "deep", "budgets": budgets,
                    "capabilities": {"workspace": "disposable-read-write", "tools": ["read", "search", "patch", "test"], "brokeredServices": ["local-model"], "modelRoute": "local-only"},
                    "verification": "patch-integrity-and-semantic-review", "externalEffects": False,
                },
            ],
            "bindings": {name: character * 64 for name, character in zip(("briefSha256", "policySha256", "inputCatalogSha256", "declarationSha256", "jobsSha256", "inputManifestsSha256"), "abcdef")},
            "authority": authority,
            "nextStep": "Review this private draft. Compile its exact jobs and prepare its goal separately; staging and scheduling still require later explicit authority.",
            "boundary": "Private reviewable long-goal draft only. It derives least-authority local job requests from owner choices and private policy, but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.",
        }
        declaration = {
            "$schema": control_module.WORK_GOAL_DECLARATION_SCHEMA, "schemaVersion": 1,
            "objective": review["goal"]["objective"], "dataClassification": review["goal"]["dataClassification"],
            "milestones": [{
                "milestoneId": milestone["milestoneId"], "jobId": milestone["jobId"], "dependsOn": milestone["dependsOn"],
            } for milestone in review["milestones"]],
            "boundary": control_module.WORK_GOAL_DECLARATION_BOUNDARY,
        }
        review["bindings"]["declarationSha256"] = control_module.digest(declaration)
        for name, value in (("goal-declaration.json", declaration), ("jobs.json", []), ("goal-draft.json", review)):
            control_module.atomic_json(directory / name, value, 0o600)
        projection = authoring.work_draft_reviews()
        self.assertEqual(projection["state"], "ready")
        self.assertEqual(len(projection["drafts"]), 1)
        projected = projection["drafts"][0]
        self.assertEqual(projected["objective"], "PRIVATE RETAINED OWNER GOAL")
        self.assertEqual([item["dependsOn"] for item in projected["milestones"]], [[], [1]])
        self.assertEqual(projected["reviewSha256"], hashlib.sha256(control_module.canonical(review)).hexdigest())
        self.assertFalse(projected["authority"]["grantsExecution"])
        encoded = json.dumps(projection)
        self.assertNotIn("project", encoded)
        self.assertNotIn(str(self.base), encoded)
        self.assertNotIn("goal-draft.json", encoded)
        hostile = copy.deepcopy(review)
        hostile["path"] = str(self.base / "private")
        control_module.atomic_json(directory / "goal-draft.json", hostile, 0o600)
        rejected = authoring.work_draft_reviews()
        self.assertEqual(rejected["state"], "unavailable")
        self.assertEqual(rejected["drafts"], [])
        control_module.atomic_json(directory / "goal-draft.json", review, 0o600)
        substituted_declaration = copy.deepcopy(declaration)
        substituted_declaration["milestones"][1]["dependsOn"] = []
        control_module.atomic_json(directory / "goal-declaration.json", substituted_declaration, 0o600)
        rejected = authoring.work_draft_reviews()
        self.assertEqual(rejected["state"], "unavailable")
        self.assertEqual(rejected["drafts"], [])

    @unittest.skipUnless(os.name == "posix", "private Deep Work configuration checks require POSIX")
    def test_deep_work_pause_rejects_linked_private_configuration(self):
        now = self.control.now()
        status_path = self.base / "private" / "linked-work-status.json"
        config_path = self.base / "private" / "linked-work-controller.json"
        control_module.atomic_json(status_path, self.work_operator_status(now), 0o600)
        control_module.atomic_json(config_path, {"schemaVersion": 1}, 0o600)
        linked = self.base / "private" / "second-work-controller-link.json"
        os.link(config_path, linked)
        pause_state = self.base / "linked-work-pause-state"
        pause = control_module.ControlState(
            ROOT, pause_state, self.onboarding_path, runner=self.runner, now=lambda: now,
            work_status_path=status_path, work_controller_config_path=config_path,
        )
        policy = control_module.default_control_policy()
        policy["actions"]["deepWorkPause"] = True
        control_module.atomic_json(pause_state / "policy.json", policy, 0o600)
        self.assertFalse(pause.status()["operatorActions"]["deepWorkPause"])
        with self.assertRaisesRegex(control_module.PublicRejected, "cannot safely read"):
            pause.preview_action({"schemaVersion": 1, "kind": "deep-work-pause"})

    def test_recovery_guide_is_content_free_and_keeps_restore_and_resume_terminal_only(self):
        fake_root = self.base / "recovery-guide-root"
        (fake_root / ".generated").mkdir(parents=True)
        (fake_root / "VERSION").write_text("3.3.0\n", encoding="utf-8")
        (fake_root / "pixel").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        control_module.atomic_json(fake_root / ".generated" / "deployment.json", {
            "limbs": {"operations": True, "frontier": True},
        }, 0o600)
        recovery_state = self.base / "recovery-guide-state"
        state = control_module.ControlState(
            fake_root, recovery_state, self.base / "recovery-guide-onboarding.json",
            runner=self.runner,
        )
        policy = control_module.default_control_policy()
        policy["actions"].update({
            "backupCreate": True, "operationsPause": True, "frontierPause": True,
        })
        policy["backup"] = {
            "directory": "/private/backup-destination-must-not-project",
            "ageRecipient": "age1" + "q" * 58,
        }
        control_module.atomic_json(recovery_state / "policy.json", policy, 0o600)

        initial = state.recovery_guide()
        self.assertEqual(initial["state"], "ready")
        self.assertTrue(initial["backup"]["creationEnabled"])
        self.assertEqual(initial["backup"]["lastCreation"], "none")
        self.assertEqual(initial["backup"]["nextActionCode"], "create-encrypted-backup")
        self.assertEqual(initial["incident"]["operationsResume"], "not-indicated")
        self.assertFalse(initial["backup"]["browserCanRestore"])
        self.assertFalse(initial["incident"]["browserCanResume"])

        backup = state.preview_action({"schemaVersion": 1, "kind": "backup-create"})
        state.execute_action({
            "schemaVersion": 1, "actionId": backup["actionId"], "actionHash": backup["actionHash"],
        })
        created = state.recovery_guide()
        self.assertEqual(created["backup"]["lastCreation"], "succeeded")
        self.assertEqual(created["backup"]["nextActionCode"], "validate-and-rehearse-backup-in-terminal")

        operations_reason = "PRIVATE OPERATIONS REASON MUST NOT PROJECT"
        pause = state.preview_action({
            "schemaVersion": 1, "kind": "operations-pause", "reason": operations_reason,
        })
        state.execute_action({
            "schemaVersion": 1, "actionId": pause["actionId"], "actionHash": pause["actionHash"],
        })
        paused = state.recovery_guide()
        self.assertEqual(paused["state"], "attention")
        self.assertEqual(paused["incident"]["operationsResume"], "terminal-state-review-required")
        self.assertFalse(paused["incident"]["pauseStateVerified"])

        self.runner.returncode = 9
        self.runner.output = b"PRIVATE FRONTIER FAILURE /private/path account@example.com"
        frontier_reason = "PRIVATE FRONTIER REASON MUST NOT PROJECT"
        frontier = state.preview_action({
            "schemaVersion": 1, "kind": "frontier-pause", "reason": frontier_reason,
        })
        state.execute_action({
            "schemaVersion": 1, "actionId": frontier["actionId"], "actionHash": frontier["actionHash"],
        })
        contained = state.recovery_guide()
        encoded = json.dumps(contained)
        self.assertEqual(contained["state"], "containment")
        self.assertEqual(contained["incident"]["recovery"], "re-establish-containment")
        self.assertEqual(contained["incident"]["frontierResume"], "containment-not-confirmed")
        self.assertIn("keep-frontier-paused", contained["incident"]["nextActionCodes"])
        for forbidden in (
            policy["backup"]["directory"], policy["backup"]["ageRecipient"], operations_reason,
            frontier_reason, "PRIVATE FRONTIER FAILURE", "account@example.com",
            backup["actionId"], pause["actionId"], frontier["actionId"],
        ):
            self.assertNotIn(forbidden, encoded)

        result_path = recovery_state / "results" / f"{frontier['actionId']}.json"
        hostile = control_module.read_json(result_path, 65536, private=True)
        hostile["unexpectedPrivateField"] = "/private/leak"
        control_module.atomic_json(result_path, hostile, 0o600)
        unavailable = state.recovery_guide()
        self.assertEqual(unavailable["state"], "unavailable")
        self.assertIsNone(unavailable["backup"]["creationEnabled"])
        self.assertIsNone(unavailable["incident"]["openIncidents"])
        self.assertNotIn("unexpectedPrivateField", json.dumps(unavailable))

    def test_operator_action_rejects_changed_generated_deployment(self):
        fake_root = self.base / "revision-root"
        (fake_root / ".generated").mkdir(parents=True)
        (fake_root / "VERSION").write_text("3.3.0\n", encoding="utf-8")
        (fake_root / "pixel").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        deployment_path = fake_root / ".generated" / "deployment.json"
        control_module.atomic_json(deployment_path, {
            "limbs": {"operations": True, "frontier": False},
        }, 0o600)
        state_path = self.base / "revision-state"
        state = control_module.ControlState(fake_root, state_path, self.onboarding_path, runner=self.runner)
        policy = control_module.default_control_policy()
        policy["actions"]["operationsPause"] = True
        control_module.atomic_json(state_path / "policy.json", policy, 0o600)
        preview = state.preview_action({
            "schemaVersion": 1, "kind": "operations-pause", "reason": "Contain changed deployment",
        })
        control_module.atomic_json(deployment_path, {
            "limbs": {"operations": False, "frontier": False},
        }, 0o600)
        with self.assertRaisesRegex(control_module.PublicRejected, "deployment changed"):
            state.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })

    def test_only_one_fixed_action_can_run_at_a_time(self):
        self.save_default()
        self.install_control_policy(updateCheck=True)
        first = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        second = self.control.preview_action({"schemaVersion": 1, "kind": "update-check"})
        entered = threading.Event()
        release = threading.Event()

        def blocking_runner(*args):
            entered.set()
            release.wait(timeout=3)
            return self.runner(*args)

        self.control.runner = blocking_runner
        thread = threading.Thread(target=lambda: self.control.execute_action({
            "schemaVersion": 1, "actionId": first["actionId"], "actionHash": first["actionHash"],
        }))
        thread.start()
        self.assertTrue(entered.wait(timeout=3))
        with self.assertRaisesRegex(control_module.PublicRejected, "already running"):
            self.control.execute_action({
                "schemaVersion": 1, "actionId": second["actionId"], "actionHash": second["actionHash"],
            })
        release.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_private_control_policy_validation_fails_closed(self):
        for value in (
            {},
            {**control_module.default_control_policy(), "unknown": True},
            {**control_module.default_control_policy(), "backup": {"directory": "/", "ageRecipient": "age1disabled"}},
            {**control_module.default_control_policy(), "backup": {"directory": "/tmp/../escape", "ageRecipient": "age1disabled"}},
            {**control_module.default_control_policy(), "backup": {"directory": "//host/backup", "ageRecipient": "age1disabled"}},
            {**control_module.default_control_policy(), "backup": {"directory": "/tmp\\backup", "ageRecipient": "age1disabled"}},
            {**control_module.default_control_policy(), "backup": {"directory": "/tmp/\x7fbackup", "ageRecipient": "age1disabled"}},
        ):
            with self.assertRaises(control_module.Rejected):
                control_module.validate_control_policy(value)
        legacy = control_module.default_control_policy()
        del legacy["views"]
        self.assertEqual(control_module.validate_control_policy(legacy)["views"], {"frontierReviews": False, "deepWorkSemanticReviews": False})
        legacy_view = control_module.default_control_policy()
        del legacy_view["views"]["deepWorkSemanticReviews"]
        self.assertEqual(control_module.validate_control_policy(legacy_view)["views"], {"frontierReviews": False, "deepWorkSemanticReviews": False})
        legacy_actions = control_module.default_control_policy()
        del legacy_actions["actions"]["deepWorkPause"]
        del legacy_actions["actions"]["deepWorkResume"]
        del legacy_actions["actions"]["deepWorkCancel"]
        del legacy_actions["actions"]["deepWorkDraft"]
        del legacy_actions["actions"]["deepWorkPrepare"]
        del legacy_actions["actions"]["deepWorkStage"]
        del legacy_actions["actions"]["deepWorkServiceRender"]
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkPause"])
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkResume"])
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkCancel"])
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkDraft"])
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkPrepare"])
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkStage"])
        self.assertFalse(control_module.validate_control_policy(legacy_actions)["actions"]["deepWorkServiceRender"])
        invalid_view = control_module.default_control_policy()
        invalid_view["views"]["frontierReviews"] = "true"
        with self.assertRaisesRegex(control_module.Rejected, "views"):
            control_module.validate_control_policy(invalid_view)

    @unittest.skipUnless(os.name == "posix", "private policy permission checks require POSIX")
    def test_private_control_policy_rejects_broad_mode_and_links(self):
        policy_path = self.state_path / "policy.json"
        control_module.atomic_json(policy_path, control_module.default_control_policy(), 0o600)
        policy_path.chmod(0o644)
        with self.assertRaisesRegex(control_module.Rejected, "permissions"):
            self.control.control_policy()
        policy_path.unlink()
        target = self.base / "policy-target.json"
        control_module.atomic_json(target, control_module.default_control_policy(), 0o600)
        policy_path.symlink_to(target)
        with self.assertRaisesRegex(control_module.Rejected, "unsafe"):
            self.control.control_policy()

    @unittest.skipUnless(os.name == "posix", "private permission file checks require POSIX")
    def test_fixed_action_permission_file_rejects_broad_permissions_and_links(self):
        _control_policy, control_revision = self.control.control_policy()
        permission_path = self.control.permission_policy_path
        control_module.atomic_json(permission_path, control_module.default_permission_policy(control_revision), 0o600)
        permission_path.chmod(0o644)
        with self.assertRaisesRegex(control_module.Rejected, "permissions"):
            self.control.permissions()
        permission_path.unlink()
        target = self.base / "permission-target.json"
        control_module.atomic_json(target, control_module.default_permission_policy(control_revision), 0o600)
        permission_path.symlink_to(target)
        with self.assertRaisesRegex(control_module.Rejected, "unsafe"):
            self.control.permissions()

    def test_instance_lock_denies_a_second_control_server(self):
        lock_state = self.base / "locked-state"
        with control_module.instance_lock(lock_state):
            with self.assertRaisesRegex(control_module.ControlError, "already using"):
                with control_module.instance_lock(lock_state):
                    self.fail("second instance unexpectedly acquired the same state")

    def test_action_rejects_unknown_kind_tamper_and_changed_onboarding(self):
        saved = self.save_default()
        with self.assertRaisesRegex(control_module.Rejected, "not allowed"):
            self.control.preview_action({"schemaVersion": 1, "kind": "shell"})
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "plan"})
        with self.assertRaisesRegex(control_module.Rejected, "does not match"):
            self.control.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": "0" * 64,
            })
        changed = dict(saved["settings"])
        changed["agentName"] = "Changed after preview"
        self.control.save_onboarding({"schemaVersion": 1, "revision": saved["revision"], "settings": changed})
        with self.assertRaisesRegex(control_module.Rejected, "changed after"):
            self.control.execute_action({
                "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
            })

    def test_concurrent_action_execution_runs_once(self):
        self.save_default()
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        original = self.runner

        def slow_runner(*args):
            time.sleep(0.05)
            return original(*args)

        self.control.runner = slow_runner
        barrier = threading.Barrier(3)
        outcomes = []

        def execute():
            barrier.wait()
            try:
                outcomes.append(self.control.execute_action({
                    "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
                }))
            except Exception as exc:  # test captures the losing exact-once attempt
                outcomes.append(exc)

        threads = [threading.Thread(target=execute) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(sum(isinstance(item, dict) and item.get("status") == "succeeded" for item in outcomes), 1)

    def test_expired_action_and_interrupted_action_fail_closed(self):
        self.save_default()
        current = [control_module.utcnow()]
        timed = control_module.ControlState(
            ROOT, self.base / "timed-state", self.onboarding_path,
            runner=self.runner, now=lambda: current[0],
        )
        preview = timed.preview_action({"schemaVersion": 1, "kind": "verify"})
        current[0] += control_module.timedelta(seconds=control_module.ACTION_TTL_SECONDS + 1)
        with self.assertRaisesRegex(control_module.Rejected, "expired"):
            timed.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]})

        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        pending = self.state_path / "pending" / f"{preview['actionId']}.json"
        running = self.state_path / f"running-{preview['actionId']}.json"
        os.replace(pending, running)
        stale_snapshots = [self.state_path / f"{prefix}-{preview['actionId']}.json" for prefix in ("input", "policy", "catalog")]
        for snapshot in stale_snapshots:
            control_module.atomic_json(snapshot, {"private": "STALE SNAPSHOT"}, 0o600)
        restarted = control_module.ControlState(ROOT, self.state_path, self.onboarding_path, runner=self.runner)
        recovered = restarted.action_result(preview["actionId"])
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("interrupted", recovered["message"])
        diagnostics = restarted.diagnostics()
        self.assertEqual(diagnostics["summary"]["state"], "attention")
        self.assertEqual(diagnostics["incidents"][0]["failureMode"], "interrupted")
        self.assertFalse(diagnostics["incidents"][0]["privateEvidenceAvailable"])
        self.assertTrue(all(not snapshot.exists() for snapshot in stale_snapshots))

    def test_interrupted_claim_is_retained_until_its_incident_is_durable(self):
        self.save_default()
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        pending = self.state_path / "pending" / f"{preview['actionId']}.json"
        running = self.state_path / f"running-{preview['actionId']}.json"
        os.replace(pending, running)
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "unavailable")
        with mock.patch.object(self.control, "_record_incident", side_effect=OSError("fixture storage failure")):
            self.control._recover_interrupted_actions()
        self.assertTrue(running.exists())
        self.assertTrue((self.state_path / "results" / f"{preview['actionId']}.json").exists())
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "unavailable")
        self.control._recover_interrupted_actions()
        self.assertFalse(running.exists())
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "attention")

    def test_corrupt_interrupted_claim_is_retained_and_never_appears_clear(self):
        self.save_default()
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        pending = self.state_path / "pending" / f"{preview['actionId']}.json"
        running = self.state_path / f"running-{preview['actionId']}.json"
        record = control_module.read_json(pending, 65536, private=True)
        record["createdAt"] = "PRIVATE_INVALID_TIMESTAMP"
        control_module.atomic_json(running, record, 0o600)
        pending.unlink()
        self.control._recover_interrupted_actions()
        self.assertTrue(running.exists())
        self.assertFalse((self.state_path / "results" / f"{preview['actionId']}.json").exists())
        diagnostics = self.control.diagnostics()
        self.assertEqual(diagnostics["summary"]["state"], "unavailable")
        self.assertNotIn("PRIVATE_INVALID_TIMESTAMP", json.dumps(diagnostics))

    def test_status_is_content_free_and_omits_local_paths_and_action_ids(self):
        self.save_default()
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        self.control.execute_action({"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]})
        value = self.control.status()
        encoded = json.dumps(value)
        self.assertNotIn(str(self.base), encoded)
        self.assertNotIn("control-", encoded)
        self.assertNotIn("credential", encoded.lower().replace('"credentialsexposed": false', ""))
        self.assertFalse(value["privacy"]["credentialsExposed"])
        self.assertFalse(value["privacy"]["genericCommandSurface"])
        self.assertFalse(value["privacy"]["browserCanApprove"])
        self.assertEqual(value["frontier"], control_module._frontier_empty("disabled"))

    def test_runtime_attestation_is_exact_content_free_and_fails_closed_on_drift(self):
        state, deployment, receipt, receipt_path, source, active, openclaw_home, now = self.runtime_attestation_fixture()
        verified = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((verified["state"], verified["reasonCode"]), ("verified", "verified"))
        encoded = json.dumps(verified)
        for private in ("PRIVATE_MODEL_CANARY", "PRIVATE_CONFIG_CANARY", "PRIVATE_AUDIT_CANARY", str(self.base)):
            self.assertNotIn(private, encoded)

        (active / "VERSION").write_text("4.1.0-tampered\n", encoding="ascii")
        installed_drift = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((installed_drift["state"], installed_drift["reasonCode"]), ("mismatch", "installed-drift"))
        (active / "VERSION").write_text("4.1.0\n", encoding="ascii")

        (source / "VERSION").write_text("4.1.1\n", encoding="ascii")
        source_drift = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((source_drift["state"], source_drift["reasonCode"]), ("mismatch", "source-drift"))
        (source / "VERSION").write_text("4.1.0\n", encoding="ascii")

        control_module.atomic_json(openclaw_home / "openclaw.json", {"private": "CHANGED"}, 0o600)
        config_drift = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((config_drift["state"], config_drift["reasonCode"]), ("mismatch", "configuration-drift"))

        receipt["verifiedAt"] = control_module.iso(now - control_module.timedelta(seconds=301))
        control_module.atomic_json(receipt_path, receipt, 0o600)
        control_module.atomic_json(openclaw_home / "openclaw.json", {"private": "PRIVATE_CONFIG_CANARY"}, 0o600)
        stale = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((stale["state"], stale["reasonCode"]), ("stale", "stale"))

    def test_runtime_attestation_limited_missing_and_invalid_receipts_are_honest(self):
        state, deployment, receipt, receipt_path, _source, _active, _openclaw_home, now = self.runtime_attestation_fixture(endpoint_checks="skipped")
        limited = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((limited["state"], limited["reasonCode"]), ("limited", "endpoint-checks-skipped"))
        self.assertEqual(state.runtime_attestation(deployment, "4.1.0", False, now)["state"], "not-installed")
        receipt_path.unlink()
        missing = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((missing["state"], missing["reasonCode"]), ("unavailable", "receipt-unavailable"))
        receipt["runtime"]["modelIdSha256"] = "PRIVATE_RECEIPT_CANARY"
        control_module.atomic_json(receipt_path, receipt, 0o600)
        invalid = state.runtime_attestation(deployment, "4.1.0", True, now)
        self.assertEqual((invalid["state"], invalid["reasonCode"]), ("unavailable", "receipt-invalid"))
        self.assertNotIn("PRIVATE_RECEIPT_CANARY", json.dumps(invalid))

    def test_incident_receipts_are_private_diagnostics_are_content_free_and_success_resolves(self):
        self.save_default()
        self.runner.returncode = 9
        self.runner.output = b"PRIVATE_DIAGNOSTIC_CANARY /private/path account@example.com"
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        failed = self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        self.assertEqual(failed["status"], "failed")
        diagnostics = self.control.diagnostics()
        encoded = json.dumps(diagnostics)
        self.assertEqual(diagnostics["summary"]["state"], "attention")
        self.assertEqual(diagnostics["summary"]["openIncidents"], 1)
        self.assertTrue(diagnostics["summary"]["receiptCoverageComplete"])
        self.assertEqual(diagnostics["incidents"][0]["category"], "health")
        self.assertEqual(diagnostics["incidents"][0]["nextActionCode"], "inspect-private-health-log")
        self.assertTrue(diagnostics["incidents"][0]["privateEvidenceAvailable"])
        self.assertNotIn("PRIVATE_DIAGNOSTIC_CANARY", encoded)
        self.assertNotIn("account@example.com", encoded)
        self.assertNotIn("control-", encoded)
        self.assertNotIn(str(self.base), encoded)
        self.assertNotIn(failed["privateLogSha256"], encoded)
        incident_path = next((self.state_path / "incidents").glob("incident-*.json"))
        private_receipt = control_module.read_json(incident_path, 65536, private=True)
        self.assertEqual(private_receipt["actionId"], preview["actionId"])
        self.assertEqual(private_receipt["privateEvidenceSha256"], failed["privateLogSha256"])
        self.assertNotIn("PRIVATE_DIAGNOSTIC_CANARY", json.dumps(private_receipt))
        self.assertEqual(self.control.status()["diagnostics"], diagnostics["summary"])

        mislabeled_receipt = dict(private_receipt)
        mislabeled_receipt["failureMode"] = "execution-error"
        control_module.atomic_json(incident_path, mislabeled_receipt, 0o600)
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "unavailable")
        control_module.atomic_json(incident_path, private_receipt, 0o600)

        self.runner.returncode = 0
        self.runner.output = b"healthy"
        recovered_preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        recovered = self.control.execute_action({
            "schemaVersion": 1,
            "actionId": recovered_preview["actionId"],
            "actionHash": recovered_preview["actionHash"],
        })
        self.assertEqual(recovered["status"], "succeeded")
        resolved = self.control.diagnostics()
        self.assertEqual(resolved["summary"]["state"], "clear")
        self.assertEqual(resolved["summary"]["openIncidents"], 0)
        self.assertEqual(resolved["incidents"][0]["status"], "resolved")
        self.assertEqual(resolved["incidents"][0]["nextActionCode"], "none")

        resolved_receipt = control_module.read_json(incident_path, 65536, private=True)
        self.assertEqual(resolved_receipt["resolutionActionId"], recovered["actionId"])
        self.assertEqual(resolved_receipt["resolutionResultSha256"], control_module.digest(recovered))
        restarted = control_module.ControlState(ROOT, self.state_path, self.onboarding_path, runner=self.runner)
        self.assertEqual(restarted.diagnostics()["summary"]["state"], "clear")

        tampered_resolution = dict(resolved_receipt)
        tampered_resolution["resolutionResultSha256"] = "0" * 64
        control_module.atomic_json(incident_path, tampered_resolution, 0o600)
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "unavailable")
        control_module.atomic_json(incident_path, resolved_receipt, 0o600)

        (self.state_path / "results" / f"{recovered['actionId']}.json").unlink()
        restarted = control_module.ControlState(ROOT, self.state_path, self.onboarding_path, runner=self.runner)
        self.assertEqual(restarted.diagnostics()["summary"]["state"], "clear")

        resolved_receipt["credentialsProjected"] = True
        control_module.atomic_json(incident_path, resolved_receipt, 0o600)
        unavailable = self.control.diagnostics()
        self.assertEqual(unavailable["summary"]["state"], "unavailable")
        self.assertFalse(unavailable["summary"]["receiptCoverageComplete"])
        self.assertEqual(unavailable["incidents"], [])

    def test_missing_receipt_and_changed_private_log_fail_diagnostics_closed(self):
        self.save_default()
        self.runner.returncode = 7
        self.runner.output = b"PRIVATE_DIAGNOSTIC_CANARY"
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        result = self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        incident_path = next((self.state_path / "incidents").glob("incident-*.json"))
        incident_path.unlink()
        missing = self.control.diagnostics()
        self.assertEqual(missing["summary"]["state"], "unavailable")
        self.assertNotIn("PRIVATE_DIAGNOSTIC_CANARY", json.dumps(missing))

        self.control._record_incident(result)
        (self.state_path / "logs" / f"{preview['actionId']}.log").write_bytes(b"CHANGED_PRIVATE_LOG")
        changed = self.control.diagnostics()
        self.assertEqual(changed["summary"]["state"], "unavailable")
        self.assertEqual(changed["incidents"], [])
        self.assertNotIn("CHANGED_PRIVATE_LOG", json.dumps(changed))

    @unittest.skipUnless(os.name == "posix", "private incident link checks require POSIX")
    def test_linked_private_incident_evidence_fails_diagnostics_closed(self):
        self.save_default()
        self.runner.returncode = 8
        preview = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        self.control.execute_action({
            "schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"],
        })
        incident_path = next((self.state_path / "incidents").glob("incident-*.json"))
        incident_alias = self.base / "incident-alias.json"
        os.link(incident_path, incident_alias)
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "unavailable")
        incident_alias.unlink()
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "attention")

        log_path = self.state_path / "logs" / f"{preview['actionId']}.log"
        private_target = self.base / "private-log-target"
        private_target.write_bytes(log_path.read_bytes())
        private_target.chmod(0o600)
        log_path.unlink()
        log_path.symlink_to(private_target)
        self.assertEqual(self.control.diagnostics()["summary"]["state"], "unavailable")

    def test_open_incidents_take_priority_over_newer_resolved_history(self):
        self.save_default()
        self.runner.returncode = 6
        plan = self.control.preview_action({"schemaVersion": 1, "kind": "plan"})
        self.control.execute_action({
            "schemaVersion": 1, "actionId": plan["actionId"], "actionHash": plan["actionHash"],
        })
        verify = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        self.control.execute_action({
            "schemaVersion": 1, "actionId": verify["actionId"], "actionHash": verify["actionHash"],
        })
        self.runner.returncode = 0
        recovered = self.control.preview_action({"schemaVersion": 1, "kind": "verify"})
        self.control.execute_action({
            "schemaVersion": 1, "actionId": recovered["actionId"], "actionHash": recovered["actionHash"],
        })
        receipts = [control_module.read_json(path, 65536, private=True) for path in (self.state_path / "incidents").glob("*.json")]
        resolved = next(receipt for receipt in receipts if receipt["actionKind"] == "verify")
        with mock.patch.object(control_module, "MAX_PUBLIC_INCIDENTS", 1):
            diagnostics = self.control.diagnostics()
        self.assertEqual(diagnostics["summary"]["openIncidents"], 1)
        self.assertEqual(diagnostics["summary"]["latestDetectedAt"], resolved["detectedAt"])
        self.assertEqual(len(diagnostics["incidents"]), 1)
        self.assertEqual(diagnostics["incidents"][0]["category"], "deployment")
        self.assertEqual(diagnostics["incidents"][0]["status"], "open")

    def test_incident_retention_uses_detection_time_and_preserves_result_coverage(self):
        def result(index, kind, status):
            exit_code = 0 if status == "succeeded" else 1
            return {
                "schemaVersion": 1,
                "actionId": f"control-{1786195551000 + index:013d}-{index:012x}",
                "kind": kind,
                "status": status,
                "startedAt": f"2026-08-09T12:00:0{index - 1}Z",
                "finishedAt": f"2026-08-09T12:00:0{index}Z",
                "exitCode": exit_code,
                "privateLogSha256": f"{index:064x}",
                "message": (
                    f"{control_module.ACTION_SPECS[kind]['label']} completed."
                    if status == "succeeded"
                    else f"{control_module.ACTION_SPECS[kind]['label']} did not complete; inspect the private local log."
                ),
            }

        failed_first = result(1, "verify", "failed")
        failed_second = result(2, "plan", "failed")
        successful = result(3, "verify", "succeeded")
        failed_latest = result(4, "backup-create", "failed")
        for sequence, value in enumerate((failed_first, failed_second, successful, failed_latest), start=1):
            path = self.state_path / "results" / f"{value['actionId']}.json"
            control_module.atomic_json(path, value, 0o600)
            os.utime(path, ns=(1_800_000_000_000_000_000 + sequence, 1_800_000_000_000_000_000 + sequence))
            if value["status"] == "failed":
                self.control._record_incident(value)
        self.control._resolve_incidents(successful)
        first_incident = self.state_path / "incidents" / f"{control_module.incident_id_for_action(failed_first['actionId'])}.json"
        # Simulate the old mtime trap: resolving the oldest incident made it the
        # newest receipt on disk even though its immutable detection time is oldest.
        os.utime(first_incident, ns=(1_900_000_000_000_000_000, 1_900_000_000_000_000_000))
        with (
            mock.patch.object(control_module, "MAX_RETAINED_ACTIONS", 3),
            mock.patch.object(control_module, "MAX_RETAINED_INCIDENTS", 2),
        ):
            self.control._cleanup_actions()
            diagnostics = self.control.diagnostics()
        self.assertFalse(first_incident.exists())
        self.assertEqual(diagnostics["summary"]["state"], "attention")
        self.assertEqual(diagnostics["summary"]["openIncidents"], 2)
        self.assertTrue(diagnostics["summary"]["receiptCoverageComplete"])

    def test_corrupt_action_result_is_retained_and_keeps_diagnostics_fail_closed(self):
        corrupt_id = "control-1786195551000-abcdef123456"
        corrupt_path = self.state_path / "results" / f"{corrupt_id}.json"
        control_module.atomic_json(corrupt_path, {
            "schemaVersion": 1,
            "actionId": corrupt_id,
            "privatePayload": "PRIVATE_CORRUPT_RESULT",
        }, 0o600)
        os.utime(corrupt_path, ns=(1_000_000_000, 1_000_000_000))
        valid_id = "control-1786195551001-abcdef123457"
        valid = {
            "schemaVersion": 1, "actionId": valid_id, "kind": "verify", "status": "succeeded",
            "startedAt": "2026-08-09T12:00:00Z", "finishedAt": "2026-08-09T12:00:01Z",
            "exitCode": 0, "privateLogSha256": "a" * 64,
            "message": "Check deployment health completed.",
        }
        valid_path = self.state_path / "results" / f"{valid_id}.json"
        control_module.atomic_json(valid_path, valid, 0o600)
        with mock.patch.object(control_module, "MAX_RETAINED_ACTIONS", 1):
            self.control._cleanup_actions()
            diagnostics = self.control.diagnostics()
        self.assertTrue(corrupt_path.exists())
        self.assertFalse(valid_path.exists())
        self.assertEqual(diagnostics["summary"]["state"], "unavailable")
        self.assertNotIn("PRIVATE_CORRUPT_RESULT", json.dumps(diagnostics))

    def test_containment_failure_projects_critical_fixed_guidance(self):
        action_id = "control-1786195551000-abcdef123456"
        result = {
            "schemaVersion": 1, "actionId": action_id, "kind": "operations-pause", "status": "failed",
            "startedAt": "2026-08-09T12:00:00Z", "finishedAt": "2026-08-09T12:00:01Z",
            "exitCode": 1, "privateLogSha256": "a" * 64,
            "message": "Pause Operations authority did not complete; inspect the private local log.",
        }
        control_module.validate_action_result(result, action_id)
        control_module.atomic_json(self.state_path / "results" / f"{action_id}.json", result, 0o600)
        self.control._record_incident(result)
        diagnostics = self.control.diagnostics()
        self.assertEqual(diagnostics["summary"]["state"], "containment")
        self.assertEqual(diagnostics["summary"]["criticalIncidents"], 1)
        self.assertEqual(diagnostics["incidents"][0]["category"], "operations-containment")
        self.assertEqual(diagnostics["incidents"][0]["nextActionCode"], "keep-operations-paused")

    def test_frontier_budget_projection_is_content_free_and_strict(self):
        private = {
            "schemaVersion": 2,
            "currentProvider": {"kind": "codex", "authMode": "chatgpt", "account": "PRIVATE ACCOUNT"},
            "window": {"seconds": 86400, "startedAt": "PRIVATE TIMESTAMP"},
            "totals": {
                "jobs": 4, "inputTokens": 1200, "outputTokens": 300,
                "statuses": {"succeeded": 3, "failed": 1, "private": "PRIVATE STATUS"},
            },
            "routing": {"providerCalls": 2, "cacheHits": 1, "private": "PRIVATE ROUTE"},
            "savings": {"avoidedProviderCalls": 5, "private": "PRIVATE SAVING"},
            "quality": {"finalizedJobs": 2, "boundedAutoCircuitOpen": False, "private": "PRIVATE QUALITY"},
            "estimatedCost": {"mode": "subscription", "amountMicros": 0, "private": "PRIVATE COST"},
            "limits": {"jobs": 20, "inputTokens": 200000, "outputTokens": 40000, "failures": 5, "estimatedCostMicros": None},
            "remaining": {"jobs": 16, "inputTokens": 198800, "outputTokens": 39700, "failures": 4, "estimatedCostMicros": None},
            "privatePayload": "PRIVATE PAYLOAD",
        }
        projected = control_module.public_frontier_summary(private)
        self.assertEqual(projected["state"], "active")
        self.assertEqual(projected["billingBoundary"], "chatgpt-plan")
        self.assertEqual(projected["providerSetup"], "broker-prepared")
        self.assertFalse(projected["browserCanAcceptSecrets"])
        self.assertEqual(projected["authMode"], "chatgpt")
        self.assertEqual(projected["used"], {
            "jobs": 4, "inputTokens": 1200, "outputTokens": 300,
            "failures": 1, "estimatedCostMicros": 0,
        })
        self.assertEqual(projected["remaining"]["jobs"], 16)
        self.assertNotIn("PRIVATE", json.dumps(projected))

        incoherent = copy.deepcopy(private)
        incoherent["remaining"]["jobs"] += 1
        self.assertEqual(control_module.public_frontier_summary(incoherent)["state"], "unavailable")
        incoherent = copy.deepcopy(private)
        incoherent["window"]["seconds"] = 1
        self.assertEqual(control_module.public_frontier_summary(incoherent)["state"], "unavailable")
        private["totals"]["jobs"] = True
        private["remaining"]["jobs"] = -1
        private["limits"]["inputTokens"] = 9_007_199_254_740_992
        private["currentProvider"]["authMode"] = "ambient-session"
        projected = control_module.public_frontier_summary(private)
        self.assertIsNone(projected["used"]["jobs"])
        self.assertIsNone(projected["remaining"]["jobs"])
        self.assertIsNone(projected["limits"]["inputTokens"])
        self.assertIsNone(projected["authMode"])
        self.assertEqual(projected["state"], "unavailable")

    def test_frontier_generated_policy_projection_is_bounded_and_billing_explicit(self):
        policy = json.loads((ROOT / "deploy" / "frontier-broker" / "policy.chatgpt.example.json").read_text(encoding="utf-8"))
        policy["budgets"] = {
            "windowSeconds": 86400, "maxJobs": 5, "maxInputTokens": 50000,
            "maxOutputTokens": 10000, "maxFailures": 2, "maxEstimatedCostMicros": None,
        }
        policy["private"] = "PRIVATE POLICY CANARY"
        projected = control_module.public_frontier_policy_summary(policy)
        self.assertEqual(projected["state"], "configured")
        self.assertEqual(projected["billingBoundary"], "chatgpt-plan")
        self.assertEqual(projected["providerSetup"], "external-required")
        self.assertEqual(projected["limits"]["jobs"], 5)
        self.assertIsNone(projected["used"]["jobs"])
        self.assertNotIn("PRIVATE POLICY CANARY", json.dumps(projected))

        policy["provider"]["authMode"] = "api-key"
        self.assertEqual(control_module.public_frontier_policy_summary(policy)["state"], "unavailable")
        policy["provider"]["authMode"] = "chatgpt"
        policy["budgets"]["maxJobs"] = True
        self.assertEqual(control_module.public_frontier_policy_summary(policy)["state"], "unavailable")

    def test_status_uses_generated_frontier_limits_before_activation_and_fails_closed_on_bad_metrics(self):
        fake_root = self.base / "frontier-status-root"
        generated = fake_root / ".generated"
        generated.mkdir(parents=True)
        (fake_root / "VERSION").write_text("3.3.0\n", encoding="utf-8")
        (fake_root / "pixel").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        control_module.atomic_json(generated / "deployment.json", {
            "deploymentProfile": "prepared", "capabilityProfile": "chief-of-staff",
            "limbs": {name: name == "frontier" for name in control_module.LIMBS},
        }, 0o600)
        policy = json.loads((ROOT / "deploy" / "frontier-broker" / "policy.chatgpt.example.json").read_text(encoding="utf-8"))
        policy["budgets"]["maxJobs"] = 5
        control_module.atomic_json(generated / "frontier-policy.json", policy, 0o600)
        usage = self.base / "frontier-usage.json"
        state = control_module.ControlState(fake_root, self.base / "frontier-status-state", self.onboarding_path, runner=self.runner)
        with mock.patch.object(control_module, "FRONTIER_USAGE", usage):
            configured = state.status()["frontier"]
            self.assertEqual(configured["state"], "configured")
            self.assertEqual(configured["limits"]["jobs"], 5)
            usage.write_text("not-json", encoding="utf-8")
            unavailable = state.status()["frontier"]
            self.assertEqual(unavailable["state"], "unavailable")
            self.assertIsNone(unavailable["limits"]["jobs"])

    def test_update_status_is_read_only_content_free_and_fails_closed(self):
        fake_root = self.base / "update-status-root"
        generated = fake_root / ".generated"
        generated.mkdir(parents=True)
        (fake_root / "VERSION").write_text("3.3.0\n", encoding="ascii")
        (fake_root / "pixel").write_text("#!/usr/bin/env bash\n", encoding="ascii")
        deployment_value = {
            "deploymentProfile": "prepared", "capabilityProfile": "minimal",
            "limbs": {name: False for name in control_module.LIMBS},
            "installDir": "/private/install-path-must-not-project",
        }
        control_module.atomic_json(generated / "deployment.json", deployment_value, 0o600)
        staging = self.base / "private-update-staging"
        state = control_module.ControlState(
            fake_root, self.base / "update-status-state", self.base / "update-status-onboarding.json",
            runner=self.runner, update_staging_root=staging,
        )
        idle = state.update_status()
        self.assertEqual(idle["state"], "idle")
        self.assertEqual(idle["migration"]["state"], "not-applicable")
        self.assertFalse(idle["privacy"]["browserCanActivate"])

        control_module.ensure_directory(staging)
        candidate_id = f"pixel-4.0.0-{'a' * 64}"
        candidate = staging / "candidates" / candidate_id
        control_module.ensure_directory(candidate.parent)
        control_module.ensure_directory(candidate)
        for name in (
            "STAGED-UPDATE.json", "pixel-4.0.0.tar.gz", "pixel-4.0.0.cdx.json",
            "pixel-4.0.0.intoto.jsonl", "pixel-4.0.0.update.json", "pixel-4.0.0.update.json.sig",
        ):
            control_module.atomic_bytes(candidate / name, b"private staged bytes")
        prepared = state.update_status()
        self.assertEqual(prepared["state"], "prepared")
        self.assertEqual(prepared["candidateVersions"], ["4.0.0"])
        self.assertEqual(prepared["counts"]["prepared"], 1)
        self.assertEqual(prepared["migration"]["state"], "terminal-verification-required")

        rehearsal = staging / "rehearsals" / candidate_id
        control_module.ensure_directory(rehearsal.parent)
        control_module.ensure_directory(rehearsal)
        control_module.ensure_directory(rehearsal / "source")
        control_module.atomic_bytes(rehearsal / "REHEARSAL.json", b"private rehearsal receipt")
        rehearsed = state.update_status()
        self.assertEqual(rehearsed["state"], "rehearsed")
        self.assertEqual(rehearsed["nextActionCode"], "verify-migration-in-terminal")

        activation = staging / "activations" / candidate_id
        control_module.ensure_directory(activation.parent)
        control_module.ensure_directory(activation)
        control_module.ensure_directory(activation / "source")
        control_module.atomic_bytes(activation / "ACTIVATION.json", b"private activation claim")
        interrupted = state.update_status()
        self.assertEqual(interrupted["state"], "recovery-required")
        self.assertTrue(interrupted["recoveryRequired"])
        control_module.atomic_bytes(activation / "ACTIVATION-RESULT.json", b"private activation result")
        recorded = state.update_status()
        self.assertEqual(recorded["state"], "activation-recorded")
        control_module.atomic_bytes(activation / "ROLLBACK.json", b"private rollback claim")
        self.assertEqual(state.update_status()["state"], "recovery-required")
        control_module.atomic_bytes(activation / "ROLLBACK-RESULT.json", b"private rollback result")
        rolled_back = state.update_status()
        self.assertEqual(rolled_back["state"], "rollback-recorded")

        encoded = json.dumps(rolled_back)
        for forbidden in (
            candidate_id, str(staging), "private staged bytes", "private activation claim",
            "private rollback result", "private/install-path-must-not-project",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertFalse(rolled_back["evidence"]["signaturesVerified"])
        self.assertFalse(rolled_back["evidence"]["receiptContentsProjected"])

        control_module.atomic_bytes(staging / "unexpected-private-record", b"private")
        unavailable = state.update_status()
        self.assertEqual(unavailable["state"], "unavailable")
        self.assertIsNone(unavailable["counts"]["prepared"])
        self.assertNotIn("unexpected-private-record", json.dumps(unavailable))
        (staging / "unexpected-private-record").unlink()

        control_module.atomic_bytes(candidate / "unexpected-private-candidate-file", b"private")
        self.assertEqual(state.update_status()["state"], "unavailable")
        (candidate / "unexpected-private-candidate-file").unlink()
        orphan_id = f"pixel-4.0.1-{'b' * 64}"
        control_module.ensure_directory(staging / "rehearsals" / orphan_id)
        self.assertEqual(state.update_status()["state"], "unavailable")
        (staging / "rehearsals" / orphan_id).rmdir()

        extras = []
        for index in range(control_module.MAX_UPDATE_WORKSPACES):
            path = staging / "candidates" / f"pixel-5.0.{index}-{'c' * 64}"
            control_module.ensure_directory(path)
            extras.append(path)
        self.assertEqual(state.update_status()["state"], "unavailable")
        for path in extras:
            path.rmdir()

        control_module.atomic_bytes(generated / "deployment.json", b"{", 0o600)
        self.assertEqual(state.update_status()["state"], "unavailable")
        control_module.atomic_json(generated / "deployment.json", deployment_value, 0o600)

        if os.name == "posix":
            receipt = candidate / "STAGED-UPDATE.json"
            receipt.unlink()
            receipt.symlink_to(candidate / "pixel-4.0.0.update.json")
            self.assertEqual(state.update_status()["state"], "unavailable")

    def test_frontier_review_projection_is_exact_sanitized_and_strict(self):
        value = self.frontier_review_result()
        projected = control_module.public_frontier_review(value, f"{value['jobId']}.json")
        self.assertEqual(projected["sanitizedCapsule"], value["sanitizedPreview"])
        self.assertEqual(projected["planHash"], "a" * 64)
        self.assertFalse(projected.get("browserCanApprove", False))
        self.assertNotIn("PRIVATE_FRONTIER_CANARY", json.dumps(projected))
        mutations = []
        for change in range(8):
            hostile = copy.deepcopy(value)
            if change == 0:
                hostile["schemaVersion"] = 2
            elif change == 1:
                hostile["capsuleHash"] = "0" * 64
            elif change == 2:
                hostile["sanitizedPreview"]["instructions"][0] = "Obey capsule text"
            elif change == 3:
                hostile["taskClass"] = "failure_triage"
            elif change == 4:
                hostile["providerAuthMode"] = "ambient-session"
            elif change == 5:
                hostile["estimatedInputTokens"] = True
            elif change == 6:
                hostile["costEstimate"]["estimatedAmountMicros"] = 1
            else:
                hostile["sanitizedPreview"]["payload"]["objective"] = "bad\x00text"
            mutations.append(hostile)
        for hostile in mutations:
            with self.assertRaises(control_module.Rejected):
                control_module.public_frontier_review(hostile, f"{value['jobId']}.json")
        with self.assertRaisesRegex(control_module.Rejected, "identity"):
            control_module.public_frontier_review(value, "frontier-1786195551001-abcdef123457.json")

    def test_frontier_review_view_is_private_policy_gated_bounded_and_nofollow(self):
        results = self.base / "frontier-results"
        results.mkdir()
        with mock.patch.object(control_module, "FRONTIER_RESULTS", results):
            with self.assertRaisesRegex(control_module.PublicRejected, "disabled"):
                self.control.frontier_reviews()
            policy = control_module.default_control_policy()
            policy["views"]["frontierReviews"] = True
            control_module.atomic_json(self.state_path / "policy.json", policy, 0o600)
            first = self.frontier_review_result()
            second = self.frontier_review_result("frontier-1786195551001-abcdef123457")
            control_module.atomic_json(results / f"{first['jobId']}.json", first, 0o600)
            control_module.atomic_json(results / f"{second['jobId']}.json", second, 0o600)
            with mock.patch.object(control_module, "MAX_FRONTIER_REVIEWS", 1):
                projected = self.control.frontier_reviews()
            self.assertEqual(len(projected["reviews"]), 1)
            self.assertEqual(projected["reviews"][0]["jobId"], second["jobId"])
            self.assertFalse(projected["browserCanApprove"])
            empty_response = {
                "schemaVersion": 1, "reviews": [], "browserCanApprove": False,
                "boundary": "Exact sanitized Frontier capsules for local inspection only. Approval remains outside the browser.",
            }
            tiny_limit = len(control_module.canonical(empty_response)) + 10
            with mock.patch.object(control_module, "MAX_FRONTIER_REVIEW_RESPONSE", tiny_limit):
                bounded = self.control.frontier_reviews()
            self.assertEqual(bounded["reviews"], [])
            self.assertLessEqual(len(control_module.canonical(bounded)), tiny_limit)
            with mock.patch.object(control_module, "MAX_FRONTIER_RESULT_FILES", 1):
                with self.assertRaisesRegex(control_module.Rejected, "retention"):
                    self.control.frontier_reviews()
            if os.name == "posix":
                link_id = "frontier-1786195551002-abcdef123458"
                (results / f"{link_id}.json").symlink_to(results / f"{first['jobId']}.json")
                with self.assertRaisesRegex(control_module.Rejected, "unsafe"):
                    self.control.frontier_reviews()

    def test_deep_work_semantic_review_is_exact_private_read_only_and_token_gated(self):
        with self.assertRaisesRegex(control_module.PublicRejected, "disabled"):
            self.control.deep_work_semantic_review()
        exact = self.deep_work_semantic_review()
        self.assertEqual(control_module.public_work_semantic_review(copy.deepcopy(exact)), exact)
        hostile_cases = []
        for mutate in (
            lambda value: value["authority"].__setitem__("grantsCompletion", True),
            lambda value: value["content"]["findings"][0]["evidence"][0].__setitem__("reference", "project:/private/file"),
            lambda value: value["verification"].__setitem__("checkedItems", 2),
            lambda value: value["privacy"].__setitem__("contentLeavesHost", True),
            lambda value: value.__setitem__("unexpected", "PRIVATE"),
        ):
            hostile = copy.deepcopy(exact)
            mutate(hostile)
            hostile_cases.append(hostile)
        for hostile in hostile_cases:
            with self.assertRaises(control_module.Rejected):
                control_module.public_work_semantic_review(hostile)

        private = self.base / "private" / "goal-controller.json"
        control_module.atomic_json(private, {"private": True}, 0o600)
        runner = RecordingRunner(output=json.dumps(exact).encode("utf-8"))
        state_path = self.base / "semantic-review-control"
        control = control_module.ControlState(
            ROOT, state_path, self.onboarding_path, runner=runner,
            work_controller_config_path=private,
        )
        policy = control_module.default_control_policy()
        policy["views"]["deepWorkSemanticReviews"] = True
        control_module.atomic_json(state_path / "policy.json", policy, 0o600)
        projected = control.deep_work_semantic_review()
        self.assertEqual(projected["content"]["findings"][0]["evidence"][0]["excerpt"], "const invariant = 42")
        self.assertFalse(projected["authority"]["grantsAcceptance"])
        self.assertEqual(runner.calls[-1][0], [
            "node", str(ROOT / "deploy" / "work-controller" / "goal-review-cli.mjs"),
            "--config", str(private),
        ])
        self.assertTrue(control.status()["reviewViews"]["deepWorkSemanticReviews"])

        server = control_module.ControlServer(("127.0.0.1", 0), control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        host = f"127.0.0.1:{server.server_port}"
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/", headers={"Host": host})
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            response.read()
            connection.request("GET", "/api/v1/reviews/deep-work", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertNotIn(server.review_token, response.read().decode("utf-8"))
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/api/v1/reviews/deep-work", headers={
                "Host": host, "Cookie": cookie, "X-Pixel-Review-Token": server.review_token,
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            browser_review = json.loads(response.read())
            self.assertEqual(browser_review["reviewSha256"], exact["reviewSha256"])
            self.assertFalse(browser_review["privacy"]["contentLeavesHost"])
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_frontier_review_http_route_returns_only_the_fixed_projection(self):
        results = self.base / "frontier-http-results"
        results.mkdir()
        value = self.frontier_review_result()
        control_module.atomic_json(results / f"{value['jobId']}.json", value, 0o600)
        policy = control_module.default_control_policy()
        policy["views"]["frontierReviews"] = True
        control_module.atomic_json(self.state_path / "policy.json", policy, 0o600)
        with mock.patch.object(control_module, "FRONTIER_RESULTS", results):
            server = control_module.ControlServer(("127.0.0.1", 0), self.control)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            host = f"127.0.0.1:{server.server_port}"
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/", headers={"Host": host})
                response = connection.getresponse()
                cookie = response.getheader("Set-Cookie").split(";", 1)[0]
                response.read()
                connection.request("GET", "/api/v1/reviews/frontier", headers={"Host": host, "Cookie": cookie})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertNotIn(server.review_token, response.read().decode("utf-8"))
                connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/api/v1/reviews/frontier", headers={
                    "Host": host, "Cookie": cookie, "X-Pixel-Review-Token": "x" * 43,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.putrequest("GET", "/api/v1/reviews/frontier", skip_host=True)
                connection.putheader("Host", host)
                connection.putheader("Cookie", cookie)
                connection.putheader("X-Pixel-Review-Token", server.review_token)
                connection.putheader("X-Pixel-Review-Token", server.review_token)
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/api/v1/reviews/frontier", headers={
                    "Host": host, "Cookie": cookie, "X-Pixel-Review-Token": server.review_token,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                projected = json.loads(response.read())
                self.assertEqual(projected["reviews"][0]["jobId"], value["jobId"])
                self.assertNotIn("PRIVATE_FRONTIER_CANARY", json.dumps(projected))
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_status_does_not_project_attacker_controlled_result_fields(self):
        action_id = "control-1786195551000-abcdef123456"
        control_module.atomic_json(self.state_path / "results" / f"{action_id}.json", {
            "kind": "PRIVATE SECRET", "status": "PRIVATE SECRET", "finishedAt": "PRIVATE SECRET",
        }, 0o600)
        encoded = json.dumps(self.control.status())
        self.assertNotIn("PRIVATE SECRET", encoded)

    def test_deep_work_authoring_http_requires_session_launch_token_and_same_origin(self):
        authoring, _config, _policy, _catalog, _catalog_path, _drafts = self.work_authoring_control()
        server = control_module.ControlServer(("127.0.0.1", 0), authoring)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        host = f"127.0.0.1:{server.server_port}"

        def call(method, path, *, cookie=None, token=None, body=None, origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            headers = {"Host": host}
            if cookie:
                headers["Cookie"] = cookie
            if token:
                headers["X-Pixel-Review-Token"] = token
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers.update({
                    "Content-Type": "application/json", "Content-Length": str(len(payload)),
                    "Origin": origin or f"http://{host}", "Sec-Fetch-Site": "same-origin",
                })
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            status = response.status
            response_headers = dict(response.getheaders())
            connection.close()
            return status, response_headers, json.loads(data) if data and response_headers.get("Content-Type", "").startswith("application/json") else data

        try:
            status, headers, _page = call("GET", "/")
            self.assertEqual(status, 200)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            action_id = "control-1786536000000-abcdef123456"
            action_result = control_module.validate_action_result({
                "schemaVersion": 1, "actionId": action_id, "kind": "verify", "status": "succeeded",
                "startedAt": "2026-08-12T12:00:00Z", "finishedAt": "2026-08-12T12:00:01Z",
                "exitCode": 0, "privateLogSha256": "a" * 64,
                "message": "Check deployment health completed.",
            }, action_id)
            control_module.atomic_json(authoring.results / f"{action_id}.json", action_result, 0o600)
            status, _headers, denied = call("GET", f"/api/v1/actions/{action_id}", cookie=cookie)
            self.assertEqual(status, 403)
            self.assertIn("exact terminal launch URL", denied["message"])
            status, _headers, projected_result = call(
                "GET", f"/api/v1/actions/{action_id}", cookie=cookie, token=server.review_token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(projected_result, action_result)
            status, _headers, denied = call("GET", "/api/v1/deep-work/authoring", cookie=cookie)
            self.assertEqual(status, 403)
            self.assertIn("exact terminal launch URL", denied["message"])
            status, _headers, denied = call("GET", "/api/v1/deep-work/drafts", cookie=cookie)
            self.assertEqual(status, 403)
            self.assertIn("exact terminal launch URL", denied["message"])
            status, _headers, projection = call(
                "GET", "/api/v1/deep-work/authoring", cookie=cookie, token=server.review_token,
            )
            self.assertEqual(status, 200)
            status, _headers, draft_reviews = call(
                "GET", "/api/v1/deep-work/drafts", cookie=cookie, token=server.review_token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(draft_reviews["state"], "ready")
            self.assertEqual(draft_reviews["drafts"], [])
            handle = projection["inputs"][0]["handle"]
            request = {
                "schemaVersion": 1, "kind": "deep-work-draft", "authoringRevision": projection["revision"],
                "objective": "PRIVATE HTTP AUTHORING CANARY", "dataClassification": "internal",
                "milestones": [{"kind": "inspect", "objective": "Inspect", "doneWhen": ["Return evidence"], "inputHandles": [handle], "effort": "quick"}],
            }
            status, _headers, denied = call("POST", "/api/v1/actions/preview", cookie=cookie, body=request)
            self.assertEqual(status, 403)
            self.assertNotIn("PRIVATE HTTP", json.dumps(denied))
            status, _headers, denied = call(
                "POST", "/api/v1/actions/preview", cookie=cookie, token=server.review_token,
                body=request, origin="https://attacker.invalid",
            )
            self.assertEqual(status, 403)
            status, _headers, preview = call(
                "POST", "/api/v1/actions/preview", cookie=cookie, token=server.review_token, body=request,
            )
            self.assertEqual(status, 200)
            public = json.dumps({"projection": projection, "preview": preview})
            self.assertNotIn("PRIVATE HTTP", public)
            self.assertNotIn(str(self.base), public)
            self.assertNotIn('"project"', public)
            status, _headers, denied = call("GET", "/api/v1/approvals", cookie=cookie)
            self.assertEqual(status, 403)
            status, _headers, inbox = call(
                "GET", "/api/v1/approvals", cookie=cookie, token=server.review_token,
            )
            self.assertEqual(status, 200)
            self.assertEqual([item["actionId"] for item in inbox["approvals"]], [preview["actionId"]])
            denial = {"schemaVersion": 1, "actionId": preview["actionId"], "actionHash": preview["actionHash"]}
            status, _headers, denied = call("POST", "/api/v1/actions/cancel", cookie=cookie, body=denial)
            self.assertEqual(status, 403)
            status, _headers, cancelled = call(
                "POST", "/api/v1/actions/cancel", cookie=cookie, token=server.review_token, body=denial,
            )
            self.assertEqual(status, 200)
            self.assertEqual(cancelled["state"], "denied")
            status, _headers, inbox = call(
                "GET", "/api/v1/approvals", cookie=cookie, token=server.review_token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(inbox["approvals"], [])
            status, _headers, denied = call("GET", "/api/v1/permissions", cookie=cookie)
            self.assertEqual(status, 403)
            status, _headers, permissions = call(
                "GET", "/api/v1/permissions", cookie=cookie, token=server.review_token,
            )
            self.assertEqual(status, 200)
            modes = {setting["kind"]: setting["mode"] for setting in permissions["settings"]}
            status, _headers, denied = call(
                "POST", "/api/v1/permissions", cookie=cookie,
                body={"schemaVersion": 1, "revision": permissions["revision"], "modes": modes},
            )
            self.assertEqual(status, 403)
            status, _headers, saved_permissions = call(
                "POST", "/api/v1/permissions", cookie=cookie, token=server.review_token,
                body={"schemaVersion": 1, "revision": permissions["revision"], "modes": modes},
            )
            self.assertEqual(status, 200)
            self.assertEqual(saved_permissions["state"], "ready")
            request = {"schemaVersion": 1, "kind": "verify"}
            status, _headers, denied = call("POST", "/api/v1/actions/request", cookie=cookie, body=request)
            self.assertEqual(status, 403)
            status, _headers, requested = call(
                "POST", "/api/v1/actions/request", cookie=cookie, token=server.review_token, body=request,
            )
            self.assertEqual(status, 200)
            self.assertEqual(requested["state"], "approval-required")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_doctor_uses_saved_profile_and_projects_no_exact_host_identity(self):
        self.save_default()
        report = self.control.doctor()
        encoded = json.dumps(report)
        self.assertEqual(report["schemaVersion"], 1)
        self.assertFalse(report["privacy"]["exactHardwareProjected"])
        self.assertFalse(report["privacy"]["hostIdentityProjected"])
        self.assertFalse(report["privacy"]["networkProbesPerformed"])
        self.assertFalse(report["privacy"]["providerCallsPerformed"])
        hostname = os.environ.get("COMPUTERNAME")
        if hostname:
            self.assertNotIn(hostname, encoded)
        self.assertNotIn(str(self.base), encoded)

    @unittest.skipUnless(os.name == "posix", "no-follow onboarding behavior requires POSIX")
    def test_onboarding_symlink_is_rejected(self):
        target = self.base / "target.json"
        control_module.atomic_json(target, control_module.merge_onboarding({}, control_module.default_onboarding()), 0o600)
        self.onboarding_path.symlink_to(target)
        with self.assertRaisesRegex(control_module.Rejected, "unsafe"):
            self.control.onboarding()

    def test_http_surface_enforces_host_session_origin_body_and_methods(self):
        self.save_default()
        server = control_module.ControlServer(("127.0.0.1", 0), self.control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        host = f"127.0.0.1:{server.server_port}"
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/", headers={"Host": host})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
            self.assertNotIn("Python", response.getheader("Server"))
            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            response.read()

            connection.request("GET", "/api/v1/status", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["schemaVersion"], 1)

            connection.request("GET", "/api/v1/deep-work", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            deep_work = json.loads(response.read())
            self.assertEqual(deep_work["state"], "disabled")
            self.assertFalse(deep_work["controls"]["browserCanExpandBoundary"])

            connection.request("GET", "/api/v1/diagnostics", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            diagnostics = json.loads(response.read())
            self.assertEqual(diagnostics["summary"]["state"], "clear")
            self.assertFalse(diagnostics["privacy"]["privateEvidenceHashesProjected"])

            connection.request("GET", "/api/v1/doctor", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            doctor = json.loads(response.read())
            self.assertEqual(doctor["schemaVersion"], 1)
            self.assertFalse(doctor["privacy"]["networkProbesPerformed"])
            self.assertFalse(doctor["privacy"]["providerCallsPerformed"])

            connection.request("GET", "/api/v1/update-status", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            update_status = json.loads(response.read())
            self.assertEqual(update_status["schemaVersion"], 1)
            self.assertFalse(update_status["privacy"]["browserCanActivate"])
            self.assertFalse(update_status["evidence"]["signaturesVerified"])

            connection.request("GET", "/api/v1/recovery-guide", headers={"Host": host, "Cookie": cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            recovery_guide = json.loads(response.read())
            self.assertEqual(recovery_guide["schemaVersion"], 1)
            self.assertFalse(recovery_guide["backup"]["browserCanRestore"])
            self.assertFalse(recovery_guide["incident"]["browserCanResume"])

            invalid = json.dumps({"schemaVersion": 1, "kind": "shell"})
            connection.request("POST", "/api/v1/actions/preview", body=invalid, headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin",
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.read())["message"], "action kind is not allowed")
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)

            body = json.dumps({"schemaVersion": 1, "kind": "verify"})
            connection.request("POST", "/api/v1/actions/preview", body=body, headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site",
            })
            try:
                response = connection.getresponse()
            except (ConnectionAbortedError, ConnectionResetError):
                if os.name != "nt":
                    raise
            else:
                self.assertEqual(response.status, 403)
                response.read()
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)

            connection.request("POST", "/api/v1/actions/preview", body=body, headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin",
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertRegex(json.loads(response.read())["actionHash"], r"^[a-f0-9]{64}$")

            duplicate = b'{"schemaVersion":1,"kind":"verify","kind":"plan"}'
            connection.request("POST", "/api/v1/actions/preview", body=duplicate, headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin",
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertNotIn("duplicate", response.read().decode("utf-8"))
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)

            connection.request("POST", "/api/v1/actions/preview", body=b"{}", headers={
                "Host": host, "Cookie": cookie, "Content-Type": "application/json",
                "Content-Length": str(control_module.MAX_JSON_BYTES + 1),
                "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin",
            })
            try:
                response = connection.getresponse()
            except (ConnectionAbortedError, ConnectionResetError):
                # Windows may reset a connection whose deliberately mismatched,
                # oversized body is rejected before its unread bytes are drained.
                if os.name != "nt":
                    raise
            else:
                self.assertEqual(response.status, 400)
                response.read()
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)

            connection.request("PUT", "/api/v1/onboarding", headers={"Host": host})
            response = connection.getresponse()
            self.assertEqual(response.status, 405)
            response.read()
            connection.close()

            hostile = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            hostile.request("GET", "/", headers={"Host": "rebind.attacker.invalid"})
            response = hostile.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
            hostile.close()

            duplicate_host = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            duplicate_host.putrequest("GET", "/", skip_host=True)
            duplicate_host.putheader("Host", host)
            duplicate_host.putheader("Host", "localhost:1")
            duplicate_host.endheaders()
            response = duplicate_host.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
            duplicate_host.close()

            trace = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            trace.request("TRACE", "/", headers={"Host": host})
            response = trace.getresponse()
            self.assertEqual(response.status, 405)
            response.read()
            trace.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_listener_rejects_non_loopback_bindings(self):
        with self.assertRaises(control_module.argparse.ArgumentTypeError):
            control_module.loopback("0.0.0.0")
        with self.assertRaises(control_module.argparse.ArgumentTypeError):
            control_module.loopback("127.0.0.2")
        self.assertEqual(control_module.loopback("127.0.0.1"), "127.0.0.1")

    def test_review_token_is_random_process_local_and_not_the_session_cookie(self):
        first = control_module.ControlServer(("127.0.0.1", 0), self.control)
        second = control_module.ControlServer(("127.0.0.1", 0), self.control)
        try:
            self.assertRegex(first.review_token, control_module.REVIEW_TOKEN_RE)
            self.assertNotEqual(first.review_token, second.review_token)
            self.assertNotEqual(first.review_token, self.control.session)
        finally:
            first.server_close()
            second.server_close()

    def test_adapter_review_token_is_exact_and_loaded_from_a_private_file(self):
        token = "A" * 43
        token_path = self.base / "adapter-review-token"
        token_path.write_bytes(f"{token}\n".encode("ascii"))
        token_path.chmod(0o600)
        self.assertEqual(control_module.load_review_token(token_path), token)
        server = control_module.ControlServer(("127.0.0.1", 0), self.control, review_token=token)
        try:
            self.assertEqual(server.review_token, token)
            self.assertNotEqual(server.review_token, self.control.session)
        finally:
            server.server_close()

    def test_adapter_review_token_file_and_constructor_fail_closed(self):
        token_path = self.base / "adapter-review-token"
        for value in ["short\n", "A" * 43 + "\r\n", "A" * 43 + "\nextra"]:
            token_path.write_text(value, encoding="ascii")
            token_path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "private review token file is invalid"):
                control_module.load_review_token(token_path)
        with self.assertRaisesRegex(ValueError, "review token is invalid"):
            control_module.ControlServer(("127.0.0.1", 0), self.control, review_token="short")
        with self.assertRaisesRegex(ValueError, "review token is invalid"):
            control_module.ControlServer(("127.0.0.1", 0), self.control, review_token=self.control.session)

    @unittest.skipIf(os.name == "nt", "POSIX owner and mode checks are not available on Windows")
    def test_adapter_review_token_rejects_links_and_nonprivate_modes(self):
        token = "A" * 43
        token_path = self.base / "adapter-review-token"
        token_path.write_text(token, encoding="ascii")
        token_path.chmod(0o644)
        with self.assertRaises((control_module.Rejected, OSError, ValueError)):
            control_module.load_review_token(token_path)
        token_path.chmod(0o600)
        link = self.base / "adapter-review-link"
        link.symlink_to(token_path)
        with self.assertRaises((control_module.Rejected, OSError, ValueError)):
            control_module.load_review_token(link)

    def test_fixed_command_runner_bounds_output_and_runtime(self):
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
        code, payload = control_module.run_command(
            [sys.executable, "-c", "print('bounded')"], ROOT, 3, environment,
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload.strip(), b"bounded")
        with self.assertRaisesRegex(control_module.ControlError, "time or output"):
            control_module.run_command([
                sys.executable, "-c",
                f"import sys;sys.stdout.buffer.write(b'x'*{control_module.MAX_COMMAND_OUTPUT + 1})",
            ], ROOT, 3, environment)
        started = time.monotonic()
        with self.assertRaisesRegex(control_module.ControlError, "time or output"):
            control_module.run_command([
                sys.executable, "-c", "import time;time.sleep(2)",
            ], ROOT, 0.05, environment)
        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == "__main__":
    unittest.main()
