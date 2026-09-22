"""Exercise the installed Aider launcher at its shell/Compose boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "extensions/library/services/aider/run.sh"


class AiderLauncher(unittest.TestCase):
    def test_installed_launcher_overrides_echo_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory(prefix="aider launch ") as directory:
            root = Path(directory)
            (root / "docker-compose.base.yml").touch()
            (root / ".env").touch()
            service = root / "data/user-extensions/aider"
            service.mkdir(parents=True)
            shutil.copyfile(LAUNCHER, service / "run.sh")
            (service / "compose.yaml").touch()
            commands = root / "bin"
            commands.mkdir()
            fake = commands / "docker"
            fake.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
            fake.chmod(0o755)
            env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]}
            caller = ["my project/main.py", "--message", "Keep $HOME and `date` as text", "--model", "openai/local"]
            result = subprocess.run(["bash", str(service / "run.sh"), *caller], cwd=root,
                                    env=env, capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(json.loads(result.stdout), [
                "compose", "--project-directory", str(root), "--env-file", str(root / ".env"),
                "-f", str(service / "compose.yaml"), "run", "--rm", "--no-deps",
                "--entrypoint", "/venv/bin/aider", "aider", *caller,
            ])

    def test_wrong_working_directory_fails_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["bash", str(LAUNCHER), "--version"], cwd=directory,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ODS install directory", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
