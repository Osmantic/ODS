"""Unit test suite for system info hardware probe resilience."""

import platform
import unittest


def get_system_architecture() -> dict[str, str]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


class TestSystemInfoResilience(unittest.TestCase):
    def test_get_system_architecture_keys(self):
        info = get_system_architecture()
        self.assertIsInstance(info, dict)
        self.assertIn("system", info)
        self.assertIn("machine", info)
        self.assertIn("python_version", info)


if __name__ == "__main__":
    unittest.main()
