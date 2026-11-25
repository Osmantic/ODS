"""Shell entrypoint contracts; native ownership is tested in the manager suites."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "installers/macos/lib/native-llama-service.sh"


class NativeServiceWrapperTests(unittest.TestCase):
    def test_shell_preserves_arguments_and_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install & spaces"
            binary = root / "bin/llama-server"
            manager = root / "bin/macos_native_service.py"
            manager.parent.mkdir(parents=True)
            manager.write_text("import sys,json; print(json.dumps(sys.argv[1:]))\n")
            args = [str(root), str(binary), str(root / "data/native.pid")]
            env = {**os.environ, "ODS_PYTHON_CMD": sys.executable}
            for action in ("start", "stop", "status"):
                tail = ["--model", str(root / "model's & file.gguf"), "--ctx-size", "4096"] if action == "start" else []
                result = subprocess.run(["bash", str(SCRIPT), action, *args, *tail],
                                        env=env, text=True, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [action, *args, *tail])

    def test_missing_manager_fails_without_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = root / "native.pid"
            pid.write_text(str(os.getpid()))
            for action in ("start", "stop", "status"):
                result = subprocess.run(["bash", str(SCRIPT), action, str(root), str(root / "llama-server"), str(pid)],
                                        text=True, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("manager is missing", result.stderr)
                self.assertEqual(pid.read_text(), str(os.getpid()))

    def test_unknown_action_is_rejected(self):
        result = subprocess.run(["bash", str(SCRIPT), "bad", "/x", "/x/bin", "/x/pid"],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
