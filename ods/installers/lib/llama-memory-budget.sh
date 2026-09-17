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
