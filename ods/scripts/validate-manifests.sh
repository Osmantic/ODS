#!/usr/bin/env bash
# Validate extension service manifests against the v1 schema and
# check ODS version compatibility metadata.
#
# Usage:
#   scripts/validate-manifests.sh
#
# Behavior:
#   - Loads core version and extension schema path from manifest.json
#   - Scans extensions/services/*/manifest.{yaml,yml,json}
#   - Validates structure against extensions/schema/service-manifest.v1.json
#     when python3 + PyYAML + jsonschema are available (otherwise warns)
#   - Reads optional per-manifest compatibility block:
#       compatibility:
#         ods_min: "2.0.0"
#         ods_max: "2.0.99"
#     and compares it to the current ODS version.
#   - Prints a human-readable summary and exits non-zero only on
#     hard failures (schema errors, IO problems).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST_FILE="${ROOT_DIR}/manifest.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
test -f "$MANIFEST_FILE" || fail "manifest.json not found"

CORE_VERSION="$(jq -r '.release.version' "$MANIFEST_FILE")"
SCHEMA_PATH_REL="$(jq -r '.contracts.extensions.serviceManifestSchema' "$MANIFEST_FILE")"
EXT_DIR_REL="$(jq -r '.contracts.extensions.serviceDirectory' "$MANIFEST_FILE")"

test -n "$CORE_VERSION" || fail "release.version missing in manifest.json"
test -n "$SCHEMA_PATH_REL" || fail "contracts.extensions.serviceManifestSchema missing in manifest.json"
test -n "$EXT_DIR_REL" || fail "contracts.extensions.serviceDirectory missing in manifest.json"

SCHEMA_PATH="${ROOT_DIR}/${SCHEMA_PATH_REL}"
EXT_DIR="${ROOT_DIR}/${EXT_DIR_REL%/}"

test -f "$SCHEMA_PATH" || fail "extension schema not found at ${SCHEMA_PATH_REL}"
test -d "$EXT_DIR" || fail "extensions directory not found at ${EXT_DIR_REL}"

info "Core version: ${CORE_VERSION}"
info "Extensions directory: ${EXT_DIR_REL}"
info "Schema: ${SCHEMA_PATH_REL}"

PYTHON_OK=true
if ! command -v python3 >/dev/null 2>&1; then
  PYTHON_OK=false
fi

if $PYTHON_OK; then
  # Probe for required modules; fall back gracefully if missing.
  if ! python3 - <<'PY' >/dev/null 2>&1
import sys
import json  # noqa
import importlib
importlib.import_module("yaml")
importlib.import_module("jsonschema")
PY
  then
    warn "python3 yaml/jsonschema modules not available — skipping schema validation (compatibility checks only)"
    PYTHON_OK=false
  fi
else
  warn "python3 not found — skipping schema validation (compatibility checks only)"
fi

py_exit=0
python3 - "$ROOT_DIR" "$EXT_DIR_REL" "$SCHEMA_PATH_REL" "$CORE_VERSION" "$PYTHON_OK" <<'PY' || py_exit=$?
import json
import sys
import textwrap
from pathlib import Path

root_dir = Path(sys.argv[1])
ext_dir_rel = sys.argv[2]
schema_rel = sys.argv[3]
core_version = sys.argv[4]
python_ok = sys.argv[5].lower() == "true"

ext_dir = root_dir / ext_dir_rel
schema_path = root_dir / schema_rel

results = []
schema_errors = False


def parse_version(v: str):
    """Parse "2.0.0" into (2, 0, 0). Non-numeric segments become 0."""
    parts = []
    for part in v.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def compatibility_result(manifest, fallback_sid, core_ver_tuple):
    """Given a parsed manifest, return the single compatibility result dict."""
    svc = manifest.get("service", {})
    sid = svc.get("id") or fallback_sid
    compat = manifest.get("compatibility", {})
    ods_min = compat.get("ods_min")
    ods_max = compat.get("ods_max")

    if not ods_min and not ods_max:
        return {
            "service_id": sid,
            "status": "ok-no-metadata",
            "reason": "No ods_min/ods_max specified (assumed compatible)",
        }

    status = "ok"
    reason = "Compatible with current version"
    if ods_min and parse_version(str(ods_min)) > core_ver_tuple:
        status = "incompatible"
        reason = f"Requires ODS >= {ods_min}"
    elif ods_max and parse_version(str(ods_max)) < core_ver_tuple:
        status = "incompatible"
        reason = f"Supports ODS <= {ods_max}"

    return {"service_id": sid, "status": status, "reason": reason}


core_ver_tuple = parse_version(core_version)

if python_ok:
    import yaml
    import jsonschema

    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
else:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception:
        yaml = None  # type: ignore[assignment]
    schema = None

for service_dir in sorted(ext_dir.iterdir()):
    if not service_dir.is_dir():
        continue
    manifest_path = None
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = service_dir / name
        if candidate.exists():
            manifest_path = candidate
            break
    if not manifest_path:
        continue

    manifest = None
    try:
        if manifest_path.suffix == ".json":
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
        elif yaml is not None:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = yaml.safe_load(f)
        else:
            results.append(
                {
                    "service_id": service_dir.name,
                    "status": "skipped",
                    "reason": "Cannot parse manifest (no PyYAML/jsonschema)",
                }
            )
            continue
    except Exception as e:  # noqa: BLE001
        schema_errors = True
        results.append(
            {
                "service_id": service_dir.name,
                "status": "error",
                "reason": f"Failed to parse manifest: {e}",
            }
        )
        continue

    if schema is not None:
        try:
            jsonschema.validate(manifest, schema)
        except jsonschema.ValidationError as e:  # type: ignore[attr-defined]
            schema_errors = True
            sid = manifest.get("service", {}).get("id") or service_dir.name
            results.append(
                {
                    "service_id": sid,
                    "status": "error",
                    "reason": f"Schema validation failed at {list(e.absolute_path)}: {e.message}",
                }
            )
            continue

    results.append(compatibility_result(manifest, service_dir.name, core_ver_tuple))

# Print human-readable summary
print()
print("Extension manifest validation")
print("────────────────────────────")
if not results:
    print("No extension manifests found.")
    sys.exit(0)

width_id = max(len(r["service_id"]) for r in results) + 2
width_status = max(len(r["status"]) for r in results) + 2

print(f"{'SERVICE'.ljust(width_id)}{'STATUS'.ljust(width_status)}REASON")
print(f"{'-' * width_id}{'-' * width_status}{'-' * 40}")

incompatible = 0
no_meta = 0
skipped = 0
errors = 0

for r in results:
    sid = r["service_id"]
    status = r["status"]
    reason = r["reason"]
    print(f"{sid.ljust(width_id)}{status.ljust(width_status)}{reason}")

    if status == "incompatible":
        incompatible += 1
    elif status == "ok-no-metadata":
        no_meta += 1
    elif status == "skipped":
        skipped += 1
    elif status == "error":
        errors += 1

print()
print(
    textwrap.dedent(
        f"""\
Summary:
  Compatible:   {len(results) - incompatible - errors - skipped}
  Incompatible: {incompatible}
  No metadata:  {no_meta}
  Skipped:      {skipped}
  Errors:       {errors}
"""
    ).rstrip()
)

if schema_errors or errors:
    sys.exit(1)
sys.exit(0)
PY

if [[ "$py_exit" -eq 0 ]]; then
  pass "Extension manifests validated"
else
  fail "Extension manifest validation failed"
fi

