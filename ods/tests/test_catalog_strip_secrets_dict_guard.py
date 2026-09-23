"""generate-extensions-catalog strip_secrets must skip non-dict entries without raising AttributeError."""
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "generate-extensions-catalog.py"

spec = importlib.util.spec_from_file_location("generate_extensions_catalog", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class StripSecretsGuardTests(unittest.TestCase):
    def test_strip_secrets_skips_non_dict_elements(self):
        raw = [
            {"name": "API_KEY", "secret": True, "value": "123"},
            "INVALID_STRING_ENTRY",
            None,
            12345,
            {"name": "PUBLIC_PORT", "secret": False, "value": "8080"}
        ]
        cleaned = mod.strip_secrets(raw)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0], {"name": "API_KEY", "value": "123"})
        self.assertEqual(cleaned[1], {"name": "PUBLIC_PORT", "value": "8080"})


if __name__ == "__main__":
    unittest.main()
