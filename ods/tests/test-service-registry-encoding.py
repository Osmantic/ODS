#!/usr/bin/env python3
"""Regression test for service-registry manifest UTF-8 parsing and GPU backend coercion."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# On Windows, CreateProcess searches System32 before PATH, so a bare "bash"
# resolves to the WSL launcher (C:\Windows\System32\bash.exe), which cannot
# see C:/ paths. shutil.which honors PATH order and finds Git Bash first.
BASH = shutil.which("bash") or "bash"


def test_manifest_utf8_and_gpu_backends():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        ext_dir = tmp_root / "extensions" / "services"
        svc = ext_dir / "sample-utf8-service"
        svc.mkdir(parents=True)
        manifest = svc / "manifest.yaml"

        manifest.write_text("""schema_version: ods.services.v1
service:
  id: sample-utf8-service
  name: "Sample — Service (Élite Unicode) 🚀"
  description: "Unicode description with em-dash — and accents: café"
  container_name: ods-sample
  port: 8080
  gpu_backends: all
""", encoding="utf-8")

        # Run the parser snippet from service-registry.sh. Force a legacy
        # non-UTF-8 stdio encoding (Windows cp1252 / POSIX C locale equivalent)
        # so the regression cannot hide behind the host's UTF-8 default: before
        # the fix, print() crashed on the em-dash and the service vanished from
        # the generated registry.
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        # Git Bash on Windows cannot source backslash paths; emit POSIX form.
        root_posix = ROOT.as_posix()
        ext_posix = ext_dir.as_posix()
        script = f"""
export SCRIPT_DIR="{root_posix}"
. "{root_posix}/lib/service-registry.sh"
EXTENSIONS_DIR="{ext_posix}"
sr_load
printf '%s\\n' "${{SERVICE_GPU_BACKENDS[sample-utf8-service]}}"
printf '%s\\n' "${{SERVICE_NAMES[sample-utf8-service]}}"
"""
        proc = subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=True,
        )
        stdout = proc.stdout.splitlines()
        backends = stdout[0].strip()
        name = stdout[1].strip()

        assert backends == "all", f"Expected 'all', got {backends!r} (must not split into 'a l l')"
        assert "Élite Unicode" in name, f"Expected Unicode name preserved, got {name!r}"
        print("[PASS] test_manifest_utf8_and_gpu_backends")


if __name__ == "__main__":
    test_manifest_utf8_and_gpu_backends()
