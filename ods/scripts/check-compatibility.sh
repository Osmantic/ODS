#!/bin/bash
# Validate core compatibility contracts from manifest.json.

set -euo pipefail

ROOT_DIR="${ODS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ROOT_DIR="$(cd "$ROOT_DIR" && pwd -P)"
MANIFEST_FILE="${ROOT_DIR}/manifest.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

contract_file() {
  local label="$1"
  local relative="$2"
  local candidate parent resolved

  [[ -n "$relative" && "$relative" != "null" && "$relative" != /* ]] \
    || fail "invalid ${label} path: ${relative}"
  candidate="${ROOT_DIR}/${relative}"
  test -f "$candidate" || fail "missing ${label}: ${relative}"
  parent="$(cd "$(dirname "$candidate")" && pwd -P)"
  resolved="${parent}/$(basename "$candidate")"
  case "$resolved" in
    "$ROOT_DIR"/*) printf '%s\n' "$resolved" ;;
    *) fail "${label} escapes the repository: ${relative}" ;;
  esac
}

command -v jq >/dev/null 2>&1 || fail "jq is required"
test -f "$MANIFEST_FILE" || fail "manifest.json not found"

jq -e '.manifestVersion and .release.version and .compatibility and .contracts' "$MANIFEST_FILE" >/dev/null \
  || fail "manifest.json missing required top-level fields"
pass "manifest structure"

# Compose contract files
while IFS= read -r file; do
  file="${file%$'\r'}"
  contract_file "compose contract file" "$file" >/dev/null
done < <(jq -r '.contracts.compose.canonical[]' "$MANIFEST_FILE")
pass "compose canonical files"

# Workflow catalog canonical path
workflow_path="$(jq -r '.contracts.workflowCatalog.canonicalPath' "$MANIFEST_FILE")"
workflow_path="${workflow_path%$'\r'}"
contract_file "canonical workflow catalog" "$workflow_path" >/dev/null
pass "workflow catalog canonical path"

# Extension schema contract
schema_path="$(jq -r '.contracts.extensions.serviceManifestSchema' "$MANIFEST_FILE")"
schema_path="${schema_path%$'\r'}"
contract_file "extension schema" "$schema_path" >/dev/null
pass "extension schema contract"

# Port contract
ports_path="$(jq -r '.contracts.ports.canonicalPath' "$MANIFEST_FILE")"
ports_path="${ports_path%$'\r'}"
ports_file="$(contract_file "canonical ports contract" "$ports_path")"
jq -e '.version and (.ports | type=="array" and length>0)' "$ports_file" >/dev/null \
  || fail "invalid ports contract structure: ${ports_path}"
pass "ports contract"

# Support matrix consistency checks
if jq -e '.compatibility.os.macos.supported == false' "$MANIFEST_FILE" >/dev/null; then
  grep -q "macOS.*Tier C" "${ROOT_DIR}/docs/SUPPORT-MATRIX.md" \
    || warn "manifest says macOS unsupported/preview but docs may be out of sync"
fi
pass "compatibility check complete"
