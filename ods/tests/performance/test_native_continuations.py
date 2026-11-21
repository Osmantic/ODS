"""Optional continuation witness: append-only binding to an existing transcript."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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


def encode(rows_):
    return "".join(json.dumps(r) + "\n" for r in rows_).encode("utf-8")


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "sessions.json"
        self.store.write_text("{}", encoding="utf-8")
        self.manifest = manifest()
        self.key = "agent:pixel:openai-user:ods-" + sha(CHAT)
        self.transcript = self.root / (SESSION + ".jsonl")

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

    def witness(self, allow=True):
        return join.NativeReceiptWitness(
            str(self.root), self.manifest, CHAT, REQUEST,
            allow_existing_session=allow,
        )

    def append(self, messages):
        with self.transcript.open("ab") as stream:
            stream.write(encode(messages))

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


class ContinuationTests(unittest.TestCase):
    def fixture(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        return fixture

    def test_default_denies_existing_session(self):
        f = self.fixture()
        f.publish()
        with self.assertRaises(ValueError):
            join.NativeReceiptWitness(str(f.root), f.manifest, CHAT, REQUEST)
        with self.assertRaises(ValueError):
            join.NativeReceiptWitness(
                str(f.root), f.manifest, CHAT, REQUEST,
                allow_existing_session=1,
            )

    def test_valid_new_turn_binds_only_appended_rows(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        new_turn = rows()
        f.append(new_turn)
        out = witness.finish(f.record())
        self.assertTrue(out["success"], out)
        self.assertTrue(out["binding_proven"])
        self.assertTrue(out["tool_execution_proven"])
        self.assertEqual(out["model_calls_observed"], 2)
        self.assertEqual(out["model_calls_completed"], 2)
        self.assertEqual(out["usage"]["totalTokens"], 70)
        self.assertEqual(out["interim_stop_count"], 0)
        for secret in (PROMPT, "PRIVATE_COMMAND", "PRIVATE_TOOL_OUTPUT", str(f.root)):
            self.assertNotIn(secret, json.dumps(out))

    def test_old_turn_tools_cannot_satisfy_new_required_tools(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        f.append(rows(tools=False))
        out = witness.finish(f.record())
        self.assertTrue(out["binding_proven"], out)
        self.assertFalse(out["tool_execution_proven"])
        self.assertFalse(out["success"])
        self.assertEqual(out["model_calls_observed"], 1)
        self.assertEqual(out["usage"]["totalTokens"], 35)

    def test_metadata_after_prior_terminal_message_allowed(self):
        f = self.fixture()
        f.publish(rows() + [{"type": "metadata"}])
        witness = f.witness()
        f.append(rows())
        self.assertTrue(witness.finish(f.record())["success"])

    def test_partial_prior_transcript_rejected(self):
        f = self.fixture()
        f.publish()
        f.transcript.write_bytes(f.transcript.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, "before_transcript_invalid"):
            f.witness()

    def test_index_replacement_during_capture_rejected(self):
        f = self.fixture()
        f.publish()
        original = join._read_stable
        def replacing_read(path):
            value = original(path)
            if path == str(f.transcript):
                f.store.write_text("{}", encoding="utf-8")
            return value
        with patch.object(join, "_read_stable", side_effect=replacing_read):
            with self.assertRaisesRegex(ValueError, "before_entry_changed"):
                f.witness()

    def test_truncated_prior_transcript_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        f.transcript.write_bytes(f.transcript.read_bytes()[:10])
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_original_prefix_rewritten_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        original = f.transcript.read_bytes()
        f.append(rows())
        rewritten = original.replace(b"454", b"455", 1)
        f.transcript.write_bytes(rewritten + encode(rows()))
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_rotated_inode_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        original = f.transcript.read_bytes()
        replacement = f.root / "replacement.jsonl"
        replacement.write_bytes(original + encode(rows()))
        os.replace(replacement, f.transcript)
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_entry_changed_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        f.append(rows())
        other = "99999999-8888-4777-8666-555555555555"
        other_file = f.root / (other + ".jsonl")
        other_file.write_bytes(f.transcript.read_bytes())
        f.store.write_text(
            json.dumps({f.key: {"sessionId": other, "sessionFile": str(other_file)}}),
            encoding="utf-8",
        )
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_unfinished_dangling_prior_call_rejected(self):
        f = self.fixture()
        data = rows()
        data[-1]["message"]["content"] = [
            {"type": "toolCall", "id": "call1", "name": TOOL, "arguments": {}}
        ]
        data[-1]["message"]["stopReason"] = "toolUse"
        f.publish(data)
        with self.assertRaises(ValueError):
            f.witness()

    def test_no_append_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_partial_suffix_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        with f.transcript.open("ab") as stream:
            stream.write(encode(rows())[:-1])
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_repeated_suffix_user_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        new_turn = rows()
        new_turn.insert(1, copy.deepcopy(new_turn[0]))
        f.append(new_turn)
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_changed_answer_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        f.append(rows(final="148"))
        out = witness.finish(f.record("454"))
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_interim_continuation_before_user_rejected(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        new_turn = rows()
        interim = copy.deepcopy(new_turn[-1])
        interim["message"]["content"][0]["text"] = "155"
        new_turn.insert(0, interim)
        f.append(new_turn)
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_metadata_only_suffix_cannot_prove(self):
        f = self.fixture()
        f.publish()
        witness = f.witness()
        with f.transcript.open("ab") as stream:
            stream.write(b'{"type":"metadata"}\n')
        out = witness.finish(f.record())
        self.assertFalse(out["binding_proven"], out)
        self.assertFalse(out["success"])

    def test_run_rejects_allow_without_sessions_dir(self):
        import pixel_adapter
        with self.assertRaises(ValueError):
            pixel_adapter.run(manifest(), native_allow_continuation=True)
        with self.assertRaises(ValueError):
            pixel_adapter.run(manifest(), native_allow_continuation=1)


if __name__ == "__main__":
    unittest.main()
