#!/bin/bash
# Validate core compatibility contracts from manifest.json.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST_FILE="${ROOT_DIR}/manifest.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
test -f "$MANIFEST_FILE" || fail "manifest.json not found"

jq -e '.manifestVersion and .release.version and .compatibility and .contracts' "$MANIFEST_FILE" >/dev/null \
  || fail "manifest.json missing required top-level fields"
pass "manifest structure"

# Compose contract files
while IFS= read -r file; do
  test -f "${ROOT_DIR}/${file}" || fail "missing compose contract file: ${file}"
done < <(jq -r '.contracts.compose.canonical[]' "$MANIFEST_FILE")
pass "compose canonical files"

# Workflow catalog canonical path
workflow_path="$(jq -r '.contracts.workflowCatalog.canonicalPath' "$MANIFEST_FILE")"
test -f "${ROOT_DIR}/${workflow_path}" || fail "missing canonical workflow catalog: ${workflow_path}"
pass "workflow catalog canonical path"

# Extension schema contract
schema_path="$(jq -r '.contracts.extensions.serviceManifestSchema' "$MANIFEST_FILE")"
test -f "${ROOT_DIR}/${schema_path}" || fail "missing extension schema: ${schema_path}"
pass "extension schema contract"

# Port contract
ports_path="$(jq -r '.contracts.ports.canonicalPath' "$MANIFEST_FILE")"
test -f "${ROOT_DIR}/${ports_path}" || fail "missing canonical ports contract: ${ports_path}"
jq -e '.version and (.ports | type=="array" and length>0)' "${ROOT_DIR}/${ports_path}" >/dev/null \
  || fail "invalid ports contract structure: ${ports_path}"
pass "ports contract"

# Support matrix consistency checks
if jq -e '.compatibility.os.macos.supported == false' "$MANIFEST_FILE" >/dev/null; then
  grep -q "macOS.*Tier C" "${ROOT_DIR}/docs/SUPPORT-MATRIX.md" \
    || warn "manifest says macOS unsupported/preview but docs may be out of sync"
fi
pass "compatibility check complete"
