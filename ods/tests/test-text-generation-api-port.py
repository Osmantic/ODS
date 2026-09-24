"""The published OpenAI API port must target the actual container listener."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

DIRECTORY = Path(__file__).resolve().parents[1] / "extensions/library/services/text-generation-webui"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class TextGenerationApiPort(unittest.TestCase):
    def test_public_port_override_does_not_move_the_internal_listener(self):
        for overlay in (None, "nvidia", "amd"):
            for published in (None, "", "15001", "65534"):
                with self.subTest(overlay=overlay, published=published), tempfile.TemporaryDirectory() as temp:
                    env = os.environ.copy()
                    env.pop("TEXT_GEN_WEBUI_API_PORT", None)
                    if published is not None:
                        env["TEXT_GEN_WEBUI_API_PORT"] = published
                    empty_env = Path(temp) / ".env"
                    empty_env.touch()
                    args = ["docker", "compose", "--env-file", str(empty_env), "-f", str(DIRECTORY / "compose.yaml")]
                    if overlay:
                        args += ["-f", str(DIRECTORY / f"compose.{overlay}.yaml")]
                    result = subprocess.run(args + ["config", "--format", "json"], env=env,
                                            capture_output=True, text=True, check=True, timeout=30)
                    service = json.loads(result.stdout)["services"]["text-generation-webui"]
                    api_port = next(port for port in service["ports"] if port["target"] == 5001)
                    launch = shlex.split(service["environment"]["EXTRA_LAUNCH_ARGS"])
                    listener = int(launch[launch.index("--api-port") + 1])
                    self.assertEqual(listener, api_port["target"])
                    self.assertEqual(api_port["published"], published or "5001")
                    self.assertIn("--api", launch)
                    self.assertIn("--listen", launch)


if __name__ == "__main__":
    unittest.main()
