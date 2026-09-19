"""Capability profile generation without service registry must not fail on unbound associative arrays."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def test_capability_profile_runs_without_service_registry():
    repo_root = Path(__file__).resolve().parents[1]
    source_script = repo_root / "scripts/build-capability-profile.sh"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True)
        target_script = scripts_dir / "build-capability-profile.sh"
        shutil.copyfile(source_script, target_script)
        target_script.chmod(0o755)

        lib_dir = root / "lib"
        lib_dir.mkdir(parents=True)
        shutil.copyfile(repo_root / "lib/safe-env.sh", lib_dir / "safe-env.sh")

        detect = scripts_dir / "detect-hardware.sh"
        detect.write_text("""#!/usr/bin/env bash
echo '{"os":"linux","gpu":{"type":"cpu"},"ram_gb":16}'
""")
        detect.chmod(0o755)

        classify = scripts_dir / "classify-hardware.sh"
        classify.write_text("""#!/usr/bin/env bash
echo 'HW_CLASS_ID=cpu-only'
echo 'HW_CLASS_LABEL=CPU Only'
echo 'HW_REC_BACKEND=cpu'
echo 'HW_REC_TIER=T1'
""")
        classify.chmod(0o755)

        out_json = root / "capabilities.json"
        result = subprocess.run(
            ["bash", str(target_script), "--output", str(out_json)],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert out_json.exists()
        data = json.loads(out_json.read_text())
        assert data.get("runtime", {}).get("llm_api_port") == 11434


if __name__ == "__main__":
    test_capability_profile_runs_without_service_registry()
    print("test_capability_unbound_ports passed.")
