"""hook_reply must reject non-string operation types with ProtocolError."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_access_protocol import hook_reply, ProtocolError


class HookReplyOperationTypeTests(unittest.TestCase):
    def test_non_string_operation_rejected(self):
        for bad in (None, 12345, ["op"]):
            with self.assertRaises(ProtocolError):
                hook_reply(bad, "busy", True)

    def test_valid_hook_reply_accepted(self):
        res = hook_reply("full-access", "busy", True)
        self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
