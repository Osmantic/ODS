import unittest
import sys
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from model_switchboard import state as sb  # type: ignore


class TestModelStateResilience(unittest.TestCase):
    def test_model_state_read_returns_dict(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=True) as tmp:
            path = Path(tmp.name)
            if path.exists():
                path.unlink()
            sb.record_verified_route(
                path,
                catalog_id="test-model",
                runtime_model_id="test-model.gguf",
                backend_kind="llama-server",
                endpoint_id="llama-server-default",
                context_length=32768,
                capabilities={"chat": True},
                proof_identity="test-model.gguf",
            )
            st, errors = sb.read_state(path)
            self.assertIsInstance(st, dict)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
