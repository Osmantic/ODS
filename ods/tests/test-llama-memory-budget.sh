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

# Docker memory limits in MiB; unreadable values print nothing.
assert_eq "$(ods_memory_limit_mib 12G)" "12288" "12G"
assert_eq "$(ods_memory_limit_mib 12gb)" "12288" "12gb"
assert_eq "$(ods_memory_limit_mib 512m)" "512" "512m"
assert_eq "$(ods_memory_limit_mib 1T)" "1048576" "1T"
assert_eq "$(ods_memory_limit_mib 12884901888)" "12288" "plain bytes"
assert_eq "$(ods_memory_limit_mib 1.5G)" "" "decimal is not read"
assert_eq "$(ods_memory_limit_mib '')" "" "empty"

# llama.cpp RAM prompt cache: a third of (memory - 6 GiB), at most a quarter
# of the container limit, at least 512 MiB; nothing when 8192 MiB fits.
assert_eq "$(ods_default_llama_cache_ram_mib 15 12G)" "3072" "16 GB WSL VM (15 GiB) with the 12G 8 GB-GPU profile"
assert_eq "$(ods_default_llama_cache_ram_mib 15 64G)" "3072" "16 GB class with the NVIDIA compose limit"
assert_eq "$(ods_default_llama_cache_ram_mib 16 64G)" "3413" "16 GiB"
assert_eq "$(ods_default_llama_cache_ram_mib 24 20G)" "5120" "24 GiB host: a quarter of its 20G container"
assert_eq "$(ods_default_llama_cache_ram_mib 29 64G)" "7850" "29 GiB"
assert_eq "$(ods_default_llama_cache_ram_mib 31 27G)" "6912" "32 GB host (31 GiB, NVIDIA 27G default): the container limit binds"
assert_eq "$(ods_default_llama_cache_ram_mib 30 64G)" "" "30 GiB with a large container keeps llama.cpp's default"
assert_eq "$(ods_default_llama_cache_ram_mib 62 12G)" "3072" "64 GB host whose profile limits the container to 12G"
assert_eq "$(ods_default_llama_cache_ram_mib 125 64G)" "" "tower-class host keeps the default"
assert_eq "$(ods_default_llama_cache_ram_mib 64 6G)" "1536" "CPU compose limit bounds a large host"
assert_eq "$(ods_default_llama_cache_ram_mib 8 5G)" "682" "8 GiB host"
assert_eq "$(ods_default_llama_cache_ram_mib 7 4G)" "512" "floor"
assert_eq "$(ods_default_llama_cache_ram_mib 4 1G)" "512" "floor holds below it"
assert_eq "$(ods_default_llama_cache_ram_mib 0 12G)" "3072" "unknown memory: container limit only"
assert_eq "$(ods_default_llama_cache_ram_mib 0 '')" "" "nothing known keeps the default"
assert_eq "$(ods_default_llama_cache_ram_mib invalid 64G)" "" "invalid memory keeps the default"

# Phase 06 sizes the cache only when neither the profile nor .env did, and
# only for the Docker llama.cpp backends.
# shellcheck disable=SC2016
grep -qF 'LLAMA_ARG_CACHE_RAM="${LLAMA_ARG_CACHE_RAM:-$(_env_get LLAMA_ARG_CACHE_RAM "")}"' "$phase06"
# shellcheck disable=SC2016
grep -qF 'LLAMA_ARG_CACHE_RAM="$(ods_default_llama_cache_ram_mib "$_cache_memory_gb" \' "$phase06"
cache_line="$(grep -nF 'LLAMA_ARG_CACHE_RAM="$(ods_default_llama_cache_ram_mib' "$phase06" | head -1 | cut -d: -f1)"
template_line="$(grep -nF 'echo "LLAMA_ARG_CACHE_RAM=${LLAMA_ARG_CACHE_RAM}"' "$phase06" | head -1 | cut -d: -f1)"
limit_line="$(grep -nF 'LLAMA_SERVER_MEMORY_LIMIT_VALUE=""' "$phase06" | head -1 | cut -d: -f1)"
if [[ -z "$cache_line" || -z "$template_line" || "$limit_line" -ge "$cache_line" || "$cache_line" -ge "$template_line" ]]; then
    printf '[FAIL] phase 06 must size the prompt cache after the container limit and before writing .env\n' >&2
    exit 1
fi

# Run phase 06's own prompt-cache block against fixtures.
cache_block="$(awk '
    /# llama.cpp.s RAM prompt cache \(b9014 default 8192 MiB\) sits outside every/ { emit = 1 }
    emit && /^    ODS_MODE_VALUE=/ { exit }
    emit { print }
' "$phase06")"
env_get_definition="$(awk '
    $0 == "    _env_get() {" { emit = 1 }
    emit { print }
    emit && $0 == "    }" { exit }
' "$phase06")"
[[ -n "$cache_block" && -n "$env_get_definition" ]] \
    || { printf '[FAIL] could not extract the phase 06 prompt-cache block\n' >&2; exit 1; }
# shellcheck source=../lib/safe-env.sh
source "$ROOT_DIR/lib/safe-env.sh"
cache_tmp="$(mktemp -d)"
trap 'rm -rf "$cache_tmp"' EXIT

# phase06_cache BACKEND RAM_GB DOCKER_GB CONTAINER_LIMIT ENV_FILE_VALUE PROFILE_VALUE [MODE EXTERNAL LEMONADE_EXTERNAL]
phase06_cache() {
    (
        set -euo pipefail
        log() { :; }
        eval "$env_get_definition"
        GPU_BACKEND="$1" RAM_GB="$2"
        _fixture_docker_gb="$3"
        ods_docker_memory_gb() { [[ -n "$_fixture_docker_gb" ]] || return 1; printf '%s\n' "$_fixture_docker_gb"; }
        LLAMA_SERVER_MEMORY_LIMIT_VALUE="$4"
        _env_existing=""
        if [[ -n "$5" ]]; then
            _env_existing="$cache_tmp/.env"
            printf 'LLAMA_ARG_CACHE_RAM=%s\n' "$5" > "$_env_existing"
        fi
        LLAMA_ARG_CACHE_RAM="$6"
        ODS_MODE="${7:-local}" EXTERNAL_LLM_ACTIVE="${8:-false}" LEMONADE_EXTERNAL_VALUE="${9:-false}"
        eval "$cache_block"
        printf '%s\n' "${LLAMA_ARG_CACHE_RAM:-}"
    )
}

assert_eq "$(phase06_cache nvidia 15 15 12G '' '')" "3072" "phase 06: 16 GB WSL VM with the 8 GB-GPU profile"
assert_eq "$(phase06_cache nvidia 31 15 12G '' '')" "3072" "phase 06: Docker's smaller VM reading wins"
assert_eq "$(phase06_cache nvidia 15 '' 12G '' '')" "3072" "phase 06: host RAM when Docker does not answer"
assert_eq "$(phase06_cache nvidia 125 125 64G '' '')" "" "phase 06: tower keeps llama.cpp's default"
assert_eq "$(phase06_cache nvidia 15 15 12G 4096 '')" "4096" "phase 06: the owner's .env value is kept"
assert_eq "$(phase06_cache nvidia 15 15 12G 4096 1024)" "1024" "phase 06: a runtime profile's value wins"
assert_eq "$(phase06_cache cpu 64 64 '' '' '')" "1536" "phase 06: CPU compose limit (6G) bounds the cache"
assert_eq "$(phase06_cache none 64 64 8G '' '')" "2048" "phase 06: CPU profile container limit bounds the cache"
assert_eq "$(phase06_cache intel 15 15 '' '' '')" "3072" "phase 06: Intel Docker llama-server"
assert_eq "$(phase06_cache amd 15 15 '' '' '')" "" "phase 06: Lemonade (AMD) is left alone"
assert_eq "$(phase06_cache apple 15 15 '' '' '')" "" "phase 06: Apple is left alone"
assert_eq "$(phase06_cache nvidia 15 15 12G '' '' cloud)" "" "phase 06: cloud mode is left alone"
assert_eq "$(phase06_cache nvidia 15 15 12G '' '' local true)" "" "phase 06: an external LLM is left alone"
assert_eq "$(phase06_cache nvidia 15 15 12G '' '' local false true)" "" "phase 06: external Lemonade is left alone"
printf '[PASS] llama.cpp RAM prompt-cache default contract\n'
