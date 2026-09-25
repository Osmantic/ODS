import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "security-evals" / "source-pressure" / "fuzz.py"
SPEC = importlib.util.spec_from_file_location("pixel_source_pressure", MODULE_PATH)
pressure = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pressure)


class SourcePressureTests(unittest.TestCase):
    def test_deterministic_hostile_corpus(self):
        counts = pressure.run(250, 12345)
        self.assertEqual(counts, {"email": 250, "calendar": 250, "social": 250, "parser": 250})


if __name__ == "__main__":
    unittest.main()
