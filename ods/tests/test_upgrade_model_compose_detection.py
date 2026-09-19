"""upgrade-model must detect apple and cloud compose overlays."""
import subprocess
import tempfile
import unittest
from pathlib import Path

class UpgradeModelComposeTests(unittest.TestCase):
    def test_apple_overlay_detected(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "upgrade-model.sh"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "docker-compose.base.yml").write_text("services: {}\n")
            (tmp / "docker-compose.apple.yml").write_text("services: {}\n")
            cmd = f"""
            eval "$(sed -n '/^detect_compose_file()/,/^}}/p' {script})"
            ODS_DIR={tmp}
            detect_compose_file
            echo "${{COMPOSE_FILE_ARGS[*]}}"
            """
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("docker-compose.apple.yml", res.stdout)

if __name__ == "__main__":
    unittest.main()
