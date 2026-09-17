#!/usr/bin/env python3
"""Regression test for service-registry manifest UTF-8 parsing and GPU backend coercion."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

        # Run the parser snippet from service-registry.sh
        script = f"""
export SCRIPT_DIR="{ROOT}"
. "{ROOT}/lib/service-registry.sh"
EXTENSIONS_DIR="{ext_dir}"
sr_load
printf '%s\\n' "${{SERVICE_GPU_BACKENDS[sample-utf8-service]}}"
printf '%s\\n' "${{SERVICE_NAMES[sample-utf8-service]}}"
"""
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
        stdout = proc.stdout.splitlines()
        backends = stdout[0].strip()
        name = stdout[1].strip()

        assert backends == "all", f"Expected 'all', got {backends!r} (must not split into 'a l l')"
        assert "Élite Unicode" in name, f"Expected Unicode name preserved, got {name!r}"
        print("[PASS] test_manifest_utf8_and_gpu_backends")


if __name__ == "__main__":
    test_manifest_utf8_and_gpu_backends()
