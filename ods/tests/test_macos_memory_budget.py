import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess

SOURCE = Path(__file__).resolve().parents[1] / "installers/macos/lib/native-memory-budget.py"
spec = importlib.util.spec_from_file_location("native_memory_budget", SOURCE)
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)


class MemoryBudgetTests(unittest.TestCase):
    def test_16gb_mac_with_8gb_docker_and_9b_model_warns(self):
        result = budget.assess(16 * budget.GIB, 8 * budget.GIB, 6 * budget.GIB)
        self.assertEqual(result["risk"], "overcommitted")
        self.assertEqual(result["plannedBytes"], 19 * budget.GIB)

    def test_larger_mac_is_within_estimate_not_guaranteed(self):
        self.assertEqual(budget.assess(32 * budget.GIB, 8 * budget.GIB, 6 * budget.GIB)["risk"], "within-estimate")

    def test_missing_docker_is_not_zero_usage(self):
        self.assertEqual(budget.assess(16 * budget.GIB, None, 6 * budget.GIB)["risk"], "unknown")
        self.assertEqual(budget.assess(8 * budget.GIB, None, 6 * budget.GIB)["risk"], "overcommitted")

    def test_invalid_inputs_are_rejected(self):
        for values in [(0, 1, 1), (1, 0, 1), (1, 1, 0), (-1, None, 1)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                budget.assess(*values)

    def test_queries_have_deadline_and_do_not_print_raw_output(self):
        with patch.object(budget.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "123\n", "")) as run:
            self.assertEqual(budget.command_integer(["docker", "info"]), 123)
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            self.assertTrue(run.call_args.kwargs["capture_output"])


if __name__ == "__main__":
    unittest.main()
