"""Exercise LibreChat's search credential handoff in the actual Compose plan."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

COMPOSE = Path(__file__).resolve().parents[1] / "extensions/library/services/librechat/compose.yaml"
REQUIRED = ("JWT_SECRET", "JWT_REFRESH_SECRET", "LIBRECHAT_MONGO_PASSWORD", "CREDS_KEY", "CREDS_IV")


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class LibreChatSearchEnvironment(unittest.TestCase):
    def render(self, key, search):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("LIBRECHAT_", "MEILI_")) and k not in (*REQUIRED, "SEARCH")}
        env.update(dict.fromkeys(REQUIRED, "librechat-compose-fixture-secret"))
        env.update(LIBRECHAT_MEILI_KEY=key, SEARCH=search)
        with tempfile.TemporaryDirectory() as directory:
            empty_env = Path(directory) / ".env"
            empty_env.touch()
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(empty_env), "-f", str(COMPOSE),
                 "config", "--format", "json"],
                check=True, capture_output=True, text=True, env=env, timeout=30,
            )
        return json.loads(result.stdout)["services"]

    def test_enabled_search_receives_the_same_key_as_meilisearch(self):
        for key in ("custom-search-fixture-key", "space:$literal#percent%-value"):
            with self.subTest(key=key):
                services = self.render(key, "true")
                app = services["librechat"]["environment"]
                server = services["librechat-meilisearch"]["environment"]
                # LibreChat 0.7.7 enables indexing only with all three fields.
                self.assertTrue(app.get("MEILI_HOST") and app.get("MEILI_MASTER_KEY")
                                and app.get("SEARCH", "").lower() == "true")
                self.assertEqual(app["MEILI_MASTER_KEY"], server["MEILI_MASTER_KEY"])
                self.assertEqual(app["MEILI_MASTER_KEY"], key.replace("$", "$$"))

    def test_search_opt_out_is_preserved(self):
        app = self.render("custom-search-fixture-key", "false")["librechat"]["environment"]
        self.assertEqual(app["SEARCH"], "false")


if __name__ == "__main__":
    unittest.main()
