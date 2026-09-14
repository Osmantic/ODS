"""Execute the shipped generator and inspect the config delivered to IDE users."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ODS = Path(__file__).resolve().parents[1]
SERVICE = ODS / "extensions/library/services/continue"


class ContinueClientConfigTests(unittest.TestCase):
    def generate(self, endpoint):
        with tempfile.TemporaryDirectory(prefix="continue config ") as directory:
            env = {**os.environ, "CONTINUE_WEBROOT": directory,
                   "CONTINUE_API_BASE": endpoint,
                   "LLM_API_URL": "http://ods-llama-server:8080"}
            result = subprocess.run(
                ["sh", str(SERVICE / "config/continue/entrypoint.sh")],
                env=env, capture_output=True, text=True, check=False,
            )
            config = Path(directory) / "config.yaml"
            return result, config.read_text() if config.exists() else None

    def test_complete_client_endpoints_are_preserved(self):
        for endpoint in (
            "http://127.0.0.1:8080/v1", "http://192.0.2.10:8080/api/v1",
            "https://models.example.test/gateway/v1", "http://[::1]:8080/v1/",
        ):
            with self.subTest(endpoint=endpoint):
                result, rendered = self.generate(endpoint)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNotNone(rendered)
                config = yaml.safe_load(rendered)
                self.assertEqual(len(config["models"]), 2)
                for model in config["models"]:
                    self.assertEqual(model["apiBase"], endpoint.rstrip("/"))
                    self.assertEqual(model["provider"], "openai")
                self.assertNotIn("ods-llama-server", rendered)
                self.assertNotIn("remoteConfigServerUrl", rendered)

    def test_missing_or_non_base_urls_fail_before_publishing(self):
        for endpoint in ("", "file:///tmp/config", "http://", "http:///v1", "http://host/v1\nmodels: []",
                         "http://user:password@host/v1", "http://host/v1?token=secret"):
            with self.subTest(endpoint=endpoint):
                result, rendered = self.generate(endpoint)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(rendered)

    def test_compose_and_setup_use_the_client_setting(self):
        compose = yaml.safe_load((SERVICE / "compose.yaml").read_text())
        env = compose["services"]["continue"]["environment"]
        self.assertTrue(any(item.startswith("CONTINUE_API_BASE=") for item in env))
        self.assertFalse(any(item.startswith("LLM_API_URL=") for item in env))
        manifest = yaml.safe_load((SERVICE / "manifest.yaml").read_text())
        field = next(item for item in manifest["service"]["env_vars"]
                     if item["key"] == "CONTINUE_API_BASE")
        self.assertTrue(field["required"])


if __name__ == "__main__":
    unittest.main()
