"""Regression test for Issue #5712:
resolve-compose-stack.sh must tolerate manifests where 'service' is not a mapping.
"""

import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "resolve-compose-stack.sh"


def test_malformed_service_mapping_scalar_does_not_crash():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        user_ext_dir = tmp_path / "data" / "user-extensions" / "badsvc"
        user_ext_dir.mkdir(parents=True)

        manifest_file = user_ext_dir / "manifest.yaml"
        manifest_file.write_text(
            'schema_version: "ods.services.v1"\n'
            'service: "oops"\n'
        )
        compose_file = user_ext_dir / "compose.yaml"
        compose_file.write_text("services: {}\n")

        res = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--script-dir",
                str(tmp_path),
                "--tier",
                "1",
                "--gpu-backend",
                "cpu",
                "--env",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Resolver failed: {res.stderr}"
        assert "AttributeError" not in res.stderr
        assert "malformed 'service' mapping for badsvc" in res.stderr


def test_malformed_service_mapping_list_does_not_crash():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        user_ext_dir = tmp_path / "data" / "user-extensions" / "listsvc"
        user_ext_dir.mkdir(parents=True)

        manifest_file = user_ext_dir / "manifest.yaml"
        manifest_file.write_text(
            'schema_version: "ods.services.v1"\n'
            'service:\n'
            '  - invalid\n'
            '  - array\n'
        )
        compose_file = user_ext_dir / "compose.yaml"
        compose_file.write_text("services: {}\n")

        res = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--script-dir",
                str(tmp_path),
                "--tier",
                "1",
                "--gpu-backend",
                "cpu",
                "--env",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Resolver failed: {res.stderr}"
        assert "AttributeError" not in res.stderr
        assert "malformed 'service' mapping for listsvc" in res.stderr


if __name__ == "__main__":
    test_malformed_service_mapping_scalar_does_not_crash()
    test_malformed_service_mapping_list_does_not_crash()
    print("test_resolve_compose_service_mapping: OK")
