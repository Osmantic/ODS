"""Migrate-config version comparator must tolerate uppercase V and whitespace."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class MigrateConfigSemverTests(unittest.TestCase):
    def setUp(self):
        self.script_path = (
            Path(__file__).resolve().parents[1] / "scripts" / "migrate-config.sh"
        )

    def test_check_handles_uppercase_v_and_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            scripts_dir = tmp / "ods" / "scripts"
            scripts_dir.mkdir(parents=True)
            script_copy = scripts_dir / "migrate-config.sh"
            script_copy.write_text(self.script_path.read_text())

            (tmp / ".version").write_text("V2.1.0\n")
            (tmp / ".migration-state").write_text(" 2.0.0 \n")

            env = os.environ.copy()
            env["INSTALL_DIR"] = str(tmp)
            env["DATA_DIR"] = str(tmp)
            res = subprocess.run(
                ["bash", str(script_copy), "check"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            # Under set -u, the unpatched baseline crashes with:
            # 'line 88: V2: unbound variable' (exit 1)
            # The fixed script cleanly evaluates without unbound variable error.
            self.assertNotIn("unbound variable", res.stderr)
            self.assertIn("Migration needed", res.stdout)


if __name__ == "__main__":
    unittest.main()
