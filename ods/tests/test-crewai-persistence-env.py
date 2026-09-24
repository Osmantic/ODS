"""Verify the rendered Studio startup/data handoff using its real shell command."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

COMPOSE = Path(__file__).resolve().parents[1] / "extensions/library/services/crewai/compose.yaml"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class CrewAiPersistence(unittest.TestCase):
    def test_startup_links_sqlite_and_keeps_image_relative_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").touch()
            plan = subprocess.run(["docker", "compose", "--env-file", str(root / ".env"), "-f", str(COMPOSE),
                                   "config", "--format", "json"], check=True, text=True,
                                  capture_output=True, timeout=30)
            service = json.loads(plan.stdout)["services"]["crewai"]
            command = service.get("command") or []
            self.assertEqual(command[:2], ["bash", "-c"])
            self.assertNotIn("working_dir", service)
            self.assertIn("/app", [volume["target"] for volume in service["volumes"]])
            # Config JSON escapes dollars for reuse. Execute the same startup
            # command with Streamlit replaced only at the process boundary.
            script = command[2].replace("$$", "$")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "streamlit"
            fake.write_text("#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd()}))\n")
            fake.chmod(0o755)
            env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
            for _ in range(2):
                result = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                        capture_output=True, text=True, check=True, timeout=10)
                self.assertEqual(os.readlink(root / "crewai.db"), "/app/crewai.db")
                self.assertEqual(json.loads(result.stdout), {
                    "args": ["run", "./app/app.py", "--server.headless", "true"], "cwd": str(root),
                })
            (root / "crewai.db").unlink()
            (root / "crewai.db").write_bytes(b"existing database")
            result = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("back it up", result.stderr)
            self.assertEqual((root / "crewai.db").read_bytes(), b"existing database")


if __name__ == "__main__":
    unittest.main()
