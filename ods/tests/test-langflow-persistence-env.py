"""Check Langflow's rendered persistent database configuration without a daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

COMPOSE = Path(__file__).resolve().parents[1] / "extensions/library/services/langflow/compose.yaml"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class LangflowPersistenceEnvironment(unittest.TestCase):
    def test_sqlite_database_uses_the_mounted_config_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            empty_env = Path(directory) / ".env"
            empty_env.touch()
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(empty_env), "-f", str(COMPOSE),
                 "config", "--format", "json"],
                check=True, capture_output=True, text=True, env=os.environ.copy(), timeout=30,
            )
        service = json.loads(result.stdout)["services"]["langflow"]
        # In the shipped 1.8.0 image HOME is /app/data. Without this setting,
        # SQLite is created in site-packages, outside every persistent mount.
        self.assertEqual(service["environment"].get("LANGFLOW_SAVE_DB_IN_CONFIG_DIR"), "true")
        data_mount = next(mount for mount in service["volumes"] if mount["target"] == "/app/data")
        self.assertEqual(data_mount["type"], "bind")
        self.assertTrue(data_mount["source"].endswith("/data/langflow"))
        self.assertNotIn("LANGFLOW_DATABASE_URL", service["environment"])


if __name__ == "__main__":
    unittest.main()
