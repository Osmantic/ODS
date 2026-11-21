import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installers/phases/11-services.sh"
SHA_ABC = hashlib.sha256(b"abc").hexdigest()

GGUF_START = "    # Download GGUF model if not already present"
GGUF_END = "    # The vLLM engine's model"
HELPER_START = "_phase11_model_sha256() {"
HELPER_END = "_phase11_yaml_double_quoted_scalar_content() {"


def _bash_available():
    if os.name == "nt":
        return False
    return shutil.which("bash") is not None


@unittest.skipUnless(_bash_available(), "bash unavailable or unsupported platform")
class Phase11ModelChecksumTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not INSTALLER.is_file():
            raise AssertionError(f"installer not found: {INSTALLER}")
        cls.source = INSTALLER.read_text(encoding="utf-8", errors="replace")
        try:
            h_start = cls.source.index(HELPER_START)
            h_end = cls.source.index(HELPER_END, h_start)
        except ValueError as exc:
            raise AssertionError(
                "missing real _phase11_model_sha256 helper markers"
            ) from exc
        if h_end <= h_start:
            raise AssertionError("helper markers out of order")
        cls.helper_src = cls.source[h_start:h_end]
        try:
            g_start = cls.source.index(GGUF_START)
            g_end = cls.source.index(GGUF_END, g_start)
        except ValueError as exc:
            raise AssertionError(
                "missing real GGUF block comment markers"
            ) from exc
        if g_end <= g_start:
            raise AssertionError("GGUF markers out of order")
        cls.gguf_src = cls.source[g_start:g_end]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.install_dir = self.base / "install"
        for sub in ("logs", "data/models"):
            (self.install_dir / sub).mkdir(parents=True, exist_ok=True)
        self.model = self.install_dir / "data/models/model.gguf"
        self.curl_log = self.base / "curl_calls.log"
        self.curl_log.write_text("", encoding="utf-8")
        self.curl_fixture = self.base / "curl_fixture.bin"
        self.curl_fixture.write_bytes(b"abc")
        self.python_cmd = sys.executable

    def _make_python_shim(self, body):
        shim = self.base / "python_shim.py"
        shim.write_text(f"#!{sys.executable}\nimport sys\nprint('TEST_HELPER_INVOKED', file=sys.stderr)\n" + textwrap.dedent(body), encoding="utf-8")
        shim.chmod(0o755)
        return shim

    def _build_script(self, gguf_sha256=SHA_ABC, extra_env=None):
        env = {
            "INSTALL_DIR": str(self.install_dir),
            "SCRIPT_DIR": str(ROOT),
            "ODS_PYTHON_CMD": self.python_cmd,
            "CURL_LOG": str(self.curl_log),
            "CURL_FIXTURE": str(self.curl_fixture),
            "GGUF_FILE": "model.gguf",
            "GGUF_URL": "https://example.invalid/model.gguf",
            "GGUF_SHA256": gguf_sha256,
            "_BOOTSTRAP_ACTIVE": "false",
            "LLM_MODEL_SIZE_MB": "1",
            "ODS_MODE": "local",
        }
        if extra_env:
            env.update(extra_env)
        env_lines = "\n".join(
            f"export {k}={shlex.quote(str(v))}" for k, v in env.items()
        )
        prelude = textwrap.dedent(
            """
            set -euo pipefail
            ods_progress() { echo "PROGRESS:$*" >&2; }
            ai() { echo "AI:$*" >&2; }
            ai_ok() { echo "AI_OK:$*" >&2; }
            ai_warn() { echo "AI_WARN:$*" >&2; }
            ai_bad() { echo "AI_BAD:$*" >&2; }
            signal() { :; }
            ui_status_line() { echo "UI:$*" >&2; }
            _phase11_host_native_llm() { return 1; }
            _phase11_external_llm() { return 1; }
            report_active_download_preserved() { :; }
            sleep() { :; }
            format_download_progress() { echo "dl-progress"; }
            download_part_bytes() { echo 0; }
            _phase11_download_hf_artifact() { return 1; }
            spin_task() {
                local pid="$1"
                wait "$pid"
            }
            curl() {
                local out=""
                while [ "$#" -gt 0 ]; do
                    case "$1" in
                        -o) out="$2"; shift 2 ;;
                        *) shift ;;
                    esac
                done
                if [ -e "${GGUF_DIR}/${GGUF_FILE}" ]; then
                    echo "CURL_SAW_EXISTING_MODEL" >&2
                    return 99
                fi
                echo "curl $*" >> "${CURL_LOG}"
                if [ -n "$out" ]; then
                    cp "${CURL_FIXTURE}" "$out"
                fi
                return 0
            }
            """
        )
        body = "\n".join(
            [
                "#!/usr/bin/env bash",
                env_lines,
                prelude,
                self.helper_src,
                self.gguf_src,
                "echo GGUF_BLOCK_DONE >&2",
            ]
        )
        script = self.base / "run.sh"
        script.write_text(body, encoding="utf-8")
        return script

    def _run(self, script, timeout=60):
        proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.base),
        )
        return proc

    def _curl_calls(self):
        return self.curl_log.read_text(encoding="utf-8")

    def _assert_no_download(self, proc):
        self.assertEqual(self._curl_calls(), "", f"unexpected curl: {proc.stderr}")

    def _assert_downloaded(self, proc):
        self.assertIn("curl", self._curl_calls(), proc.stderr)
        self.assertTrue(self.model.is_file(), proc.stderr)
        self.assertEqual(self.model.read_bytes(), self.curl_fixture.read_bytes())

    def test_valid_existing_no_download_retained(self):
        self.model.write_bytes(b"EXISTING-VALID")
        # Use real sha of existing content via helper-compatible python
        digest = hashlib.sha256(b"EXISTING-VALID").hexdigest()
        script = self._build_script(gguf_sha256=digest)
        proc = self._run(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_no_download(proc)
        self.assertEqual(self.model.read_bytes(), b"EXISTING-VALID")
        self.assertIn("GGUF_BLOCK_DONE", proc.stderr)

    def test_existing_mismatch_removed_then_downloaded(self):
        self.model.write_bytes(b"EXISTING-WRONG")
        script = self._build_script()
        proc = self._run(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_downloaded(proc)
        self.assertNotIn("CURL_SAW_EXISTING_MODEL", proc.stderr)

    def test_existing_helper_read_failure_fails_closed(self):
        self.model.write_bytes(b"EXISTING-KEEP")
        shim = self._make_python_shim(
            """
            import sys
            sys.exit(1)
            """
        )
        script = self._build_script(
            extra_env={"ODS_PYTHON_CMD": str(shim)}
        )
        proc = self._run(script)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self._assert_no_download(proc)
        self.assertEqual(self.model.read_bytes(), b"EXISTING-KEEP")
        self.assertIn("TEST_HELPER_INVOKED", proc.stderr)
        self.assertNotIn("AI_OK:", proc.stderr)
        self.assertNotIn("GGUF_BLOCK_DONE", proc.stderr)

    def test_existing_helper_malformed_success_fails_closed(self):
        self.model.write_bytes(b"EXISTING-KEEP2")
        shim = self._make_python_shim(
            """
            import sys
            sys.stdout.write("not-a-hash\\n")
            sys.exit(0)
            """
        )
        script = self._build_script(
            extra_env={"ODS_PYTHON_CMD": str(shim)}
        )
        proc = self._run(script)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self._assert_no_download(proc)
        self.assertEqual(self.model.read_bytes(), b"EXISTING-KEEP2")
        self.assertIn("TEST_HELPER_INVOKED", proc.stderr)
        self.assertNotIn("GGUF_BLOCK_DONE", proc.stderr)

    def test_fresh_valid_download_verified(self):
        script = self._build_script()
        proc = self._run(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_downloaded(proc)
        self.assertIn("GGUF_BLOCK_DONE", proc.stderr)

    def test_fresh_mismatch_aborts_and_removes(self):
        script = self._build_script(gguf_sha256="0" * 64)
        proc = self._run(script)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("curl", self._curl_calls(), proc.stderr)
        self.assertFalse(self.model.exists(), proc.stderr)
        self.assertNotIn("GGUF_BLOCK_DONE", proc.stderr)

    def test_fresh_helper_read_failure_retains_download(self):
        shim = self._make_python_shim(
            """
            import sys
            sys.exit(1)
            """
        )
        script = self._build_script(
            extra_env={"ODS_PYTHON_CMD": str(shim)}
        )
        proc = self._run(script)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("curl", self._curl_calls(), proc.stderr)
        self.assertTrue(self.model.is_file(), proc.stderr)
        self.assertEqual(self.model.read_bytes(), b"abc")
        self.assertIn("TEST_HELPER_INVOKED", proc.stderr)
        self.assertNotIn("GGUF_BLOCK_DONE", proc.stderr)

    def test_empty_expected_checksum_preserves_skip_behavior(self):
        self.model.write_bytes(b"EXISTING-SKIP")
        shim = self._make_python_shim(
            """
            import sys
            sys.stderr.write("HELPER_SHOULD_NOT_RUN\\n")
            sys.exit(42)
            """
        )
        script = self._build_script(
            gguf_sha256="", extra_env={"ODS_PYTHON_CMD": str(shim)}
        )
        proc = self._run(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._assert_no_download(proc)
        self.assertEqual(self.model.read_bytes(), b"EXISTING-SKIP")
        self.assertNotIn("HELPER_SHOULD_NOT_RUN", proc.stderr)
        self.assertNotIn("TEST_HELPER_INVOKED", proc.stderr)
        self.assertIn("GGUF_BLOCK_DONE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
