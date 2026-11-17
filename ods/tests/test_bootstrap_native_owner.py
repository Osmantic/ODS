import json
import os
import select
import shlex
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "installers" / "macos" / "lib" / "native-llama-service.sh"



def _extract_block():
    text = Path(os.environ.get("BOOTSTRAP_UPGRADE_TEST_SOURCE", ROOT / "scripts" / "bootstrap-upgrade.sh")).read_text()
    start = text.index("# Capture old model path for rollback")
    end_marker = '\n        fi\n    fi\nelse\n    log "Docker services not running.'
    end = text.index(end_marker, start)
    return text[start:end]


class Harness:
    def __init__(self, tmp: Path, mode: str):
        self.tmp = tmp
        self.mode = mode
        self.home = tmp / "home dir"
        self.install = tmp / "install dir"
        self.home.mkdir()
        self.install.mkdir()
        self.models = self.install / "data" / "models"
        self.models.mkdir(parents=True)
        self.env_file = self.install / ".env"
        self.env_file.write_text("")
        self.pid_file = self.install / "llama.pid"
        self.log_file = tmp / "log.txt"
        self.status_file = tmp / "status.txt"
        self.record_file = tmp / "record.json"
        self.record_file.write_text("[]")
        self.llama_bin = self.install / "bin" / "llama-server"
        self.llama_bin.parent.mkdir()
        self.llama_bin.write_text("#!/bin/sh\nexit 0\n")
        self.llama_bin.chmod(0o755)
        self.old_model = self.models / "old model with spaces.gguf"
        self.old_model.write_text("x")
        self.new_model = self.models / "new model with spaces.gguf"
        self.new_model.write_text("x")
        self.stub = self.install / "installers" / "macos" / "lib" / "native-llama-service.sh"
        self.stub.parent.mkdir(parents=True)
        self.stub.write_text(self._stub_source())
        self.stub.chmod(0o755)
        self.script = tmp / "block.sh"
        self.script.write_text(self._script_source())
        self.script.chmod(0o755)
        self.bystander = None

    def _stub_source(self):
        return textwrap.dedent('''\
            #!/usr/bin/env bash
            set -uo pipefail
            action="$1"; shift
            python3 - "$RECORD_FILE" "$STUB_MODE" "$action" "$@" <<'PY'
            import json, sys
            path, mode, action, *args = sys.argv[1:]
            data = json.load(open(path))
            data.append({"action": action, "args": args, "mode": mode})
            json.dump(data, open(path, "w"))
            PY
            case "$STUB_MODE:$action" in
                startfail:start|cleanupfail:start|rollbackfail:start)
                    if [[ "$*" != *"old model with spaces.gguf"* || "$STUB_MODE" == rollbackfail ]]; then exit 1; fi
                    ;;
                cleanupfail:stop) exit 1 ;;
                missingpid:start) rm -f "$PID_FILE"; exit 0 ;;
            esac
            if [[ "$action" == start ]]; then
                printf '%s\\n' "$$" > "$PID_FILE"
            fi
            exit 0
        ''')

    def _script_source(self):
        return textwrap.dedent(f'''\
            #!/usr/bin/env bash
            set -uo pipefail
            HOME={shlex.quote(str(self.home))}
            INSTALL_DIR={shlex.quote(str(self.install))}
            MODELS_DIR={shlex.quote(str(self.models))}
            ENV_FILE={shlex.quote(str(self.env_file))}
            LLAMA_SERVER_BIN={shlex.quote(str(self.llama_bin))}
            LLAMA_SERVER_PID_FILE={shlex.quote(str(self.pid_file))}
            RECORD_FILE={shlex.quote(str(self.record_file))}
            STUB_MODE={shlex.quote(self.mode)}
            PID_FILE={shlex.quote(str(self.pid_file))}
            export RECORD_FILE STUB_MODE PID_FILE
            _model_path={shlex.quote(str(self.new_model))}
            _gguf_file="new model with spaces.gguf"
            _ctx_size=4096
            _llama_tuning_args=()
            _reasoning_fmt="none"
            TOTAL_BYTES=100
            HOT_SWAP_VERIFIED=false
            MACOS_LEGACY_PREVIOUS_GGUF="old model with spaces.gguf"
            log() {{ printf '%s\\n' "$*" >> {shlex.quote(str(self.log_file))}; }}
            write_status() {{ printf '%s\\n' "$*" >> {shlex.quote(str(self.status_file))}; }}
            curl() {{ printf 'curl %s\\n' "$*" >> {shlex.quote(str(self.log_file))}; [[ "$STUB_MODE" != healthfail ]]; }}
            sleep() {{ :; }}
            export -f log write_status curl sleep
            {_extract_block()}
        ''')

    def start_bystander(self):
        target = self.tmp / "ollama"
        # A renamed multicall coreutils binary dispatches on argv[0] and exits.
        # Compile an actual bystander on both platforms; this also avoids
        # relocating an Apple-signed executable on macOS.
        code = self.tmp / "bystander.c"
        code.write_text('#include <unistd.h>\nint main(void) { '
                        'if (write(1, "ready\\n", 6) != 6) return 1; '
                        'sleep(300); return 0; }\n')
        subprocess.run(["cc", str(code), "-o", str(target)], check=True, timeout=30,
                       capture_output=True, text=True)
        target.chmod(0o755)
        self.bystander = subprocess.Popen([str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready, _, _ = select.select([self.bystander.stdout], [], [], 5)
        if (not ready or self.bystander.stdout.read(6) != b"ready\n"
                or self.bystander.poll() is not None):
            raise RuntimeError("bystander failed to start before bootstrap execution")
        self.pid_file.write_text(str(self.bystander.pid))

    def run(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        return subprocess.run(["bash", str(self.script)], capture_output=True, text=True, env=env, timeout=30)

    def records(self):
        return json.loads(self.record_file.read_text())

    def cleanup(self):
        if self.bystander is not None:
            self.bystander.terminate()
            try:
                self.bystander.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.bystander.kill()
                self.bystander.wait()
            self.bystander.stdout.close()
            self.bystander.stderr.close()


class BootstrapNativeOwnerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.harness = None

    def tearDown(self):
        if self.harness is not None:
            self.harness.cleanup()
        self._tmp.cleanup()

    def _run(self, mode):
        self.harness = Harness(self.tmp, mode)
        self.harness.start_bystander()
        result = self.harness.run()
        self.assertTrue(self.harness.bystander.poll() is None, "bystander must stay alive")
        return result

    def test_success_no_stop(self):
        result = self._run("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [r["action"] for r in self.harness.records()]
        self.assertEqual(actions, ["start"])
        self.assertIn("SUCCESS", self.harness.log_file.read_text())
        self.assertIn("http://127.0.0.1:8080/health", self.harness.log_file.read_text())

    def test_healthfail_cleanup_then_rollback(self):
        result = self._run("healthfail")
        self.assertEqual(result.returncode, 1)
        records = self.harness.records()
        self.assertEqual([r["action"] for r in records], ["start", "stop", "start"])
        rollback_args = records[2]["args"]
        self.assertIn(str(self.harness.old_model), rollback_args)
        self.assertIn("--model", rollback_args)
        self.assertIn("Restart requested", self.harness.log_file.read_text())

    def test_startfail_curl_not_called(self):
        result = self._run("startfail")
        self.assertEqual(result.returncode, 1)
        log = self.harness.log_file.read_text()
        self.assertNotIn("curl", log)
        self.assertIn("WARNING: native-llama-service.sh start returned non-zero.", log)
        records = self.harness.records()
        self.assertEqual([r["action"] for r in records], ["start", "stop", "start"])
        self.assertIn(str(self.harness.old_model), records[2]["args"])

    def test_cleanupfail_prevents_rollback(self):
        result = self._run("cleanupfail")
        self.assertEqual(result.returncode, 1)
        actions = [r["action"] for r in self.harness.records()]
        self.assertEqual(actions, ["start", "stop"])
        self.assertIn("refusing to start rollback", self.harness.log_file.read_text())

    def test_rollbackfail_no_success_log(self):
        result = self._run("rollbackfail")
        self.assertEqual(result.returncode, 1)
        log = self.harness.log_file.read_text()
        self.assertNotIn("Restart requested", log)
        self.assertIn("Rollback start failed", log)

    def test_missingpid_still_success(self):
        result = self._run("missingpid")
        self.assertEqual(result.returncode, 0)
        self.assertIn("PID unknown", self.harness.log_file.read_text())

    def test_flags_passed_to_start(self):
        self._run("success")
        args = self.harness.records()[0]["args"]
        self.assertIn("--host", args)
        self.assertIn("127.0.0.1", args)
        self.assertIn("--port", args)
        self.assertIn("8080", args)
        self.assertIn("--metrics", args)

    def test_integration_real_service(self):
        self.harness = Harness(self.tmp, "success")
        self.harness.start_bystander()
        real = SERVICE.read_text()
        wrapper = self.harness.stub
        wrapper.write_text(real)
        wrapper.chmod(0o755)
        for name in ("macos_native_service.py", "pixel_macos_process.py"):
            destination = self.harness.install / "bin" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / "bin" / name).read_bytes())
        prior_pid = self.harness.pid_file.read_bytes()
        launchctl = self.tmp / "launchctl"
        launchctl.write_text("#!/bin/sh\nexit 1\n")
        launchctl.chmod(0o755)
        sleep_stub = self.tmp / "sleep"
        sleep_stub.write_text("#!/bin/sh\nexit 0\n")
        sleep_stub.chmod(0o755)
        env = dict(os.environ)
        env["HOME"] = str(self.harness.home)
        env["PATH"] = f"{self.tmp}:{env.get('PATH', '')}"
        result = subprocess.run(
            ["bash", str(self.harness.script)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("curl", self.harness.log_file.read_text())
        self.assertTrue(self.harness.bystander.poll() is None)
        self.assertEqual(self.harness.pid_file.read_bytes(), prior_pid)
        self.assertIn("Could not inspect native launchd domain", result.stderr)


if __name__ == "__main__":
    unittest.main()

