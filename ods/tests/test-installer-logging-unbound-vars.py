#!/usr/bin/env python3
"""Regression test for installers/lib/logging.sh behavior under set -u and unset LOG_FILE."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
LOGGING_SH = ROOT_DIR / "installers" / "lib" / "logging.sh"


class LoggingUnboundVarsTests(unittest.TestCase):
    def test_logging_under_set_u_without_predefined_variables(self):
        """logging.sh must not trigger unbound variable errors when sourced under set -u."""
        script = f"""
        set -u
        source "{LOGGING_SH}"
        install_elapsed
        log "info message"
        success "success message"
        warn "warning message"
        """
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"Failed with: {result.stderr}")
        self.assertIn("[INFO] info message", result.stdout)
        self.assertIn("[OK] success message", result.stdout)
        self.assertIn("[WARN] warning message", result.stdout)
        self.assertIn("0m 00s", result.stdout)

    def test_logging_appends_to_log_file_when_configured(self):
        """When LOG_FILE is defined, log entries are mirrored to the file."""
        with tempfile.NamedTemporaryFile(mode="r+", delete=True) as tmp:
            script = f"""
            set -u
            export LOG_FILE="{tmp.name}"
            source "{LOGGING_SH}"
            log "written to disk"
            """
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"Failed with: {result.stderr}")
            content = Path(tmp.name).read_text()
            self.assertIn("[INFO] written to disk", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
