"""Check the rendered Paperless/PostgreSQL connection contract without a daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

COMPOSE = Path(__file__).resolve().parents[1] / "extensions/library/services/paperless-ngx/compose.yaml"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class PaperlessDatabaseEnvironment(unittest.TestCase):
    def render(self, password):
        env = {key: value for key, value in os.environ.items() if not key.startswith("PAPERLESS_")}
        env["PAPERLESS_SECRET_KEY"] = "paperless-regression-session-key"
        if password is not None:
            env["PAPERLESS_DB_PASSWORD"] = password
        with tempfile.TemporaryDirectory() as directory:
            empty_env = Path(directory) / ".env"
            empty_env.touch()
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(empty_env), "-f", str(COMPOSE),
                 "config", "--format", "json"],
                check=True, capture_output=True, text=True, env=env, timeout=30,
            )
        return json.loads(result.stdout)["services"]

    def test_default_and_custom_password_reach_both_database_peers(self):
        for password in (None, "", "paperless-test-value", "a b:$literal#percent%"):
            with self.subTest(password=password):
                services = self.render(password)
                app = services["paperless-ngx"]["environment"]
                database = services["paperless-postgres"]["environment"]
                # Paperless 2.14.0 defaults its DB password to "paperless".
                self.assertEqual(app.get("PAPERLESS_DBPASS", "paperless"), database["POSTGRES_PASSWORD"])
                # Compose escapes literal dollars when serializing a reusable plan.
                self.assertEqual(database["POSTGRES_PASSWORD"], (password or "paperless").replace("$", "$$"))
                self.assertEqual(app["PAPERLESS_DBHOST"], "paperless-postgres")
                self.assertEqual(app["PAPERLESS_DBPORT"], "5432")


if __name__ == "__main__":
    unittest.main()
