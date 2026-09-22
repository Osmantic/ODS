"""Verify hook_reply rejects non-string operation or hook name."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_access_protocol import ProtocolError, hook_reply

class HookReplyTypeGuardTests(unittest.TestCase):
    def test_non_string_parameters_rejected(self):
        for bad_op, bad_name in (({}, "busy"), ("status", {}), (None, None), (123, "busy")):
            with self.assertRaises(ProtocolError):
                hook_reply(bad_op, bad_name, True)

    def test_valid_hook_reply_accepted(self):
        res = hook_reply("full-access", "busy", True)
        self.assertTrue(res)

if __name__ == "__main__":
    unittest.main()
