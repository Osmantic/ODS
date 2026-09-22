"""Exercise LaunchAgent serialization and lifecycle without touching launchd."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "installers/macos/lib/native-llama-service.sh"


class NativeServiceTests(unittest.TestCase):
    def test_stop_reaps_untracked_install_owned_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / "commands"
            commands.mkdir()
            (commands / "launchctl").write_text("#!/bin/sh\nexit 1\n")
            (commands / "launchctl").chmod(0o700)
            install = root / "ods"
            binary = install / "bin" / "llama-server"
            binary.parent.mkdir(parents=True)
            # macOS system binaries can carry protected flags that an
            # unprivileged temp fixture cannot reproduce with copy2.
            shutil.copyfile("/bin/sleep", binary)
            binary.chmod(0o700)
            child = subprocess.Popen([str(binary), "120"])
            self.addCleanup(lambda: child.poll() is None and child.kill())
            pid_file = install / "data" / ".llama-server.pid"
            pid_file.parent.mkdir(parents=True)
            pid_file.write_text("999999\n")
            env = dict(
                os.environ,
                HOME=str(root),
                PATH=f"{commands}:{os.environ['PATH']}",
            )

            result = subprocess.run(
                ["bash", str(SCRIPT), "stop", str(install), str(binary), str(pid_file)],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            child.wait(timeout=5)
            self.assertIsNotNone(child.returncode)
            self.assertFalse(pid_file.exists())

    def test_live_old_process_blocks_replacement_and_preserves_recovery_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / 'commands'
            commands.mkdir()
            loaded = root / 'loaded'
            loaded.touch()
            (commands / 'launchctl').write_text('''#!/bin/sh
case "$1" in
  print) test -f "$TEST_STATE" || exit 1; printf '\\tpid = %s\\n' "$TEST_PID" ;;
  bootout) rm "$TEST_STATE" ;;
  bootstrap) touch "$TEST_STATE.unexpected-bootstrap" ;;
  *) exit 2 ;;
esac
''')
            (commands / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            for path in commands.iterdir():
                path.chmod(0o700)
            env = dict(os.environ, HOME=str(root), PATH=f"{commands}:{os.environ['PATH']}",
                       TEST_STATE=str(loaded), TEST_PID=str(os.getpid()))
            plist = root / 'Library/LaunchAgents/com.ods.llama-server.plist'
            plist.parent.mkdir(parents=True)
            plist.write_bytes(b'original plist retained')
            pid_file = root / 'native.pid'
            pid_file.write_text(str(os.getpid()))
            for action in ('start', 'stop'):
                loaded.touch()
                result = subprocess.run(['bash', str(SCRIPT), action, str(root), '/bin/sleep',
                                         str(pid_file)], env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('shutdown is not confirmed', result.stderr)
                self.assertEqual(plist.read_bytes(), b'original plist retained')
                self.assertEqual(pid_file.read_text(), str(os.getpid()))
                self.assertFalse(Path(str(loaded) + '.unexpected-bootstrap').exists())

    def test_start_and_stop_preserve_exact_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / "commands"
            commands.mkdir()
            launchctl = commands / "launchctl"
            launchctl.write_text('''#!/bin/sh
case "$1" in
  print) test -f "$TEST_STATE" || exit 1; printf '\\tpid = %s\\n' "$TEST_PID" ;;
  bootstrap)
    if test ! -f "$TEST_STATE.retry"; then
      touch "$TEST_STATE.retry"
      exit 5
    fi
    touch "$TEST_STATE" ;;
  bootout) rm "$TEST_STATE"; kill "$TEST_PID" ;;
  kickstart) test -f "$TEST_STATE" ;;
  *) exit 2 ;;
esac
''')
            launchctl.chmod(0o700)
            child = subprocess.Popen(['sleep', '120'])
            self.addCleanup(lambda: child.poll() is None and child.terminate())
            reaper = threading.Thread(target=child.wait, daemon=True)
            reaper.start()
            env = dict(os.environ, HOME=str(root), PATH=f"{commands}:{os.environ['PATH']}",
                       TEST_STATE=str(root / "loaded"), TEST_PID=str(child.pid))
            install = root / "ODS & quoted ' folder"
            install.mkdir()
            pid_file = install / "native.pid"
            model = install / "data" / "models" / "model & ' quoted.gguf"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            arguments = ["--model", str(model), "--port", "18081"]
            command = ["bash", str(SCRIPT), "start", str(install), "/bin/sleep", str(pid_file), *arguments]
            subprocess.run(command, env=env, check=True)
            plist = root / "Library/LaunchAgents/com.ods.llama-server.plist"
            payload = plistlib.loads(plist.read_bytes())
            self.assertEqual(payload["ProgramArguments"], ["/bin/sleep", *arguments])
            self.assertEqual(payload["WorkingDirectory"], str(install))
            self.assertEqual(pid_file.read_text().strip(), str(child.pid))
            command[2] = "stop"
            subprocess.run(command[:6], env=env, check=True)
            reaper.join(timeout=5)
            self.assertIsNotNone(child.poll())
            self.assertFalse(plist.exists())
            self.assertFalse(pid_file.exists())
            subprocess.run(command[:6], env=env, check=True)


if __name__ == "__main__":
    unittest.main()
