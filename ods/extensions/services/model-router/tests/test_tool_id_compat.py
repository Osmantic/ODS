import copy
import hashlib
import unittest
from unittest import mock

from app import tool_id_compat as tool_id_compat


NEMO_ID = (
    "hf-lmstudio-community-Mistral-Nemo-Instruct-2407-GGUF-"
    "Mistral-Nemo-Instruct-2407-Q4_K_M-cf67c664.gguf"
)
LONG_ID = "bZJ86vYjPtftKihZKZweE3wjFwbdfACD"


def _assistant(*ids):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": i,
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }
            for i in ids
        ],
    }


def _tool(tid, text="ok"):
    return {"role": "tool", "tool_call_id": tid, "content": text}


class DetectionTests(unittest.TestCase):
    def test_positive_actual_hf_identity(self):
        self.assertTrue(
            tool_id_compat.route_requires_strict9_tool_ids(NEMO_ID, "llama-server")
        )

    def test_negative_qwen(self):
        self.assertFalse(
            tool_id_compat.route_requires_strict9_tool_ids(
                "Qwen2.5-7B-Instruct", "llama-server"
            )
        )

    def test_negative_other_backend(self):
        self.assertFalse(
            tool_id_compat.route_requires_strict9_tool_ids(NEMO_ID, "hipfire")
        )

    def test_negative_caller_alias(self):
        self.assertFalse(
            tool_id_compat.route_requires_strict9_tool_ids("ods/current", "llama-server")
        )

    def test_negative_other_mistral_family(self):
        self.assertFalse(
            tool_id_compat.route_requires_strict9_tool_ids(
                "mistralai/Mistral-7B-Instruct-v0.3", "llama-server"
            )
        )


class NormalizeTests(unittest.TestCase):
    def test_actual_production_id_rewritten_and_paired(self):
        msgs = [_assistant(LONG_ID), _tool(LONG_ID)]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertTrue(changed)
        wire = out[0]["tool_calls"][0]["id"]
        self.assertNotEqual(wire, LONG_ID)
        self.assertEqual(len(wire), 9)
        self.assertTrue(all(c.isascii() and c.isalnum() for c in wire))
        self.assertEqual(out[1]["tool_call_id"], wire)

    def test_valid9_unchanged(self):
        msgs = [_assistant("abc123XYZ"), _tool("abc123XYZ")]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_function_args_and_content_unchanged(self):
        msgs = [_assistant(LONG_ID), _tool(LONG_ID, "payload")]
        out, _ = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertEqual(
            out[0]["tool_calls"][0]["function"],
            msgs[0]["tool_calls"][0]["function"],
        )
        self.assertEqual(out[1]["content"], "payload")
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[1]["role"], "tool")

    def test_input_deep_unchanged(self):
        msgs = [_assistant(LONG_ID), _tool(LONG_ID)]
        snapshot = copy.deepcopy(msgs)
        tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertEqual(msgs, snapshot)

    def test_deterministic_repeated_call(self):
        msgs = [_assistant(LONG_ID), _tool(LONG_ID)]
        a, _ = tool_id_compat.normalize_history_tool_ids(msgs)
        b, _ = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertEqual(a, b)

    def test_deterministic_appended_history(self):
        base = [_assistant(LONG_ID), _tool(LONG_ID)]
        first, _ = tool_id_compat.normalize_history_tool_ids(base)
        wire = first[0]["tool_calls"][0]["id"]
        extended = base + [
            {"role": "user", "content": "next"},
            _assistant("call_other_123"),
            _tool("call_other_123"),
        ]
        out, changed = tool_id_compat.normalize_history_tool_ids(extended)
        self.assertTrue(changed)
        self.assertEqual(out[0]["tool_calls"][0]["id"], wire)
        self.assertEqual(out[1]["tool_call_id"], wire)

    def test_all_valid9_reserved_collision(self):
        valid = "abc123XYZ"
        real_digest = tool_id_compat._digest9

        def fake_digest(seed):
            if seed.startswith("victim\x00"):
                if seed.endswith("\x000"):
                    return valid
                return real_digest(seed)
            return real_digest(seed)

        with mock.patch.object(tool_id_compat, "_digest9", fake_digest):
            msgs = [
                _assistant("victim"),
                _assistant(valid),
                _tool("victim"),
                _tool(valid),
            ]
            out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertTrue(changed)
        self.assertEqual(out[1]["tool_calls"][0]["id"], valid)
        self.assertNotEqual(out[0]["tool_calls"][0]["id"], valid)
        self.assertEqual(out[2]["tool_call_id"], out[0]["tool_calls"][0]["id"])
        self.assertEqual(out[3]["tool_call_id"], valid)

    def test_collision_exhaustion_noop(self):
        with mock.patch.object(tool_id_compat, "_digest9", lambda seed: "000000000"):
            msgs = [_assistant("000000000", LONG_ID), _tool("000000000"), _tool(LONG_ID)]
            out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_multiple_calls_out_of_order_results(self):
        msgs = [
            _assistant("call_a", "call_b"),
            _tool("call_b", "b"),
            _tool("call_a", "a"),
        ]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertTrue(changed)
        a = out[0]["tool_calls"][0]["id"]
        b = out[0]["tool_calls"][1]["id"]
        self.assertNotEqual(a, b)
        self.assertEqual(out[1]["tool_call_id"], b)
        self.assertEqual(out[2]["tool_call_id"], a)

    def test_duplicate_call_id_noop(self):
        msgs = [
            _assistant("dup"),
            _assistant("dup"),
            _tool("dup", "1"),
            _tool("dup", "2"),
        ]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_orphan_result_noop(self):
        msgs = [_assistant("real"), _tool("real"), _tool("orphan")]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_duplicate_result_noop(self):
        msgs = [_assistant("x"), _tool("x"), _tool("x")]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_premature_result_noop(self):
        msgs = [_tool("x"), _assistant("x")]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_malformed_call_id_noop(self):
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "", "type": "function"}]},
            _tool(""),
        ]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_malformed_result_id_noop(self):
        msgs = [_assistant("x"), {"role": "tool", "tool_call_id": "", "content": ""}]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_unicode_nonalphanumeric_normalized(self):
        bad = "call_\u00e9\u00e9\u00e9"
        msgs = [_assistant(bad), _tool(bad)]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertTrue(changed)
        wire = out[0]["tool_calls"][0]["id"]
        self.assertEqual(len(wire), 9)
        self.assertTrue(all(c.isascii() and c.isalnum() for c in wire))
        self.assertEqual(out[1]["tool_call_id"], wire)

    def test_non_list_noop(self):
        out, changed = tool_id_compat.normalize_history_tool_ids("not a list")
        self.assertFalse(changed)
        self.assertEqual(out, "not a list")

    def test_empty_noop(self):
        out, changed = tool_id_compat.normalize_history_tool_ids([])
        self.assertFalse(changed)
        self.assertEqual(out, [])

    def test_no_ids_noop(self):
        msgs = [{"role": "user", "content": "hi"}]
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)

    def test_over_cap_noop(self):
        msgs = []
        for i in range(5000):
            cid = f"call_{i}"
            msgs.append(_assistant(cid))
            msgs.append(_tool(cid))
        out, changed = tool_id_compat.normalize_history_tool_ids(msgs)
        self.assertFalse(changed)
        self.assertIs(out, msgs)


if __name__ == "__main__":
    unittest.main()
