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

assert_eq "$(ods_default_tts_workers 0)" "2" "unknown memory preserves prior default"
assert_eq "$(ods_default_tts_workers 8)" "1" "Colima-size Docker guest uses one TTS worker"
assert_eq "$(ods_default_tts_workers 11)" "1" "sub-12 GiB Docker guest uses one TTS worker"
assert_eq "$(ods_default_tts_workers 12)" "2" "12 GiB Docker guest uses two TTS workers"
assert_eq "$(ods_default_tts_workers invalid)" "2" "invalid memory preserves prior default"

assert_eq "$(ods_default_nvidia_llama_memory_limit 0)" "64G" "unknown RAM fallback"
assert_eq "$(ods_default_nvidia_llama_memory_limit 2)" "1G" "minimum usable limit"
assert_eq "$(ods_default_nvidia_llama_memory_limit 8)" "5G" "8 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 16)" "12G" "16 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 32)" "28G" "32 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 64)" "60G" "64 GiB host"
assert_eq "$(ods_default_nvidia_llama_memory_limit 128)" "64G" "absolute cap"

# These are intentional source-contract literals, not shell expansions.
# shellcheck disable=SC2016
grep -qF 'LLAMA_SERVER_MEMORY_LIMIT_VALUE="$(_env_get LLAMA_SERVER_MEMORY_LIMIT "${LLAMA_SERVER_MEMORY_LIMIT:-$_llama_memory_default}")"' \
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

# CPU backend: llama.cpp threads follow physical cores, bounded by the
# container CPU limit; 4 (the historical compose default) when unknown.
assert_eq "$(ods_default_cpu_llama_threads 8 8.0)" "8" "8 physical cores (16 logical) under an 8-CPU limit"
assert_eq "$(ods_default_cpu_llama_threads 32 8.0)" "8" "32 physical cores capped by the 8-CPU limit"
assert_eq "$(ods_default_cpu_llama_threads 2 8.0)" "2" "2 physical cores"
assert_eq "$(ods_default_cpu_llama_threads "" 8.0)" "4" "unknown core count keeps 4"
assert_eq "$(ods_default_cpu_llama_threads "" 2.0)" "2" "unknown core count still respects a smaller limit"
assert_eq "$(ods_default_cpu_llama_threads 6 invalid)" "6" "invalid limit falls back to 8"
cores="$(ods_physical_cpu_cores)"
[[ "$cores" =~ ^[1-9][0-9]*$ ]] || { printf '[FAIL] physical core detection returned %q\n' "$cores" >&2; exit 1; }

# Phase 06 writes the CPU memory limit only from a runtime profile (the CPU
# compose default stays 6G), writes LLAMA_THREADS for the CPU backend only,
# and passes the profile's checkpoint and prompt-cache caps through.
phase06="$ROOT_DIR/installers/phases/06-directories.sh"
# shellcheck disable=SC2016
grep -qF 'LLAMA_SERVER_MEMORY_LIMIT_VALUE="$(_env_get LLAMA_SERVER_MEMORY_LIMIT "${LLAMA_SERVER_MEMORY_LIMIT:-}")"' "$phase06"
# shellcheck disable=SC2016
grep -qF 'if [[ "$_cpu_backend" == "cpu" && "${ODS_MODE:-local}" != "cloud" ]]; then' "$phase06"
# shellcheck disable=SC2016
grep -qF 'echo "LLAMA_THREADS=${LLAMA_THREADS_VALUE}"' "$phase06"
# shellcheck disable=SC2016
grep -qF 'echo "LLAMA_ARG_CTX_CHECKPOINTS=${LLAMA_ARG_CTX_CHECKPOINTS}"' "$phase06"
# shellcheck disable=SC2016
grep -qF 'echo "LLAMA_ARG_CACHE_RAM=${LLAMA_ARG_CACHE_RAM}"' "$phase06"
# shellcheck disable=SC2016
grep -qF 'memory: ${LLAMA_SERVER_MEMORY_LIMIT:-6G}' "$ROOT_DIR/docker-compose.cpu.yml"
# shellcheck disable=SC2016
grep -qF -- '- "${LLAMA_THREADS:-4}"' "$ROOT_DIR/docker-compose.cpu.yml"
printf '[PASS] CPU llama-server threads, memory limit and checkpoint caps contract\n'
