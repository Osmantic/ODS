#!/usr/bin/env bash
# Exercise the shipped CLI parser, exports and phase-06 route serialization.
# All .env reads and writes belong to this fixture's temporary directory.
# Variables below are consumed by evaluated installer code.
# shellcheck disable=SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT/installers/phases/06-directories.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
source "$ROOT/lib/safe-env.sh"
source "$ROOT/lib/dotenv-quote.sh"

defaults="$(sed -n '/^LEMONADE_EXTERNAL=/,/^LEMONADE_GPU_VRAM_MB=/p' "$ROOT/install-core.sh")"
parser="$(sed -n '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$ROOT/install-core.sh")"
exports="$(sed -n '/^if \[\[ "\${LEMONADE_EXTERNAL,,}" == "true" \]\]; then$/,/^fi$/p' "$ROOT/install-core.sh")"
route_source="$(sed -n '/^    LEMONADE_EXTERNAL_VALUE=/,/^    LEMONADE_CONTAINER_API_BASE_VALUE=/p' "$PHASE")"
template="$(sed -n '/^LEMONADE_EXTERNAL=/,/^LEMONADE_MODEL=/p' "$PHASE")"
[[ -n "$defaults" && -n "$parser" && -n "$exports" && -n "$route_source" && -n "$template" ]]

for reader in _env_get _env_get_explicit_first; do
    definition="$(awk -v name="$reader" '
        $0 == "    " name "() {" { emit = 1 }
        emit { print }
        emit && $0 == "    }" { exit }
    ' "$PHASE")"
    [[ -n "$definition" ]]
    eval "$definition"
done

run_case() (
    local label="$1" inherited="$2" persisted="$3" expected="$4" exported
    shift 4
    unset LEMONADE_HOST_TRANSPORT LEMONADE_EXTERNAL LEMONADE_BASE_URL LEMONADE_API_KEY
    unset LEMONADE_MODEL LEMONADE_GPU_NAME LEMONADE_GPU_VRAM_MB
    unset ODS_WINDOWS_SYSTEM_DIRECTORY ODS_WSL_STATE_ROOT
    [[ -z "${INHERITED_STATE_ROOT:-}" ]] || ODS_WSL_STATE_ROOT="$INHERITED_STATE_ROOT"
    unset LEMONADE_CONTAINER_BASE_URL LEMONADE_API_BASE_PATH AMD_INFERENCE_PORT
    [[ "$inherited" == unset ]] || LEMONADE_HOST_TRANSPORT="$inherited"
    eval "$defaults"
    set -- --lemonade-url http://localhost:13305/api/v1 "$@"
    eval "$parser"
    eval "$exports"
    exported="$(bash -c 'printf "%s|%s|%s" "$LEMONADE_EXTERNAL" "$LEMONADE_HOST_TRANSPORT" "$ODS_WINDOWS_SYSTEM_DIRECTORY"')"
    [[ "$exported" == "true|$LEMONADE_HOST_TRANSPORT|$ODS_WINDOWS_SYSTEM_DIRECTORY" && "$ODS_MODE" == lemonade ]]

    # Run the actual route normalization and .env template slice. This avoids
    # directory creation, secret generation and unrelated phase side effects.
    INSTALL_DIR="$tmp/install"
    mkdir -p "$INSTALL_DIR"
    _env_existing="$tmp/no-existing-env"
    if [[ "$persisted" != unset ]]; then
        _env_existing="$tmp/existing-env"
        printf 'LEMONADE_HOST_TRANSPORT=%s\n' "$persisted" > "$_env_existing"
        if [[ -n "${PERSISTED_WINDOWS_SYSTEM_DIRECTORY:-}" ]]; then
            printf 'ODS_WINDOWS_SYSTEM_DIRECTORY=%s\n' "$(dotenv_value "$PERSISTED_WINDOWS_SYSTEM_DIRECTORY")" >> "$_env_existing"
        fi
        if [[ -n "${PERSISTED_STATE_ROOT:-}" ]]; then
            printf 'ODS_WSL_STATE_ROOT=%s\n' "$(dotenv_value "$PERSISTED_STATE_ROOT")" >> "$_env_existing"
        fi
    fi
    eval "$route_source"
    [[ "$LEMONADE_HOST_TRANSPORT" == "$expected" ]]
    LEMONADE_MODEL_VALUE='extra.Qwen3.5-9B-Q4_K_M.gguf'
    eval 'cat > "$INSTALL_DIR/.env" << ENV_EOF
'"$template"'
ENV_EOF'
    unset LEMONADE_HOST_TRANSPORT LEMONADE_EXTERNAL LEMONADE_BASE_URL LEMONADE_CONTAINER_BASE_URL
    unset ODS_WINDOWS_SYSTEM_DIRECTORY ODS_WSL_STATE_ROOT
    load_env_file "$INSTALL_DIR/.env"
    [[ "$LEMONADE_HOST_TRANSPORT" == "$expected" && "$LEMONADE_EXTERNAL" == true ]]
    [[ "$LEMONADE_BASE_URL" == http://localhost:13305 ]]
    [[ "$LEMONADE_CONTAINER_BASE_URL" == http://host.docker.internal:13305 ]]
    [[ "$LEMONADE_API_BASE_PATH" == /api/v1 && "$LEMONADE_MODEL" == extra.Qwen3.5-9B-Q4_K_M.gguf ]]
    [[ "${ODS_WINDOWS_SYSTEM_DIRECTORY:-}" == "${EXPECTED_WINDOWS_SYSTEM_DIRECTORY:-}" ]]
    if [[ -z "${EXPECTED_WINDOWS_SYSTEM_DIRECTORY:-}" ]]; then
        ! grep -q '^ODS_WINDOWS_SYSTEM_DIRECTORY=' "$INSTALL_DIR/.env"
    fi
    [[ "${ODS_WSL_STATE_ROOT:-}" == "${EXPECTED_STATE_ROOT:-}" ]]
    if [[ -z "${EXPECTED_STATE_ROOT:-}" ]]; then
        ! grep -q '^ODS_WSL_STATE_ROOT=' "$INSTALL_DIR/.env"
    fi
    printf 'PASS: %s is exported and persists with the normalized route\n' "$label"
)

run_case 'Omitted transport defaults to direct' unset unset direct
run_case 'Empty inherited transport defaults to direct' '' unset direct
run_case 'Inherited model-router transport' model-router unset model-router
run_case 'Explicit model-router transport' direct unset model-router --lemonade-host-transport model-router
run_case 'Explicit direct overrides inherited model-router' model-router unset direct --lemonade-host-transport direct
run_case 'Last explicit transport wins' unset unset direct \
    --lemonade-host-transport model-router --lemonade-host-transport direct
run_case 'Omitted transport preserves the saved Windows route' unset model-router model-router
run_case 'Empty inherited transport preserves the saved Windows route' '' model-router model-router
run_case 'Explicit direct overrides saved model-router' unset model-router direct --lemonade-host-transport direct
run_case 'Inherited direct overrides saved model-router' direct model-router direct
run_case 'Explicit model-router overrides saved direct' unset direct model-router --lemonade-host-transport model-router
run_case 'Omitted transport preserves saved direct' unset direct direct
EXPECTED_WINDOWS_SYSTEM_DIRECTORY='D:\Operating $ystem\System32' \
    run_case 'Explicit Windows directory is preserved literally' unset unset model-router \
    --lemonade-host-transport model-router --windows-system-directory 'D:\Operating $ystem\System32'
EXPECTED_WINDOWS_SYSTEM_DIRECTORY='E:\Windows\System32' PERSISTED_WINDOWS_SYSTEM_DIRECTORY='E:\Windows\System32' \
    run_case 'Rerun preserves Windows directory' unset model-router model-router
INHERITED_STATE_ROOT="D:\ODS state\it's [private] \$(literal)" EXPECTED_STATE_ROOT="D:\ODS state\it's [private] \$(literal)" \
    run_case 'Explicit Windows state location survives dotenv quoting' unset unset direct
PERSISTED_STATE_ROOT='E:\ODS state\saved' EXPECTED_STATE_ROOT='E:\ODS state\saved' \
    run_case 'Linux rerun preserves the existing Windows state location' unset model-router model-router
INHERITED_STATE_ROOT='D:\ODS state\selected' PERSISTED_STATE_ROOT='E:\ODS state\saved' EXPECTED_STATE_ROOT='D:\ODS state\selected' \
    run_case 'Windows selection wins over the persisted state location' unset model-router model-router

reject() {
    local label="$1" rc=0
    shift
    ( eval "$defaults"; eval "$parser" ) >"$tmp/rejected.log" 2>&1 || rc=$?
    [[ "$rc" -ne 0 ]]
    grep -q -- '--lemonade-host-transport requires direct or model-router' "$tmp/rejected.log"
    printf 'PASS: %s is rejected\n' "$label"
}
reject 'Unknown transport' --lemonade-host-transport proxy
reject 'Missing transport value' --lemonade-host-transport
reject 'Empty transport argument' --lemonade-host-transport ''
reject 'Following flag used as transport' --lemonade-host-transport --pixel

error() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
for origin in inherited persisted; do
    rc=0
    if [[ "$origin" == inherited ]]; then
        run_case 'Invalid inherited transport' proxy unset direct >"$tmp/rejected.log" 2>&1 || rc=$?
    else
        run_case 'Invalid saved transport' unset proxy direct >"$tmp/rejected.log" 2>&1 || rc=$?
    fi
    [[ "$rc" -ne 0 ]]
    grep -q 'LEMONADE_HOST_TRANSPORT must be direct or model-router' "$tmp/rejected.log"
    printf 'PASS: invalid %s transport is rejected before writing the route\n' "$origin"
done

python3 - "$ROOT/.env.schema.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    schema = json.load(source)
transport = schema["properties"]["LEMONADE_HOST_TRANSPORT"]
assert transport["type"] == "string"
assert set(transport["enum"]) == {"direct", "model-router"}
print("PASS: persisted transport values match the environment schema")
PY
