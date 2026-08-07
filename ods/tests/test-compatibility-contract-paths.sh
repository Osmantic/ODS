#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FIXTURE="$TMP_DIR/repo"
mkdir -p "$FIXTURE/contracts" "$TMP_DIR/outside"
printf '{}\n' > "$FIXTURE/contracts/workflows.json"
printf '{}\n' > "$FIXTURE/contracts/schema.json"
printf '{"version":1,"ports":[8080]}\n' > "$FIXTURE/contracts/ports.json"
printf 'services: {}\n' > "$TMP_DIR/outside/base.yml"
cat > "$FIXTURE/manifest.json" <<'JSON'
{
  "manifestVersion": 1,
  "release": {"version": "1.0.0"},
  "compatibility": {"os": {"macos": {"supported": true}}},
  "contracts": {
    "compose": {"canonical": ["../outside/base.yml"]},
    "workflowCatalog": {"canonicalPath": "contracts/workflows.json"},
    "extensions": {"serviceManifestSchema": "contracts/schema.json"},
    "ports": {"canonicalPath": "contracts/ports.json"}
  }
}
JSON

if ODS_ROOT="$FIXTURE" bash "$ROOT_DIR/scripts/check-compatibility.sh" \
    >"$TMP_DIR/output.log" 2>&1; then
  echo "[FAIL] compatibility gate accepted an escaping contract path" >&2
  exit 1
fi
grep -q "escapes the repository" "$TMP_DIR/output.log"
echo "[PASS] compatibility gate confines contract paths"
