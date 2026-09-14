"""Verify Piper's actual Compose storage target matches its shipped data directory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

COMPOSE = Path(__file__).resolve().parents[1] / "extensions/library/services/piper-audio/compose.yaml"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class PiperModelMount(unittest.TestCase):
    def test_model_downloads_and_custom_voices_share_the_persistent_config_mount(self):
        with tempfile.TemporaryDirectory(prefix="piper install ") as directory:
            root = Path(directory)
            (root / ".env").touch()
            env = {**os.environ, "PIPER_VOICE":"en_US-lessac-medium", "PIPER_PORT":"11200"}
            result = subprocess.run(["docker", "compose", "--project-directory", directory,
                                     "--env-file", str(root / ".env"), "-f", str(COMPOSE),
                                     "config", "--format", "json"], env=env, check=True,
                                    capture_output=True, text=True, timeout=30)
            service = json.loads(result.stdout)["services"]["piper-audio"]
            # The exact 1.6.3 s6 run script passes both --data-dir /config and
            # --download-dir /config to wyoming_piper. /config/piper misses both.
            self.assertEqual([(mount["source"], mount["target"]) for mount in service["volumes"]],
                             [(str(root / "data/piper"), "/config")])
            self.assertEqual(service["environment"]["PIPER_VOICE"], "en_US-lessac-medium")
            self.assertEqual(service["ports"][0]["target"], 10200)
            self.assertEqual(service["ports"][0]["published"], "11200")


if __name__ == "__main__":
    unittest.main()
