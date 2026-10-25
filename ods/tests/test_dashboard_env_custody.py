"""Regression test for issue #7961: .env custody and config state transitions.

Verifies:
1. write_status in scripts/bootstrap-upgrade.sh creates data/bootstrap-status.json
   with mode 0644 (world-readable, owner-writable) and valid JSON structure.
2. config.py logs structured warnings on PermissionError reading .env on state transition
   (first failure only, no repeated log flooding on subsequent polls).
3. get_env_file_read_error() records the diagnostic state without leaking secret values.
4. Recovery resets the diagnostic state and logs an info recovery message at most once.
5. Missing .env preserves existing compatibility without false permission errors.

Note: FastAPI / /api/status endpoint contract tests reside in
ods/extensions/services/dashboard-api/tests/test_dashboard_env_custody_api.py,
which runs in the dashboard-api CI job with locked service dependencies.
"""

import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get("ODS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
API_DIR = ROOT / "extensions/services/dashboard-api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import config


class TestDashboardEnvCustody(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.tmp = Path(self.temp_dir.name)
        self.original_install_dir = config.INSTALL_DIR
        config.INSTALL_DIR = str(self.tmp)
        self.original_logger_level = config.logger.level
        config.logger.setLevel(logging.INFO)
        config._reset_env_permission_state()
        self.addCleanup(self._restore_install_dir)

    def _restore_install_dir(self):
        config.INSTALL_DIR = self.original_install_dir
        config.logger.setLevel(self.original_logger_level)
        config._reset_env_permission_state()

    def test_bootstrap_status_mode_0644_and_valid_json(self):
        script_path = ROOT / "scripts/bootstrap-upgrade.sh"
        script_text = script_path.read_text(encoding="utf-8")
        start = script_text.find("write_status() {")
        self.assertNotEqual(
            start, -1, "write_status function not found in bootstrap-upgrade.sh"
        )
        end = script_text.find("\nstatus_percent() {", start)
        self.assertNotEqual(end, -1, "status_percent function boundary not found")
        fn_code = script_text[start:end]

        status_file = self.tmp / "data/bootstrap-status.json"
        runner_script = f"""set -euo pipefail
INSTALL_DIR={self.tmp}
STATUS_FILE={status_file}
FULL_GGUF_FILE="Qwen3.5-9B.gguf"
BOOTSTRAP_DOWNLOAD_GENERATION="gen-test-7961"
log() {{ :; }}
{fn_code}
write_status "downloading" "42.5" 425 1000 50 "10s"
"""
        res = subprocess.run(
            ["bash", "-c", runner_script], capture_output=True, text=True, check=False
        )
        self.assertEqual(
            res.returncode, 0, f"write_status execution failed: {res.stderr}"
        )
        self.assertTrue(status_file.exists(), "bootstrap-status.json was not created")

        mode = stat.S_IMODE(os.stat(status_file).st_mode)
        self.assertEqual(mode, 0o644, f"Expected file mode 0644, got {oct(mode)}")
        self.assertTrue(
            bool(os.stat(status_file).st_mode & stat.S_IROTH),
            "File is not readable by others",
        )

        data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("status"), "downloading")
        self.assertEqual(data.get("percent"), 42.5)
        self.assertEqual(data.get("bytesDownloaded"), 425)
        self.assertEqual(data.get("bytesTotal"), 1000)
        self.assertEqual(data.get("downloadGeneration"), "gen-test-7961")

    def test_env_permission_error_logged_and_diagnostic_state_set(self):
        env_file = self.tmp / ".env"
        secret_token = "sk-super-secret-fixture-key-999"
        env_file.write_text(
            f"SECRET_KEY={secret_token}\nLLM_MODEL=qwen\n", encoding="utf-8"
        )

        def mock_read_permission_denied(*args, **kwargs):
            raise PermissionError("Simulated EACCES on .env")

        log_records = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                log_records.append(self.format(record))

        handler = ListHandler()
        config.logger.addHandler(handler)
        self.addCleanup(lambda: config.logger.removeHandler(handler))

        with patch.object(Path, "read_text", mock_read_permission_denied):
            # 1. First failure must log exactly one warning
            found, val = config._find_env_file_value("LLM_MODEL")
            self.assertFalse(found)
            self.assertEqual(val, "")
            err = config.get_env_file_read_error()
            self.assertIsNotNone(err)
            self.assertIn("Permission denied", err)

            warning_logs = [
                r
                for r in log_records
                if "Permission denied reading" in r and "LLM_MODEL" in r
            ]
            self.assertEqual(
                len(warning_logs),
                1,
                "Expected exactly one warning on first permission failure",
            )
            for record in log_records:
                self.assertNotIn(secret_token, record, "Secret token leaked into logs")

            # 2. Repeated failures while unchanged must NOT log again
            values = config.read_live_env_values(("SECRET_KEY", "NONEXISTENT"))
            self.assertEqual(values.get("SECRET_KEY"), "")
            total_warnings = [r for r in log_records if "Permission denied" in r]
            self.assertEqual(
                len(total_warnings),
                1,
                "Subsequent read failures must not flood logs with additional warnings",
            )

    def test_env_recovery_resets_diagnostic_state_and_logs_info_once(self):
        log_records = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                log_records.append(self.format(record))

        handler = ListHandler()
        config.logger.addHandler(handler)
        self.addCleanup(lambda: config.logger.removeHandler(handler))

        # First trigger an error state
        with patch.object(
            Path, "read_text", side_effect=PermissionError("Simulated denial")
        ):
            config._find_env_file_value("KEY")
            self.assertIsNotNone(config.get_env_file_read_error())

        # Then read a valid file
        env_file = self.tmp / ".env"
        env_file.write_text("LLM_MODEL=valid-model-name\n", encoding="utf-8")

        found, val = config._find_env_file_value("LLM_MODEL")
        self.assertTrue(found)
        self.assertEqual(val, "valid-model-name")
        self.assertIsNone(
            config.get_env_file_read_error(),
            "Successful read must reset permission error state",
        )

        recovery_logs = [
            r
            for r in log_records
            if "Successfully read environment file" in r and "cleared previous" in r
        ]
        self.assertEqual(
            len(recovery_logs), 1, "Expected recovery log exactly once upon recovery"
        )

        # Subsequent reads must not log recovery again
        config.read_live_env_value("LLM_MODEL")
        recovery_logs_after = [
            r for r in log_records if "Successfully read environment file" in r
        ]
        self.assertEqual(
            len(recovery_logs_after),
            1,
            "Subsequent successful reads must not log recovery repeatedly",
        )

    def test_env_missing_file_preserves_compatibility(self):
        missing_env = self.tmp / ".env"
        if missing_env.exists():
            missing_env.unlink()

        log_records = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                log_records.append(self.format(record))

        handler = ListHandler()
        config.logger.addHandler(handler)
        self.addCleanup(lambda: config.logger.removeHandler(handler))

        found, val = config._find_env_file_value("ANY_KEY")
        self.assertFalse(found)
        self.assertEqual(val, "")
        self.assertIsNone(config.get_env_file_read_error())

        values = config.read_live_env_values(("KEY_A", "KEY_B"))
        self.assertEqual(values, {"KEY_A": "", "KEY_B": ""})
        self.assertIsNone(config.get_env_file_read_error())

        warning_logs = [r for r in log_records if "Permission denied" in r]
        self.assertEqual(
            warning_logs, [], "Missing file should not produce permission warnings"
        )


if __name__ == "__main__":
    unittest.main()
