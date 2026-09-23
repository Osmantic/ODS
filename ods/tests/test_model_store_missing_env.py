"""model-store-compose-flags must handle missing .env file gracefully and use default identifier."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "model-store-compose-flags.py"


class ModelStoreMissingEnvTests(unittest.TestCase):
    def test_missing_env_file_uses_default_identifier(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            host_dir = root / "external_models"
            host_dir.mkdir()
            registry = {
                "schemaVersion": 1,
                "stores": [{"id": "external", "hostPath": str(host_dir), "containerPath": "/model-stores/external"}]
            }
            (root / "data/model-stores.json").write_text(json.dumps(registry), encoding="utf-8")
            overlay = {
                "services": {
                    "dashboard-api": {
                        "volumes": [{"type": "bind", "source": str(host_dir), "target": "/model-stores/external", "read_only": True, "bind": {"create_host_path": False}}]
                    }
                }
            }
            (root / ".model-stores.compose.json").write_text(json.dumps(overlay), encoding="utf-8")
            # Note: root / .env does NOT exist

            cmd = ["python3", str(SCRIPT), "--install-dir", str(root), "--flags", "-f docker-compose.yml"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            flags = json.loads(res.stdout)
            self.assertIn("-f", flags)
            self.assertIn(".model-stores.compose.json", flags)


if __name__ == "__main__":
    unittest.main()
