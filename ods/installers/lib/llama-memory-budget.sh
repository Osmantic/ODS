#!/bin/bash

# Return the memory visible to the Docker engine in whole GiB. Docker Desktop
# may expose less memory than the physical host, so callers should prefer this
# value when it is available.
ods_docker_memory_gb() {
    command -v docker >/dev/null 2>&1 || return 1

    local bytes
    bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
    [[ "$bytes" =~ ^[0-9]+$ ]] || return 1
    (( bytes >= 1073741824 )) || return 1
    printf '%s\n' "$((bytes / 1073741824))"
}

# Choose the smaller positive memory reading. This protects Docker Desktop
# installs whose VM allocation is lower than the physical host RAM.
ods_effective_container_memory_gb() {
    local host_gb="${1:-0}" docker_gb="${2:-0}"

    [[ "$host_gb" =~ ^[0-9]+$ ]] || host_gb=0
    [[ "$docker_gb" =~ ^[0-9]+$ ]] || docker_gb=0

    if (( host_gb > 0 && docker_gb > 0 )); then
        (( host_gb < docker_gb )) && printf '%s\n' "$host_gb" || printf '%s\n' "$docker_gb"
    elif (( docker_gb > 0 )); then
        printf '%s\n' "$docker_gb"
    else
        printf '%s\n' "$host_gb"
    fi
}

# A small Docker VM cannot afford two independent Kokoro model copies alongside
# Portal, Pixel, and the rest of ODS. Unknown memory retains the old default.
ods_default_tts_workers() {
    local memory_gb="${1:-0}"
    [[ "$memory_gb" =~ ^[0-9]+$ ]] || memory_gb=0
    if (( memory_gb > 0 && memory_gb < 12 )); then
        printf '%s\n' 1
    else
        printf '%s\n' 2
    fi
}

# Physical CPU cores (unique core/socket pairs from lscpu), else the logical
# CPU count. Prints nothing when neither is available.
ods_physical_cpu_cores() {
    local cores=""
    if command -v lscpu >/dev/null 2>&1; then
        cores="$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | grep -c . || true)"
    fi
    if [[ ! "$cores" =~ ^[1-9][0-9]*$ ]] && command -v nproc >/dev/null 2>&1; then
        cores="$(nproc 2>/dev/null || true)"
    fi
    [[ "$cores" =~ ^[1-9][0-9]*$ ]] && printf '%s\n' "$cores"
    return 0
}

# CPU-backend llama.cpp threads: one per physical core (llama.cpp's own
# default), bounded by the container CPU limit (LLAMA_CPU_LIMIT, default 8).
# 4 when the core count is unknown, the historical compose default.
ods_default_cpu_llama_threads() {
    local cores="${1:-}" cpu_limit="${2:-8}" limit
    limit="${cpu_limit%%.*}"
    [[ "$limit" =~ ^[0-9]+$ ]] || limit=8
    (( limit < 1 )) && limit=1
    if [[ ! "$cores" =~ ^[1-9][0-9]*$ ]]; then
        (( limit < 4 )) && { printf '%s\n' "$limit"; return; }
        printf '%s\n' 4
        return
    fi
    (( cores > limit )) && cores="$limit"
    printf '%s\n' "$cores"
}

# Keep the NVIDIA llama-server below the memory available to its Docker
# engine. Reserve 3 GiB on sub-16 GiB systems and 4 GiB otherwise for the OS,
# Docker, and the rest of the ODS stack. The historical 64 GiB value remains
# the upper bound and the fallback when detection is unavailable.
ods_default_nvidia_llama_memory_limit() {
    local memory_gb="${1:-0}" reserve_gb usable_gb

    [[ "$memory_gb" =~ ^[0-9]+$ ]] || memory_gb=0
    if (( memory_gb <= 0 )); then
        printf '%s\n' "64G"
        return
    fi

    if (( memory_gb < 16 )); then
        reserve_gb=3
    else
        reserve_gb=4
    fi

    usable_gb=$((memory_gb - reserve_gb))
    (( usable_gb < 1 )) && usable_gb=1
    (( usable_gb > 64 )) && usable_gb=64
    printf '%sG\n' "$usable_gb"
}

# A Docker memory limit (LLAMA_SERVER_MEMORY_LIMIT: 12G, 512m, 12gb or plain
# bytes) in MiB. Prints nothing for a value it cannot read.
ods_memory_limit_mib() {
    local value="${1:-}" number unit
    [[ "$value" =~ ^([0-9]+)([kKmMgGtT]?)[bB]?$ ]] || return 0
    number="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    case "$unit" in
        t|T) printf '%s\n' "$((number * 1024 * 1024))" ;;
        g|G) printf '%s\n' "$((number * 1024))" ;;
        m|M) printf '%s\n' "$number" ;;
        k|K) printf '%s\n' "$((number / 1024))" ;;
        *) printf '%s\n' "$((number / 1048576))" ;;
    esac
}

# Default llama.cpp --cache-ram (LLAMA_ARG_CACHE_RAM) in MiB for a llama-server
# ODS runs in Docker. llama.cpp keeps earlier prompts, with their context
# checkpoints, in host RAM so a conversation that lost the slot resumes without
# re-processing its prompt. b9014 caps that cache at 8192 MiB, outside every
# ODS memory check, and a 16 GB WSL VM OOM-killed llama-server at ~10 GB RSS
# with it. The default is a quarter of the memory left after 6 GiB for the rest
# of ODS and the OS, and at most a quarter of the llama-server container limit
# (so weights, KV and checkpoints keep the rest). 512 MiB is the floor; b9014
# always keeps the newest cached prompt even when it alone exceeds the cap.
# Arguments: effective memory in whole GiB (0 when unknown) and the container
# memory limit. Prints nothing when llama.cpp's own 8192 MiB default fits.
ods_default_llama_cache_ram_mib() {
    local memory_gb="${1:-0}" limit_mib cache_mib=8192
    [[ "$memory_gb" =~ ^[0-9]+$ ]] || memory_gb=0
    limit_mib="$(ods_memory_limit_mib "${2:-}")"

    if (( memory_gb > 0 )); then
        local headroom_gb=$((memory_gb - 6))
        (( headroom_gb < 0 )) && headroom_gb=0
        (( headroom_gb * 256 < cache_mib )) && cache_mib=$((headroom_gb * 256))
    fi
    if [[ -n "$limit_mib" ]] && (( limit_mib / 4 < cache_mib )); then
        cache_mib=$((limit_mib / 4))
    fi
    (( cache_mib < 512 )) && cache_mib=512
    (( cache_mib < 8192 )) && printf '%s\n' "$cache_mib"
    return 0
}
