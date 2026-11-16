"""Host-receipt boundaries, using real files and synthetic transcripts."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_receipt_join as join
import probe

CHAT, REQUEST = "fixture-chat", "fixture-request"
SESSION = "11111111-2222-4333-8444-555555555555"
TOOL = "pixel_ods_readonly_exec"
PROMPT = "Synthetic-private-prompt: compute 12 * 37 + 10 with Python."
USAGE = {"input": 10, "output": 5, "cacheRead": 20, "cacheWrite": 0, "totalTokens": 35}


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def manifest():
    return {
        "schema_version": 1,
        "base_url": "http://127.0.0.1:9",
        "api_key_env": "PRIVATE_SELECTOR",
        "timeout_s": 60,
        "recovery_timeout_s": 40,
        "task": {
            "id": "fixture",
            "messages": [{"role": "user", "content": PROMPT}],
            "validator": {"kind": "exact", "expected": "454"},
            "required_tools": [TOOL],
        },
    }


def rows(final="454", tools=True):
    messages = [{"role": "user", "content": PROMPT}]
    if tools:
        messages += [
            {
                "role": "assistant",
                "stopReason": "toolUse",
                "usage": USAGE.copy(),
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call1",
                        "name": TOOL,
                        "arguments": {"command": "PRIVATE_COMMAND"},
                    }
                ],
            },
            {
                "role": "toolResult",
                "toolCallId": "call1",
                "toolName": TOOL,
                "isError": False,
                "details": {"status": "completed", "exitCode": 0},
                "content": [{"type": "text", "text": "PRIVATE_TOOL_OUTPUT"}],
            },
        ]
    messages.append(
        {
            "role": "assistant",
            "stopReason": "stop",
            "usage": USAGE.copy(),
            "content": [{"type": "text", "text": final}],
        }
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return [
        {"type": "message", "id": str(i), "timestamp": now, "message": m}
        for i, m in enumerate(messages)
    ]


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "sessions.json"
        self.store.write_text("{}", encoding="utf-8")
        self.manifest = manifest()
        self.key = "agent:pixel:openai-user:ods-" + sha(CHAT)
        self.transcript = self.root / (SESSION + ".jsonl")
        self.witness = join.NativeReceiptWitness(
            str(self.root), self.manifest, CHAT, REQUEST
        )

    def publish(self, messages=None):
        self.transcript.write_text(
            "".join(
                json.dumps(r) + "\n" for r in (rows() if messages is None else messages)
            ),
            encoding="utf-8",
        )
        self.store.write_text(
            json.dumps(
                {self.key: {"sessionId": SESSION, "sessionFile": str(self.transcript)}}
            ),
            encoding="utf-8",
        )

    def record(self, final="454", **overrides):
        return {
            "chat_id": CHAT,
            "request_id": REQUEST,
            "task_sha256": sha(probe.canonical_json(self.manifest["task"])),
            "answer_sha256": sha(final),
            "answer_quality_pass": True,
            "result_state": "complete",
            "censored": False,
            "activity": "terminal",
            "http_status": 200,
            "transport_complete": True,
            **overrides,
        }

    def close(self):
        self.temp.cleanup()


class NativeJoinTests(unittest.TestCase):
    def fixture(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        return fixture

    def test_complete_tool_chain_and_redaction(self):
        f = self.fixture()
        f.publish()
        out = f.witness.finish(f.record())
        self.assertTrue(out["success"], out)
        self.assertTrue(out["binding_proven"])
        self.assertTrue(out["tool_execution_proven"])
        self.assertEqual(out["model_calls_observed"], 2)
        self.assertEqual(out["model_calls_completed"], 2)
        self.assertEqual(out["usage"]["totalTokens"], 70)
        self.assertIsNone(out["owned_cleanup_proven"])
        for secret in (
            PROMPT,
            "PRIVATE_COMMAND",
            "PRIVATE_TOOL_OUTPUT",
            "PRIVATE_SELECTOR",
            str(f.root),
        ):
            self.assertNotIn(secret, json.dumps(out))

    def test_wrong_answer_stays_bound_but_fails_independent_oracle(self):
        f = self.fixture()
        f.publish(rows("148", tools=False))
        out = f.witness.finish(f.record("148"))
        self.assertTrue(out["binding_proven"], out)
        self.assertEqual(out["model_calls_completed"], 1)
        self.assertFalse(out["answer_quality_pass"])
        self.assertFalse(out["tool_execution_proven"])
        self.assertFalse(out["success"])

    def test_bad_call_order_and_identity_fail_closed(self):
        for case in (
            "result-before-call",
            "duplicate-call",
            "duplicate-result",
            "wrong-name",
            "orphan",
            "after-final",
            "pending",
            "second-user",
        ):
            with self.subTest(case=case):
                f = self.fixture()
                data = rows()
                if case == "result-before-call":
                    data[1], data[2] = data[2], data[1]
                if case == "duplicate-call":
                    data.insert(2, copy.deepcopy(data[1]))
                if case == "duplicate-result":
                    data.insert(3, copy.deepcopy(data[2]))
                if case == "wrong-name":
                    data[2]["message"]["toolName"] = "write"
                if case == "orphan":
                    data[2]["message"]["toolCallId"] = "other"
                if case == "after-final":
                    data[2], data[3] = data[3], data[2]
                if case == "pending":
                    data.pop(2)
                if case == "second-user":
                    data.insert(1, copy.deepcopy(data[0]))
                f.publish(data)
                out = f.witness.finish(f.record())
                self.assertFalse(out["binding_proven"], out)
                self.assertFalse(out["success"])

    def test_failed_tool_does_not_erase_binding(self):
        for change in (
            {"isError": True},
            {"details": {"status": "completed", "exitCode": 1}},
            {"details": {"status": "completed", "exitCode": False}},
            {"details": {"status": "blocked", "exitCode": 0}},
        ):
            with self.subTest(change=change):
                f = self.fixture()
                data = rows()
                data[2]["message"].update(change)
                f.publish(data)
                out = f.witness.finish(f.record())
                self.assertTrue(out["binding_proven"], out)
                self.assertFalse(out["tool_execution_proven"])
                self.assertFalse(out["success"])

    def test_api_mismatches_cannot_upgrade_receipts(self):
        for field, value in (
            ("chat_id", "other"),
            ("request_id", "other"),
            ("task_sha256", "0" * 64),
            ("answer_sha256", "0" * 64),
            ("result_state", "active"),
        ):
            with self.subTest(field=field):
                f = self.fixture()
                f.publish()
                out = f.witness.finish(f.record(**{field: value}))
                self.assertFalse(out["binding_proven"], out)
                self.assertFalse(out["success"])

    def test_censored_unknown_activity_and_recovered_transport(self):
        for flags, expected in (
            ({"censored": True}, False),
            ({"activity": "unknown"}, False),
            ({"censored": "false"}, False),
            ({"http_status": True}, False),
            ({"transport_complete": False}, True),
        ):
            with self.subTest(flags=flags):
                f = self.fixture()
                f.publish()
                out = f.witness.finish(f.record(**flags))
                self.assertTrue(out["answer_quality_pass"], out)
                self.assertEqual(out["success"], expected)

    def test_usage_unknown_and_aborted_not_completed(self):
        f = self.fixture()
        data = rows()
        data[1]["message"].pop("usage")
        f.publish(data)
        out = f.witness.finish(f.record())
        self.assertTrue(out["success"], out)
        self.assertIsNone(out["usage"])
        self.assertEqual(out["model_calls_completed"], 1)
        f = self.fixture()
        data = rows(tools=False)
        data[-1]["message"].update(
            stopReason="aborted", usage={key: 0 for key in USAGE}
        )
        f.publish(data)
        out = f.witness.finish(f.record())
        self.assertFalse(out["success"])
        self.assertNotEqual(out["model_calls_completed"], 1)

    def test_before_absence_immutable_manifest_and_unrelated_store_updates(self):
        f = self.fixture()
        f.publish()
        with self.assertRaises(ValueError):
            join.NativeReceiptWitness(str(f.root), f.manifest, CHAT, REQUEST)
        stored = json.loads(f.store.read_text())
        stored["another-chat"] = {"sessionId": "unrelated"}
        f.store.write_text(json.dumps(stored), encoding="utf-8")
        self.assertTrue(f.witness.finish(f.record())["success"])
        f = self.fixture()
        original = f.record()
        f.manifest["task"]["validator"]["expected"] = "148"
        f.publish()
        self.assertTrue(f.witness.finish(original)["success"])

    def test_delivery_note_is_prompt_context_not_answer(self):
        for suffix, expected in ((join.DELIVERY_NOTE, True), (" bogus note", False)):
            with self.subTest(suffix=suffix):
                f = self.fixture()
                data = rows()
                data[0]["message"]["content"] += suffix
                f.publish(data)
                self.assertEqual(f.witness.finish(f.record())["success"], expected)

    def test_invalid_transcripts_and_redacted_errors(self):
        for case in (
            "duplicate-json",
            "truncated",
            "malformed",
            "oversized",
            "stale",
            "future",
            "nonuuid",
            "escape",
        ):
            with self.subTest(case=case):
                f = self.fixture()
                data = rows()
                if case == "stale":
                    data[0]["timestamp"] = "2000-01-01T00:00:00Z"
                if case == "future":
                    data[-1]["timestamp"] = "2999-01-01T00:00:00Z"
                f.publish(data)
                if case == "duplicate-json":
                    f.transcript.write_text('{"a":1,"a":2}\n', encoding="utf-8")
                if case == "truncated":
                    f.transcript.write_bytes(f.transcript.read_bytes().rstrip(b"\n"))
                if case == "malformed":
                    f.transcript.write_text(
                        '{"secret":"SUPERSECRETMARKER",BROKEN}\n', encoding="utf-8"
                    )
                if case == "oversized":
                    f.transcript.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
                if case in ("nonuuid", "escape"):
                    store = json.loads(f.store.read_text())
                    store[f.key][
                        "sessionId" if case == "nonuuid" else "sessionFile"
                    ] = (
                        "not-a-uuid"
                        if case == "nonuuid"
                        else str(f.root.parent / "foreign.jsonl")
                    )
                    f.store.write_text(json.dumps(store), encoding="utf-8")
                out = f.witness.finish(f.record())
                self.assertFalse(out["success"], out)
                self.assertNotIn("SUPERSECRETMARKER", json.dumps(out))

    def test_symlink_and_hardlink_transcripts(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                f = self.fixture()
                f.publish()
                target = f.root / "real.jsonl"
                f.transcript.rename(target)
                try:
                    (os.symlink if kind == "symlink" else os.link)(target, f.transcript)
                except OSError:
                    self.skipTest(kind + " unavailable")
                self.assertFalse(f.witness.finish(f.record())["success"])

    def test_single_use_and_unsupported_manifest(self):
        f = self.fixture()
        f.publish()
        self.assertTrue(f.witness.finish(f.record())["success"])
        self.assertFalse(f.witness.finish(f.record())["success"])
        f = self.fixture()
        f.manifest["task"]["messages"].append({"role": "user", "content": "second"})
        with self.assertRaises(ValueError):
            join.NativeReceiptWitness(str(f.root), f.manifest, CHAT, REQUEST)


if __name__ == "__main__":
    unittest.main()
