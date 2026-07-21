#!/usr/bin/env bash
# Gate health-contract field parity between core and library service-manifest schemas.
#
# Schemas are intentionally not byte-identical (core has extra fields). This test
# only compares health-contract property definitions under properties.service.properties.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$TEST_DIR")"

CORE_SCHEMA="${PROJECT_DIR}/extensions/schema/service-manifest.v1.json"
LIBRARY_SCHEMA="${PROJECT_DIR}/extensions/library/schema/service-manifest.v1.json"

if [[ ! -f "$CORE_SCHEMA" ]]; then
    echo "ERROR: missing core schema: $CORE_SCHEMA" >&2
    exit 1
fi
if [[ ! -f "$LIBRARY_SCHEMA" ]]; then
    echo "ERROR: missing library schema: $LIBRARY_SCHEMA" >&2
    exit 1
fi

PYTHON_CMD=""
if [[ -f "${PROJECT_DIR}/lib/python-cmd.sh" ]]; then
    # shellcheck source=/dev/null
    . "${PROJECT_DIR}/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd 2>/dev/null || true)"
fi
if [[ -z "$PYTHON_CMD" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    else
        echo "ERROR: python3 (stdlib json) is required for health-contract schema drift gate" >&2
        exit 1
    fi
fi

export CORE_SCHEMA LIBRARY_SCHEMA
"$PYTHON_CMD" - <<'PY'
import json
import os
import sys

HEALTH_KEYS = (
    "external_port_env",
    "health",
    "health_port",
    "health_port_env",
    "health_header",
    "health_timeout",
)

core_path = os.environ["CORE_SCHEMA"]
library_path = os.environ["LIBRARY_SCHEMA"]

with open(core_path, encoding="utf-8") as fh:
    core = json.load(fh)
with open(library_path, encoding="utf-8") as fh:
    library = json.load(fh)

try:
    core_props = core["properties"]["service"]["properties"]
    library_props = library["properties"]["service"]["properties"]
except (KeyError, TypeError) as exc:
    print(f"ERROR: schema missing properties.service.properties: {exc}", file=sys.stderr)
    sys.exit(1)

mismatches = []

for key in HEALTH_KEYS:
    core_has = key in core_props
    library_has = key in library_props
    if not core_has and not library_has:
        continue
    if core_has != library_has:
        side = "core" if core_has else "library"
        other = "library" if core_has else "core"
        mismatches.append(f"{key}: present in {side}, missing in {other}")
        continue

    core_def = core_props[key]
    library_def = library_props[key]
    if not isinstance(core_def, dict) or not isinstance(library_def, dict):
        mismatches.append(f"{key}: expected object definitions on both schemas")
        continue

    core_type = core_def.get("type")
    library_type = library_def.get("type")
    if core_type != library_type:
        mismatches.append(
            f"{key}.type: core={core_type!r} library={library_type!r}"
        )

    if "pattern" in core_def or "pattern" in library_def:
        core_pattern = core_def.get("pattern")
        library_pattern = library_def.get("pattern")
        if core_pattern != library_pattern:
            mismatches.append(
                f"{key}.pattern: core={core_pattern!r} library={library_pattern!r}"
            )

if mismatches:
    print("ERROR: service-manifest health-contract schema drift detected:", file=sys.stderr)
    for item in mismatches:
        print(f"  - {item}", file=sys.stderr)
    print(
        "Health-contract fields must match type/pattern across "
        "extensions/schema and extensions/library/schema "
        "(full schema equality is not required).",
        file=sys.stderr,
    )
    sys.exit(1)

print("SERVICE_MANIFEST_HEALTH_CONTRACT_OK")
PY
