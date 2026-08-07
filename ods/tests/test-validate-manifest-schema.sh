#!/usr/bin/env bash
# Verify that manifest validation is driven by the canonical JSON Schema.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATOR="$ROOT_DIR/scripts/validate-manifest-schema.sh"
SCHEMA="$ROOT_DIR/extensions/schema/service-manifest.v1.json"
MIRROR="$ROOT_DIR/extensions/library/schema/service-manifest.v1.json"
TMP_DIR="$(mktemp -d)"
CASE_ROOT="$TMP_DIR/manifests"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() {
    echo "[PASS] $1"
}

fail() {
    echo "[FAIL] $1" >&2
    exit 1
}

assert_success() {
    local label="$1"
    shift
    if "$@" >"$TMP_DIR/command.log" 2>&1; then
        pass "$label"
    else
        cat "$TMP_DIR/command.log" >&2
        fail "$label"
    fi
}

assert_success "generated library schema mirror is current" \
    python3 "$ROOT_DIR/scripts/sync-manifest-schema.py" --check
CUSTOM_MIRROR="$TMP_DIR/custom-schema.json"
assert_success "custom schema mirror can be generated" \
    python3 "$ROOT_DIR/scripts/sync-manifest-schema.py" --output "$CUSTOM_MIRROR"
cmp "$SCHEMA" "$CUSTOM_MIRROR" || fail "custom schema mirror differs from canonical schema"
assert_success "custom schema mirror can be checked" \
    python3 "$ROOT_DIR/scripts/sync-manifest-schema.py" --check --output "$CUSTOM_MIRROR"
assert_success "bundled and library manifests validate together" \
    bash "$VALIDATOR"
assert_success "standalone library validator accepts all library manifests" \
    python3 "$ROOT_DIR/extensions/library/validate-manifests.py"

python3 - "$ROOT_DIR" "$MIRROR" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
mirror = Path(sys.argv[2])
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
canonical = root / manifest["contracts"]["extensions"]["serviceManifestSchema"]
assert canonical.read_bytes() == mirror.read_bytes()

module_path = root / "extensions" / "library" / "validate-manifests.py"
spec = importlib.util.spec_from_file_location("library_manifest_validator", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.schema_path().resolve() == canonical.resolve()

module.MANIFEST_FILE = root / "does-not-exist.json"
assert module.schema_path().resolve() == mirror.resolve()
print("[PASS] library validator prefers the canonical contract and retains standalone fallback")
PY

MISSING_DEPS_DIR="$TMP_DIR/missing-deps"
mkdir -p "$MISSING_DEPS_DIR"
cat > "$MISSING_DEPS_DIR/python3" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$MISSING_DEPS_DIR/python3"
if env -u 'BASH_FUNC_python3%%' PATH="$MISSING_DEPS_DIR:$PATH" bash "$VALIDATOR" \
    >"$TMP_DIR/missing-deps.log" 2>&1; then
    fail "missing Python validation dependencies unexpectedly succeeded"
fi
grep -q "PyYAML and jsonschema" "$TMP_DIR/missing-deps.log" ||
    fail "missing dependency error does not name PyYAML and jsonschema"
if grep -qi "Traceback" "$TMP_DIR/missing-deps.log"; then
    fail "missing dependency error printed a traceback"
fi
pass "missing validation dependencies fail with an actionable message"

write_base_manifest() {
    mkdir -p "$CASE_ROOT/case"
    cat > "$CASE_ROOT/case/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: test-service
  name: Test Service
  port: 8080
  health: /health
  type: docker
  category: optional
  gpu_backends: [cpu]
YAML
}

mutate_manifest() {
    local operation="$1"
    python3 - "$CASE_ROOT/case/manifest.yaml" "$operation" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
operation = sys.argv[2]
manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
service = manifest.get("service", {})

if operation == "host-systemd":
    service["type"] = "host-systemd"
elif operation == "host-network":
    service["host_network"] = True
    service.pop("health", None)
elif operation == "none-backend":
    service["gpu_backends"] = ["none"]
elif operation == "missing-service":
    manifest.pop("service", None)
elif operation == "boolean-port":
    service["port"] = True
elif operation == "unknown-backend":
    service["gpu_backends"] = ["quantum"]
else:
    raise SystemExit(f"unknown fixture mutation: {operation}")

path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
PY
}

check_case() {
    local label="$1"
    local expected="$2"
    local schema_rc validator_rc

    set +e
    python3 - "$SCHEMA" "$CASE_ROOT/case/manifest.yaml" <<'PY'
import json
import sys

import jsonschema
import yaml

schema = json.loads(open(sys.argv[1], encoding="utf-8").read())
manifest = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
validator_cls = jsonschema.validators.validator_for(schema)
validator_cls.check_schema(schema)
raise SystemExit(1 if list(validator_cls(schema).iter_errors(manifest)) else 0)
PY
    schema_rc=$?
    ODS_MANIFEST_DIRS="$CASE_ROOT" bash "$VALIDATOR" >"$TMP_DIR/validator-case.log" 2>&1
    validator_rc=$?
    set -e

    if [[ "$expected" == "valid" ]]; then
        [[ $schema_rc -eq 0 ]] || fail "$label: canonical schema rejected fixture"
        [[ $validator_rc -eq 0 ]] || {
            cat "$TMP_DIR/validator-case.log" >&2
            fail "$label: validator rejected schema-valid fixture"
        }
    else
        [[ $schema_rc -ne 0 ]] || fail "$label: canonical schema accepted invalid fixture"
        [[ $validator_rc -ne 0 ]] || fail "$label: validator accepted schema-invalid fixture"
    fi
    pass "$label"
}

write_base_manifest
check_case "minimal docker manifest" valid

mutate_manifest host-systemd
check_case "host-systemd service type" valid

write_base_manifest
mutate_manifest host-network
check_case "host-network service may omit health" valid

write_base_manifest
mutate_manifest none-backend
check_case "none GPU backend" valid

write_base_manifest
cat >> "$CASE_ROOT/case/manifest.yaml" <<'YAML'
  llm:
    consumes: true
    route: gateway
    pinning: none
    min_context: 65536
    probe:
      kind: chat
      path: /api/chat
      auth: session
YAML
check_case "complete swap-safety llm metadata" valid

write_base_manifest
cat >> "$CASE_ROOT/case/manifest.yaml" <<'YAML'
  llm:
    consumes: false
YAML
check_case "non-consuming llm metadata" valid

write_base_manifest
mutate_manifest missing-service
check_case "missing service block" invalid

write_base_manifest
mutate_manifest boolean-port
check_case "boolean is not an integer port" invalid

write_base_manifest
mutate_manifest unknown-backend
check_case "unknown GPU backend" invalid

write_base_manifest
cat >> "$CASE_ROOT/case/manifest.yaml" <<'YAML'
  llm:
    consumes: true
    route: gateway
YAML
check_case "consuming llm metadata requires pinning and probe" invalid

write_base_manifest
cat >> "$CASE_ROOT/case/manifest.yaml" <<'YAML'
features:
  - id: feature
    name: Feature
    description: Feature description
    icon: Box
    category: testing
    requirements: {}
    priority: true
YAML
check_case "feature priority rejects booleans" invalid

write_base_manifest
cat >> "$CASE_ROOT/case/manifest.yaml" <<'YAML'
tags: [Bad_Tag]
YAML
check_case "tag pattern is enforced" invalid

echo "Manifest schema source-of-truth tests passed."
