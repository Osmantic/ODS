#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_literal() {
    local path="$1"
    local needle="$2"
    local label="$3"
    if ! grep -Fq -- "$needle" "$ROOT/$path"; then
        echo "FAIL: missing ${label} in ${path}: ${needle}" >&2
        exit 1
    fi
}

reject_literal() {
    local path="$1"
    local needle="$2"
    local label="$3"
    if grep -Fq -- "$needle" "$ROOT/$path"; then
        echo "FAIL: unexpected ${label} in ${path}: ${needle}" >&2
        exit 1
    fi
}

reject_regex() {
    local path="$1"
    local pattern="$2"
    local label="$3"
    if grep -Eq -- "$pattern" "$ROOT/$path"; then
        echo "FAIL: unexpected ${label} in ${path}: ${pattern}" >&2
        exit 1
    fi
}

python3 - "$ROOT/.env.schema.json" <<'PY'
import json
import sys

schema_path = sys.argv[1]
with open(schema_path, encoding="utf-8") as f:
    props = json.load(f)["properties"]

required = {
    "LLAMA_ARG_SPEC_TYPE": "string",
    "LLAMA_ARG_SPEC_DRAFT_N_MAX": "integer",
}
for key, expected_type in required.items():
    actual = props.get(key, {}).get("type")
    if actual != expected_type:
        raise SystemExit(f"FAIL: {key} schema type {actual!r}, expected {expected_type!r}")

if "LLAMA_ARG_SPEC_DRAFT_P_MIN" in props:
    raise SystemExit("FAIL: LLAMA_ARG_SPEC_DRAFT_P_MIN should not be exposed for MTP yet")
PY

for key in LLAMA_ARG_SPEC_TYPE LLAMA_ARG_SPEC_DRAFT_N_MAX; do
    require_literal ".env.example" "# ${key}=" ".env example ${key}"
    require_literal "docker-compose.base.yml" "- ${key}" "compose passthrough ${key}"
    require_literal "installers/phases/06-directories.sh" "# ${key}=" "Linux installer ${key}"
    require_literal "installers/macos/lib/env-generator.sh" "# ${key}=" "macOS env generator ${key}"
    require_literal "installers/windows/lib/env-generator.ps1" "# ${key}=" "Windows env generator ${key}"
done

for path in \
    ".env.example" \
    "docker-compose.base.yml" \
    "bin/dream-host-agent.py" \
    "installers/phases/06-directories.sh" \
    "installers/macos/dream-macos.sh" \
    "installers/macos/install-macos.sh" \
    "installers/macos/lib/env-generator.sh" \
    "installers/windows/dream.ps1" \
    "installers/windows/install-windows.ps1" \
    "installers/windows/lib/env-generator.ps1" \
    "scripts/bootstrap-upgrade.sh" \
    "extensions/services/llama-server/README.md"; do
    reject_literal "$path" "LLAMA_ARG_SPEC_DRAFT_P_MIN" "draft p-min exposure"
    reject_literal "$path" "--spec-draft-p-min" "draft p-min flag"
done

require_literal ".env.example" "# LLAMA_ARG_SPEC_DRAFT_N_MAX=3" "conservative MTP draft cap example"
require_literal "installers/phases/06-directories.sh" "# LLAMA_ARG_SPEC_DRAFT_N_MAX=3" "Linux MTP draft cap example"
require_literal "installers/macos/lib/env-generator.sh" "# LLAMA_ARG_SPEC_DRAFT_N_MAX=3" "macOS MTP draft cap example"
require_literal "installers/windows/lib/env-generator.ps1" "# LLAMA_ARG_SPEC_DRAFT_N_MAX=3" "Windows MTP draft cap example"
reject_regex ".env.example" "^LLAMA_ARG_SPEC_TYPE=" "active MTP default"
reject_regex ".env.example" "^LLAMA_ARG_SPEC_DRAFT_N_MAX=" "active MTP draft cap default"

require_literal "bin/dream-host-agent.py" '"LLAMA_ARG_SPEC_TYPE": "--spec-type"' "host-agent spec type mapping"
require_literal "bin/dream-host-agent.py" '"LLAMA_ARG_SPEC_DRAFT_N_MAX": "--spec-draft-n-max"' "host-agent spec n max mapping"

require_literal "installers/windows/install-windows.ps1" '--spec-type", $_llamaEnv["LLAMA_ARG_SPEC_TYPE"]' "Windows install native spec type"
require_literal "installers/windows/dream.ps1" '--spec-type", $envVars["LLAMA_ARG_SPEC_TYPE"]' "Windows CLI native spec type"
require_literal "installers/macos/dream-macos.sh" '--spec-type "$ENV_LLAMA_ARG_SPEC_TYPE"' "macOS CLI native spec type"
require_literal "installers/macos/install-macos.sh" '--spec-type "$_spec_type"' "macOS installer native spec type"
require_literal "scripts/bootstrap-upgrade.sh" '--spec-type "$_spec_type"' "bootstrap native spec type"
require_literal "extensions/services/llama-server/README.md" 'LLAMA_ARG_SPEC_TYPE=draft-mtp' "llama-server MTP docs"
require_literal "extensions/services/llama-server/README.md" 'spec-draft-n-max = 3' "router-mode MTP docs"

echo "llama MTP arg contract OK"
