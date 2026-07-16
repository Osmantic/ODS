#!/usr/bin/env bash
# Regression coverage for scripts/validate-manifest-schema.sh.
# JSON Schema is the single source of truth; this test checks that the CLI
# returns the same valid/invalid result that service-manifest.v1.json returns.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATOR="$ROOT_DIR/scripts/validate-manifest-schema.sh"
SCHEMA="$ROOT_DIR/$(python3 - "$ROOT_DIR/manifest.json" <<'PY'
import json
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(manifest["contracts"]["extensions"]["serviceManifestSchema"])
PY
)"
LIBRARY_SCHEMA="$ROOT_DIR/extensions/library/schema/service-manifest.v1.json"

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

MISSING_DEPS_TMP="$(mktemp -d)"
trap 'rm -rf "$MISSING_DEPS_TMP"' EXIT
mkdir -p "$MISSING_DEPS_TMP/bin" "$MISSING_DEPS_TMP/manifests/service"
cat > "$MISSING_DEPS_TMP/bin/python3" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$MISSING_DEPS_TMP/bin/python3"
cat > "$MISSING_DEPS_TMP/manifests/service/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: service
  name: Service
  port: 8080
  health: /health
  type: docker
  category: optional
YAML
if PATH="$MISSING_DEPS_TMP/bin:$PATH" ODS_MANIFEST_DIRS="$MISSING_DEPS_TMP/manifests" bash "$VALIDATOR" >/tmp/validate-manifest-schema-missing-deps.log 2>&1; then
  echo "[FAIL] missing Python validation dependencies unexpectedly succeeded" >&2
  cat /tmp/validate-manifest-schema-missing-deps.log >&2
  exit 1
fi
if ! grep -q "PyYAML and jsonschema" /tmp/validate-manifest-schema-missing-deps.log; then
  echo "[FAIL] missing dependency message did not mention PyYAML and jsonschema" >&2
  cat /tmp/validate-manifest-schema-missing-deps.log >&2
  exit 1
fi
if grep -qi "Traceback" /tmp/validate-manifest-schema-missing-deps.log; then
  echo "[FAIL] missing dependency path should not print a Python traceback" >&2
  cat /tmp/validate-manifest-schema-missing-deps.log >&2
  exit 1
fi
echo "[PASS] missing Python validation dependencies fail with a clean message"

python3 - "$ROOT_DIR" "$VALIDATOR" "$SCHEMA" "$LIBRARY_SCHEMA" <<'PY'
import copy
import filecmp
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
LIBRARY_SCHEMA = Path(sys.argv[4])

try:
    import jsonschema
except ImportError as exc:  # pragma: no cover - minimal CI images should fail loudly
    raise SystemExit(
        "jsonschema is required for manifest schema source-of-truth tests; "
        "install the repo test dependencies or add jsonschema to the test image"
    ) from exc

if SCHEMA != ROOT / "extensions/schema/service-manifest.v1.json":
    raise SystemExit(f"[FAIL] manifest.json should declare extensions/schema/service-manifest.v1.json, got {SCHEMA}")
if not filecmp.cmp(SCHEMA, LIBRARY_SCHEMA, shallow=False):
    raise SystemExit(
        "[FAIL] library service manifest schema diverged from the manifest.json-declared schema; "
        "keep extensions/library/schema/service-manifest.v1.json synchronized or give it a distinct contract/$id"
    )
print("[PASS] manifest.json-declared and library schemas are synchronized")

schema = json.loads(SCHEMA.read_text())
validator_cls = jsonschema.validators.validator_for(schema)
validator_cls.check_schema(schema)
schema_validator = validator_cls(schema)

base_manifest = {
    "schema_version": "ods.services.v1",
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


def validator_ok(manifest):
    service = manifest.get("service") if isinstance(manifest, dict) else None
    service_id = service.get("id", "case") if isinstance(service, dict) else "case"
    with tempfile.TemporaryDirectory() as tmp:
        service_dir = Path(tmp) / str(service_id)
        service_dir.mkdir()
        (service_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
        result = subprocess.run(
            ["bash", str(VALIDATOR)],
            cwd=ROOT,
            env={**os.environ, "ODS_MANIFEST_DIRS": tmp},
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


def case(name, mutator):
    manifest = copy.deepcopy(base_manifest)
    mutator(manifest)
    cases.append((name, manifest))


cases = []

# Valid current-contract cases.
case("base manifest", lambda m: None)
case("cpu gpu backend", lambda m: (m["service"].update(gpu_backends=["cpu"]), m["features"][0].update(gpu_backends=["cpu"])))
case("host_network service may omit health", lambda m: (m["service"].update(id="hostnet-service", name="Host Network Service", host_network=True, port=0, gpu_backends=["none"]), m["service"].pop("health"), m["features"][0].update(id="hostnet-service", requirements={"services": ["hostnet-service"]}, gpu_backends=["none"])))
case("host-systemd service type", lambda m: m["service"].update(type="host-systemd"))
case("feature launch service", lambda m: m["features"][0].update(launch={"type": "service", "service": "test-service", "path": "/"}))
case("service startup timeout", lambda m: m["service"].update(startup_timeout=600))
case("feature vram_mb requirement", lambda m: m["features"][0]["requirements"].update(vram_mb=512))
case("missing features", lambda m: m.pop("features"))
case("missing service.gpu_backends", lambda m: m["service"].pop("gpu_backends"))
case("health warning still schema-valid", lambda m: m["service"].update(health="health"))
case("optional strings may be empty", lambda m: (m["service"].update(host_env="", default_host="", external_port_env="", description="", setup_hook=""), m["features"][0].update(setup_time="")))

# Historically drift-prone cases. The test intentionally does not hard-code
# expected validity here; service-manifest.v1.json decides, and the CLI must
# match that decision.
case("manifest without top-level service", lambda m: m.pop("service"))
case("service missing type", lambda m: m["service"].pop("type"))
case("service missing category", lambda m: m["service"].pop("category"))
case("null features", lambda m: m.update(features=None))
case("non-host-network service without health", lambda m: m["service"].pop("health"))
case("invalid service gpu backend", lambda m: m["service"].update(gpu_backends=["quantum"]))
case("empty service gpu_backends", lambda m: m["service"].update(gpu_backends=[]))
case("boolean service port", lambda m: m["service"].update(port=True))
case("empty service name", lambda m: m["service"].update(name=""))
case("string host_network without health", lambda m: (m["service"].update(host_network="true"), m["service"].pop("health")))
case("numeric health on host_network service", lambda m: m["service"].update(host_network=True, health=123))
case("env var without key", lambda m: m["service"].update(env_vars=[{"description": "missing key"}]))
case("env var with extra property", lambda m: m["service"].update(env_vars=[{"key": "FOO", "unexpected": "bar"}]))
case("invalid feature gpu backend", lambda m: m["features"][0].update(gpu_backends=["quantum"]))
case("missing feature required field", lambda m: m["features"][0].pop("description"))
case("invalid feature id", lambda m: m["features"][0].update(id="bad_id"))
case("numeric feature id", lambda m: m["features"][0].update(id=123))
case("invalid feature priority zero", lambda m: m["features"][0].update(priority=0))
case("boolean feature priority", lambda m: m["features"][0].update(priority=True))
case("empty feature description", lambda m: m["features"][0].update(description=""))
case("string feature requirements", lambda m: m["features"][0].update(requirements="gpu"))
case("feature launch bogus type", lambda m: m["features"][0].update(launch={"type": "bogus"}))
case("service startup timeout above maximum", lambda m: m["service"].update(startup_timeout=999))
case("service ui_path without slash", lambda m: m["service"].update(ui_path="dashboard"))
case("invalid tag format", lambda m: m.update(tags=["bad_tag"]))
case("invalid depends_on service id", lambda m: m["service"].update(depends_on=["bad_dep"]))
case("boolean external port default", lambda m: m["service"].update(external_port_default=True))
case("negative external port default", lambda m: m["service"].update(external_port_default=-1))
case("boolean container uid", lambda m: m["service"].update(container_uid=True))
case("zero container uid", lambda m: m["service"].update(container_uid=0))
case("numeric container name", lambda m: m["service"].update(container_name=123))
case("numeric service host env", lambda m: m["service"].update(host_env=123))
case("numeric service default host", lambda m: m["service"].update(default_host=123))
case("numeric service external port env", lambda m: m["service"].update(external_port_env=123))
case("numeric service description", lambda m: m["service"].update(description=123))
case("numeric service setup hook", lambda m: m["service"].update(setup_hook=123))
case("numeric feature setup time", lambda m: m["features"][0].update(setup_time=123))
case("string service aliases", lambda m: m["service"].update(aliases="abc"))
case("string service depends_on", lambda m: m["service"].update(depends_on="abc"))
case("string top-level tags", lambda m: m.update(tags="abc"))
case("negative feature vram_gb requirement", lambda m: m["features"][0]["requirements"].update(vram_gb=-1))
case("string feature enabled_services_all", lambda m: m["features"][0].update(enabled_services_all="svc"))

validate_real_manifests_with_schema()

failures = []
for name, manifest in cases:
    expected = schema_ok(manifest)
    actual, output = validator_ok(manifest)
    if actual != expected:
        failures.append(
            f"{name}: validator result {actual} did not match JSON Schema result {expected}\n"
            f"validator output:\n{output}"
        )
    else:
        print(f"[PASS] {name} ({'valid' if expected else 'invalid'} by JSON Schema)")

if failures:
    print("\n".join(failures), file=sys.stderr)
    raise SystemExit(1)

print("validate-manifest-schema source-of-truth tests passed")
PY
