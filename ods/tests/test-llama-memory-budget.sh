#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../installers/lib/llama-memory-budget.sh
source "$ROOT_DIR/installers/lib/llama-memory-budget.sh"

assert_eq() {
    local actual="$1" expected="$2" label="$3"
    if [[ "$actual" != "$expected" ]]; then
        printf '[FAIL] %s: expected %s, got %s\n' "$label" "$expected" "$actual" >&2
        exit 1
    fi
}

assert_eq "$(ods_effective_container_memory_gb 64 8)" "8" "Docker Desktop VM is the lower bound"
assert_eq "$(ods_effective_container_memory_gb 8 64)" "8" "physical host can be the lower bound"
assert_eq "$(ods_effective_container_memory_gb 32 0)" "32" "host fallback"
assert_eq "$(ods_effective_container_memory_gb invalid 0)" "0" "invalid detection fallback"

assert_eq "$(ods_default_nvidia_llama_memory_limit 0)" "64G" "unknown RAM fallback"
assert_eq "$(ods_default_nvidia_llama_memory_limit 2)" "1G" "minimum usable limit"
assert_eq "$(ods_default_nvidia_llama_memory_limit 8)" "5G" "8 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 16)" "12G" "16 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 32)" "28G" "32 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 64)" "60G" "64 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 128)" "64G" "absolute cap"

# These are intentional source-contract literals, not shell expansions.
# shellcheck disable=SC2016
grep -qF 'LLAMA_SERVER_MEMORY_LIMIT_VALUE="$(_env_get LLAMA_SERVER_MEMORY_LIMIT "$_llama_memory_default")"' \
    "$ROOT_DIR/installers/phases/06-directories.sh"
# shellcheck disable=SC2016
grep -qF 'LLAMA_SERVER_MEMORY_LIMIT=${LLAMA_SERVER_MEMORY_LIMIT_VALUE}' \
    "$ROOT_DIR/installers/phases/06-directories.sh"
# shellcheck disable=SC2016
grep -qF 'memory: ${LLAMA_SERVER_MEMORY_LIMIT:-64G}' \
    "$ROOT_DIR/docker-compose.nvidia.yml"

# install-core.sh defines SCRIPT_DIR as the ODS root, so phase 06 must resolve
# the helper through the installed installers/lib tree.
grep -qF 'source "$SCRIPT_DIR/installers/lib/llama-memory-budget.sh"' \
    "$ROOT_DIR/installers/phases/06-directories.sh"
if grep -qF 'source "$SCRIPT_DIR/lib/llama-memory-budget.sh"' \
    "$ROOT_DIR/installers/phases/06-directories.sh"; then
    printf '[FAIL] phase 06 resolves llama-memory-budget.sh outside installers/lib\n' >&2
    exit 1
fi

external_active_line="$(grep -nF 'EXTERNAL_LLM_ACTIVE=false' \
    "$ROOT_DIR/installers/phases/06-directories.sh" | head -1 | cut -d: -f1)"
memory_budget_line="$(grep -nF 'LLAMA_SERVER_MEMORY_LIMIT_VALUE=""' \
    "$ROOT_DIR/installers/phases/06-directories.sh" | head -1 | cut -d: -f1)"
if [[ -z "$external_active_line" || -z "$memory_budget_line" \
    || "$external_active_line" -ge "$memory_budget_line" ]]; then
    printf '[FAIL] phase 06 evaluates the memory budget before external-LLM state is initialized\n' >&2
    exit 1
fi

printf '[PASS] NVIDIA llama-server memory budget contract\n'
