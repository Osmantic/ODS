#!/usr/bin/env bash
# The effective switchboard mode must reach Compose, not only the generated .env.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
block="$(sed -n '/^    ODS_MODEL_SWITCHBOARD_VALUE=/,/^    _default_llm_api_url=/p' \
    "$root/installers/phases/06-directories.sh" | sed '$d')"
[[ -n "$block" ]] || { echo 'FAIL: missing switchboard selection block'; exit 1; }

check_mode() (
    local requested="$1" external="$2" expected="$3"
    _env_get() { printf '%s\n' "$2"; }
    ai_warn() { :; }
    ODS_MODEL_SWITCHBOARD="$requested"
    EXTERNAL_LLM_ACTIVE="$external"
    source /dev/stdin <<< "$block"
    [[ "$ODS_MODEL_SWITCHBOARD_VALUE" == "$expected" ]]
    [[ "$ODS_MODEL_SWITCHBOARD" == "$expected" ]]
    bash -c '[[ "$ODS_MODEL_SWITCHBOARD" == "$1" ]]' _ "$expected"
)

check_mode enabled true observe
check_mode enabled false enabled
check_mode legacy true legacy
check_mode observe true observe
echo 'PASS: external-model switchboard mode is exported to Compose'
