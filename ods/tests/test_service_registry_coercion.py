#!/usr/bin/env python3
"""Regression test for service-registry manifest scalar and null coercion."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_service_registry_coerces_scalar_and_null():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        ext_dir = tmp_root / "extensions" / "services"

        # Service 1: scalar string depends_on and aliases
        svc1 = ext_dir / "scalar-svc"
        svc1.mkdir(parents=True)
        (svc1 / "manifest.yaml").write_text("""schema_version: ods.services.v1
service:
  id: scalar-svc
  name: "Scalar Test Service"
  container_name: ods-scalar-svc
  aliases: webui-alias
  depends_on: base-service
""", encoding="utf-8")

        # Service 2: null (empty) depends_on and aliases
        svc2 = ext_dir / "null-svc"
        svc2.mkdir(parents=True)
        (svc2 / "manifest.yaml").write_text("""schema_version: ods.services.v1
service:
  id: null-svc
  name: "Null Test Service"
  container_name: ods-null-svc
  aliases: ~
  depends_on: ~
""", encoding="utf-8")

        script = f"""
export SCRIPT_DIR="{ROOT}"
. "{ROOT}/lib/service-registry.sh"
EXTENSIONS_DIR="{ext_dir}"
sr_load
printf 'SVC1_DEP:%s\\n' "${{SERVICE_DEPENDS[scalar-svc]}}"
printf 'SVC1_ALIAS:%s\\n' "${{SERVICE_ALIASES[webui-alias]}}"
printf 'SVC1_POLLUTED:%s\\n' "${{SERVICE_ALIASES[w]}}"
printf 'SVC2_ID:%s\\n' "${{SERVICE_CONTAINERS[null-svc]}}"
"""
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
        output = proc.stdout

        # Assertions
        assert "SVC1_DEP:base-service" in output, f"Expected 'SVC1_DEP:base-service', got output:\\n{output}\\nstderr:\\n{proc.stderr}"
        assert "SVC1_ALIAS:scalar-svc" in output, f"Expected 'SVC1_ALIAS:scalar-svc', got output:\\n{output}\\nstderr:\\n{proc.stderr}"
        assert "SVC1_POLLUTED:\n" in output or "SVC1_POLLUTED:" == output.splitlines()[2], f"Single-char alias 'w' should not be registered: {output}"
        assert "SVC2_ID:ods-null-svc" in output, f"Service with null fields should be loaded, got output:\\n{output}\\nstderr:\\n{proc.stderr}"
        print("[PASS] test_service_registry_coerces_scalar_and_null")


if __name__ == "__main__":
    test_service_registry_coerces_scalar_and_null()
