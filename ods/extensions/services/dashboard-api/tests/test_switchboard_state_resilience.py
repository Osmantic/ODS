"""Unit test suite for model switchboard state file persistence resilience."""

import sys
import unittest
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard.state import read_state  # type: ignore


class TestSwitchboardStateResilience(unittest.TestCase):
    def test_read_nonexistent_state_file(self):
        missing_path = Path("/tmp/nonexistent_switchboard_state.json")
        st, errors = read_state(missing_path)
        self.assertIsNone(st)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
