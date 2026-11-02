"""Exercise the installer/CLI validator with the same modes Docker accepts."""

import itertools
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = """WEBUI_SECRET=test-webui-secret
SEARXNG_SECRET=test-searxng-secret
N8N_USER=admin@ods.local
N8N_PASS=test-pass-1234
LITELLM_KEY=sk-test-key-1234
"""


class ReasoningSchemaTest(unittest.TestCase):
    def validate(self, value, key="LLAMA_REASONING"):
        with tempfile.TemporaryDirectory(prefix="ods-reasoning-schema-") as temp:
            env_file = Path(temp) / ".env"
            env_file.write_text(BASE_ENV + f"{key}={value}\n", encoding="utf-8")
            return subprocess.run(
                ["/bin/bash", str(ROOT / "scripts/validate-env.sh"),
                 str(env_file), str(ROOT / ".env.schema.json")],
                capture_output=True, text=True, timeout=30,
            )

    def test_all_known_case_variants_validate(self):
        for mode in ("off", "on", "auto"):
            for letters in itertools.product(*[(c, c.upper()) for c in mode]):
                value = "".join(letters)
                with self.subTest(value=value):
                    result = self.validate(value)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_modes_still_fail_validation(self):
        for value in ("enabled", "custom-format", "on off"):
            with self.subTest(value=value):
                result = self.validate(value)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("LLAMA_REASONING: invalid value", result.stdout)

    def test_other_case_sensitive_settings_are_unchanged(self):
        result = self.validate("AUTO", key="N_GPU_LAYERS")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("N_GPU_LAYERS", result.stdout)


if __name__ == "__main__":
    unittest.main()
