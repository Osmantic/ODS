import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

WRAPPER = (
    Path(__file__).resolve().parents[1]
    / "extensions/services/llama-server/llama-server-entrypoint.sh"
)

# Fake server: prints a JSON line with argv/pid/env, flushes, then either
# exits with FAKE_EXIT or waits for SIGTERM via signal.pause().
FAKE_SERVER_SOURCE = """#!{python}
import json, os, signal, sys
print(json.dumps({{
    "argv": sys.argv,
    "pid": os.getpid(),
    "reasoning_present": "LLAMA_ARG_REASONING" in os.environ,
    "reasoning_value": os.environ.get("LLAMA_ARG_REASONING"),
}}), flush=True)
mode = os.environ.get("FAKE_MODE", "exit")
if mode == "wait":
    while True:
        signal.pause()
else:
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


class LlamaReasoningEntrypointTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WRAPPER.is_file(), f"wrapper missing: {WRAPPER}")
        self.assertFalse(
            os.path.exists("/app/llama-server") and os.access("/app/llama-server", os.X_OK),
            "unit test host must not have executable /app/llama-server",
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.bindir = self.tmpdir / "bin"
        self.bindir.mkdir()
        fake = self.bindir / "llama-server"
        fake.write_text(FAKE_SERVER_SOURCE.format(python=sys.executable))
        fake.chmod(0o755)
        self.fake = fake

    def _env(self, reasoning=..., extra=None):
        env = {k: v for k, v in os.environ.items() if k not in ("LLAMA_ARG_REASONING", "FAKE_MODE", "FAKE_EXIT")}
        env["PATH"] = f"{self.bindir}{os.pathsep}{env.get('PATH', '')}"
        if reasoning is not ...:
            env["LLAMA_ARG_REASONING"] = reasoning
        if extra:
            env.update(extra)
        return env

    def _run(self, args, reasoning=..., extra=None):
        return subprocess.run(
            ["/bin/sh", str(WRAPPER), *args],
            env=self._env(reasoning, extra),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _run_json(self, args, reasoning=..., extra=None):
        proc = self._run(args, reasoning, extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _read_json_line(self, proc, timeout=10.0):
        """Read one JSON line from proc.stdout with a bounded timeout."""
        sel = selectors.DefaultSelector()
        try:
            sel.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            buf = b""
            while b"\n" not in buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail("timed out waiting for fake server output")
                events = sel.select(timeout=remaining)
                if not events:
                    self.fail("timed out waiting for fake server output")
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    self.fail(f"fake server closed stdout before newline; got {buf!r}")
                buf += chunk
            line, _, _rest = buf.partition(b"\n")
            return json.loads(line.decode("utf-8"))
        finally:
            sel.close()

    def _kill_group(self, proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            # The scoped process group already exited; no child remains.
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.fail("process did not exit after SIGKILL")

    def test_case_insensitive_off(self):
        for value in ("OFF", "Off", "oFf", "off"):
            with self.subTest(value=value):
                data = self._run_json([], reasoning=value)
                self.assertTrue(data["reasoning_present"])
                self.assertEqual(data["reasoning_value"], "off")

    def test_case_insensitive_on(self):
        for value in ("ON", "oN", "On", "on"):
            with self.subTest(value=value):
                data = self._run_json([], reasoning=value)
                self.assertTrue(data["reasoning_present"])
                self.assertEqual(data["reasoning_value"], "on")

    def test_case_insensitive_auto(self):
        for value in ("AUTO", "Auto", "aUtO", "auto"):
            with self.subTest(value=value):
                data = self._run_json([], reasoning=value)
                self.assertTrue(data["reasoning_present"])
                self.assertEqual(data["reasoning_value"], "auto")

    def test_unknown_values_unchanged(self):
        for value in ("custom-format", " OFF ", "on;echo hi", "$(echo hi)", "`echo hi`", "*", "on off"):
            with self.subTest(value=value):
                data = self._run_json([], reasoning=value)
                self.assertTrue(data["reasoning_present"])
                self.assertEqual(data["reasoning_value"], value)

    def test_empty_vs_unset(self):
        empty = self._run_json([], reasoning="")
        self.assertTrue(empty["reasoning_present"])
        self.assertEqual(empty["reasoning_value"], "")

        unset = self._run_json([], reasoning=...)
        self.assertFalse(unset["reasoning_present"])
        self.assertIsNone(unset["reasoning_value"])

    def test_argv_preserved(self):
        args = ["a b", "c'd", 'e"f', "$HOME", "`echo hi`", "*", "x\ty", ""]
        data = self._run_json(args, reasoning="on")
        # sys.argv[0] is the resolved fake executable path, not literal "llama-server".
        self.assertEqual(data["argv"][0], str(self.fake))
        self.assertEqual(data["argv"][1:], args)

    def test_child_exit_code_propagates(self):
        proc = self._run([], reasoning="on", extra={"FAKE_EXIT": "42"})
        self.assertEqual(proc.returncode, 42)

    def test_pid_matches_and_sigterm(self):
        env = self._env("on", extra={"FAKE_MODE": "wait"})
        proc = subprocess.Popen(
            ["/bin/sh", str(WRAPPER)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            data = self._read_json_line(proc, timeout=10.0)
            self.assertEqual(data["pid"], proc.pid)
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
            self.assertEqual(proc.returncode, -signal.SIGTERM)
        finally:
            self._kill_group(proc)
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
