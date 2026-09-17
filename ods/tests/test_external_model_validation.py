"""Exercise offline validation for the installer's external LLM configuration."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate-models.py"


class ExternalModelValidationTests(unittest.TestCase):
    def test_external_backend_does_not_require_host_gguf(self):
        for backend in ("external", "EXTERNAL", "llama-server"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".env").write_text(
                    f"ODS_MODE=local\nLLM_BACKEND={backend}\n"
                    "SKIP_MODEL_DOWNLOAD=true\nGGUF_FILE=unused.gguf\n"
                    "ENABLE_VOICE=false\nENABLE_EMBEDDINGS=false\nENABLE_RAG=false\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [sys.executable, str(SCRIPT)],
                    env={**os.environ, "ODS_ROOT": str(root)},
                    text=True, capture_output=True, check=False,
                )
                if backend.lower() == "external":
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("external backend", result.stdout)
                    self.assertNotIn("MISSING", result.stdout)
                else:
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("unused.gguf", result.stdout)


if __name__ == "__main__":
    unittest.main()
