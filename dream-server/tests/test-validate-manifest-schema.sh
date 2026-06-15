#!/usr/bin/env bash
# Regression coverage for scripts/validate-manifest-schema.sh.
# Keeps the custom manifest validator aligned with service-manifest.v1.json.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATOR="$ROOT_DIR/scripts/validate-manifest-schema.sh"
SCHEMA="$ROOT_DIR/extensions/library/schema/service-manifest.v1.json"

assert_success() {
  local label="$1"
  shift
  if ! "$@" >/tmp/validate-manifest-schema-success.log 2>&1; then
    echo "[FAIL] $label" >&2
    cat /tmp/validate-manifest-schema-success.log >&2
    exit 1
  fi
  echo "[PASS] $label"
}

assert_success "current bundled and library manifests validate" bash "$VALIDATOR"

python3 - "$ROOT_DIR" "$VALIDATOR" "$SCHEMA" <<'PY'
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(sys.argv[1])
VALIDATOR = Path(sys.argv[2])
SCHEMA = Path(sys.argv[3])

try:
    import jsonschema
except ImportError as exc:  # pragma: no cover - minimal CI images should fail loudly
    raise SystemExit(
        "jsonschema is required for manifest schema parity tests; "
        "install the repo test dependencies or add jsonschema to the test image"
    ) from exc

schema = json.loads(SCHEMA.read_text())
schema_validator = jsonschema.Draft202012Validator(schema)

base_manifest = {
    "schema_version": "dream.services.v1",
    "service": {
        "id": "test-service",
        "name": "Test Service",
        "port": 8080,
        "health": "/health",
        "type": "docker",
        "category": "optional",
        "gpu_backends": ["all"],
    },
    "features": [
        {
            "id": "test-service",
            "name": "Test Feature",
            "description": "Test feature",
            "icon": "Box",
            "category": "testing",
            "requirements": {"services": ["test-service"]},
            "priority": 1,
            "gpu_backends": ["all"],
        }
    ],
}


def schema_ok(manifest):
    return not list(schema_validator.iter_errors(manifest))


def custom_ok(manifest):
    with tempfile.TemporaryDirectory() as tmp:
        service_dir = Path(tmp) / manifest.get("service", {}).get("id", "case")
        service_dir.mkdir()
        (service_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
        result = subprocess.run(
            ["bash", str(VALIDATOR)],
            cwd=ROOT,
            env={**os.environ, "DREAM_MANIFEST_DIRS": tmp},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.returncode == 0, result.stdout + result.stderr


def validate_real_manifests_with_schema():
    failures = []
    for root in [ROOT / "extensions/services", ROOT / "extensions/library/services"]:
        for path in sorted(root.glob("*/manifest.y*ml")):
            manifest = yaml.safe_load(path.read_text())
            for error in schema_validator.iter_errors(manifest):
                location = ".".join(str(part) for part in error.path) or "<root>"
                failures.append(f"{path}: {location}: {error.message}")
    if failures:
        print("[FAIL] current bundled and library manifests satisfy JSON schema", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        raise SystemExit(1)
    print("[PASS] current bundled and library manifests satisfy JSON schema")


def case(name, expected, mutator):
    manifest = copy.deepcopy(base_manifest)
    mutator(manifest)
    cases.append((name, expected, manifest))


cases = []

# Valid current-contract cases.
case("cpu gpu backend", True, lambda m: (m["service"].update(gpu_backends=["cpu"]), m["features"][0].update(gpu_backends=["cpu"])))
case("host_network service may omit health", True, lambda m: (m["service"].update(id="hostnet-service", name="Host Network Service", host_network=True, port=0, gpu_backends=["none"]), m["service"].pop("health"), m["features"][0].update(id="hostnet-service", requirements={"services": ["hostnet-service"]}, gpu_backends=["none"])))
case("host-systemd service type", True, lambda m: m["service"].update(type="host-systemd"))
case("missing features remains allowed", True, lambda m: m.pop("features"))
case("missing service.gpu_backends remains allowed", True, lambda m: m["service"].pop("gpu_backends"))

# Invalid cases that previously exposed drift between JSON schema and the custom validator.
case("manifest without top-level service", False, lambda m: m.pop("service"))
case("non-host-network service without health", False, lambda m: m["service"].pop("health"))
case("invalid service gpu backend", False, lambda m: m["service"].update(gpu_backends=["quantum"]))
case("empty service gpu_backends", False, lambda m: m["service"].update(gpu_backends=[]))
case("boolean service port", False, lambda m: m["service"].update(port=True))
case("empty service name", False, lambda m: m["service"].update(name=""))
case("string host_network without health", False, lambda m: (m["service"].update(host_network="true"), m["service"].pop("health")))
case("env var without key", False, lambda m: m["service"].update(env_vars=[{"description": "missing key"}]))
case("env var with extra property", False, lambda m: m["service"].update(env_vars=[{"key": "FOO", "unexpected": "bar"}]))
case("invalid feature gpu backend", False, lambda m: m["features"][0].update(gpu_backends=["quantum"]))
case("missing feature required field", False, lambda m: m["features"][0].pop("description"))
case("invalid feature id", False, lambda m: m["features"][0].update(id="bad_id"))
case("invalid feature priority zero", False, lambda m: m["features"][0].update(priority=0))
case("boolean feature priority", False, lambda m: m["features"][0].update(priority=True))
case("empty feature description", False, lambda m: m["features"][0].update(description=""))
case("string feature requirements", False, lambda m: m["features"][0].update(requirements="gpu"))
case("invalid tag format", False, lambda m: m.update(tags=["bad_tag"]))

validate_real_manifests_with_schema()

failures = []
for name, expected, manifest in cases:
    schema_result = schema_ok(manifest)
    custom_result, custom_output = custom_ok(manifest)
    if schema_result != expected:
        failures.append(f"{name}: expected JSON schema result {expected}, got {schema_result}")
    if custom_result != expected:
        failures.append(
            f"{name}: expected custom validator result {expected}, got {custom_result}\n"
            f"custom output:\n{custom_output}"
        )
    if schema_result != custom_result:
        failures.append(f"{name}: JSON schema/custom validator drift ({schema_result} != {custom_result})")
    if not failures:
        print(f"[PASS] {name}")

if failures:
    print("\n".join(failures), file=sys.stderr)
    raise SystemExit(1)

print("validate-manifest-schema regression tests passed")
PY
