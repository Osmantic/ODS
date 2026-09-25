import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Allow running this test from repo root without installing the service as a package.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from key_management import resolve_shield_api_key, persist_key


class TestKeyManagement(unittest.TestCase):
    def test_blank_environment_falls_back_without_changing_persisted_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            for value in ("", " \t\n", None, 42):
                self.assertEqual(resolve_shield_api_key(value, key_path), "persisted")
            self.assertEqual(resolve_shield_api_key(" exact secret ", key_path), " exact secret ")

    def test_env_key_wins(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            self.assertEqual(resolve_shield_api_key("from_env", key_path), "from_env")

    def test_loads_persisted_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            self.assertEqual(resolve_shield_api_key(None, key_path), "persisted")

    def test_generates_and_persists_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            key = resolve_shield_api_key(None, key_path)
            self.assertTrue(isinstance(key, str) and len(key) > 0)
            with open(key_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), key)

    def test_chmod_failure_is_logged_not_silently_swallowed(self):
        # Regression: persist_key() wrapped os.chmod in `except Exception:
        # pass` with no log call — a verbatim match for the pattern
        # CLAUDE.md forbids ("Never except Exception: pass"). If chmod
        # fails (some bind-mount configurations, SELinux-labeled mounts,
        # permission races), the generated SHIELD_API_KEY is left
        # world-readable with zero operator-visible signal.
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            with patch("os.chmod", side_effect=OSError("simulated chmod failure")):
                with self.assertLogs(level="WARNING") as captured:
                    persist_key(key_path, "some-key")
            self.assertTrue(
                any("chmod" in message.lower() for message in captured.output),
                f"expected a chmod-failure warning, got: {captured.output}",
            )
            # The key itself must still be written even though chmod failed.
            with open(key_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "some-key")


if __name__ == "__main__":
    unittest.main()
