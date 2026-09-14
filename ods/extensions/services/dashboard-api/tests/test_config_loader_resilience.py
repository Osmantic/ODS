"""Unit test suite for config loader resilience."""

import unittest
from config import ODS_MODES, GPU_BACKEND


class TestConfigLoaderResilience(unittest.TestCase):
    def test_ods_modes_type_and_content(self):
        self.assertIsInstance(ODS_MODES, frozenset)
        self.assertIn("local", ODS_MODES)
        self.assertIn("cloud", ODS_MODES)

    def test_gpu_backend_type(self):
        self.assertIsInstance(GPU_BACKEND, str)


if __name__ == "__main__":
    unittest.main()
