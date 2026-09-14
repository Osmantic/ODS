"""Unit test suite for AMD GPU runtime profiler resilience."""

import unittest
from unittest.mock import patch, MagicMock
from gpu import get_gpu_info_amd


class TestGpuAmdResilience(unittest.TestCase):
    @patch("subprocess.run")
    def test_amd_smi_not_found_returns_empty(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        info = get_gpu_info_amd()
        self.assertIn(info, [None, []])

    @patch("subprocess.run")
    def test_amd_smi_non_zero_exit_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        info = get_gpu_info_amd()
        self.assertIn(info, [None, []])


if __name__ == "__main__":
    unittest.main()
