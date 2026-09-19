"""Model-store Compose flag resolution must tolerate missing .env and handle export prefix."""

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("store_flags", ROOT / "scripts/model-store-compose-flags.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _setup_store_tree(root: Path):
    (root / "data/models").mkdir(parents=True)
    ssd = root / "External SSD"
    ssd.mkdir()
    registry = {
        "schemaVersion": 1,
        "stores": [{"id": "ssd", "hostPath": str(ssd), "containerPath": "/model-stores/ssd"}],
    }
    (root / "data/model-stores.json").write_text(json.dumps(registry))
    overlay = {
        "services": {
            "dashboard-api": {
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(ssd),
                        "target": "/model-stores/ssd",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    }
                ]
            }
        }
    }
    (root / ".model-stores.compose.json").write_text(json.dumps(overlay))


def test_missing_env_file_defaults_cleanly():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _setup_store_tree(root)

        # .env is deliberately absent
        flags = module.resolve_flags(root, ["-f", "base.yml"])
        assert flags == ["-f", "base.yml", "-f", ".model-stores.compose.json"]


def test_export_prefixed_active_model_store():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _setup_store_tree(root)

        # .env uses export prefix
        (root / ".env").write_text('export ODS_ACTIVE_MODEL_STORE="ssd"\n')
        flags = module.resolve_flags(root, ["-f", "base.yml"])
        assert flags == [
            "-f",
            "base.yml",
            "-f",
            ".model-stores.compose.json",
            "-f",
            "data/.active-model-store.compose.json",
        ]


if __name__ == "__main__":
    test_missing_env_file_defaults_cleanly()
    test_export_prefixed_active_model_store()
    print("test_model_store_compose_flags_env: OK")
