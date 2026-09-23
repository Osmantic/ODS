import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "security-evals" / "control-boundary" / "run-live.py"
SPEC = importlib.util.spec_from_file_location("pixel_control_boundary", MODULE_PATH)
control_boundary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(control_boundary)


class ControlBoundaryTests(unittest.TestCase):
    def test_accepts_exact_response(self):
        self.assertTrue(control_boundary.exact_ignoring_whitespace("PIXEL_CONTROL_API_OK", "PIXEL_CONTROL_API_OK"))

    def test_accepts_model_inserted_formatting_whitespace(self):
        self.assertTrue(control_boundary.exact_ignoring_whitespace("PIX\nEL_CONTROL_API_OK", "PIXEL_CONTROL_API_OK"))

    def test_rejects_any_non_whitespace_extra_content(self):
        self.assertFalse(control_boundary.exact_ignoring_whitespace("PIXEL_CONTROL_API_OK.", "PIXEL_CONTROL_API_OK"))
        self.assertFalse(control_boundary.exact_ignoring_whitespace("result: PIXEL_CONTROL_API_OK", "PIXEL_CONTROL_API_OK"))
        self.assertFalse(control_boundary.exact_ignoring_whitespace("PIXEL\u200b_CONTROL_API_OK", "PIXEL_CONTROL_API_OK"))
        self.assertFalse(control_boundary.exact_ignoring_whitespace(None, "PIXEL_CONTROL_API_OK"))


if __name__ == "__main__":
    unittest.main()
