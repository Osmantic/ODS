import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_verify_frontier_codex", ROOT / "scripts/verify-frontier-codex.py",
)
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify)


class VerifyFrontierCodexTests(unittest.TestCase):
    def test_absent_response_includes_remain_allowed(self):
        self.assertEqual(verify.validate_response_includes(None), [])
        self.assertEqual(verify.validate_response_includes([]), [])

    def test_only_encrypted_reasoning_state_is_allowed(self):
        self.assertEqual(
            verify.validate_response_includes(["reasoning.encrypted_content"]),
            ["reasoning.encrypted_content"],
        )

    def test_unknown_or_duplicate_response_includes_fail_closed(self):
        rejected = (
            ["message.output_text.logprobs"],
            ["reasoning.encrypted_content", "message.output_text.logprobs"],
            ["reasoning.encrypted_content", "reasoning.encrypted_content"],
            "reasoning.encrypted_content",
            [1],
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    verify.validate_response_includes(value)


if __name__ == "__main__":
    unittest.main()
