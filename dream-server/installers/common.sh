#!/bin/bash
# Shared installer helpers for platform dispatch.

set -euo pipefail

is_termux_shell() {
    local prefix="${PREFIX:-}"

    if [[ -n "${TERMUX_VERSION:-}" && "$prefix" == "/data/data/com.termux/files/usr" ]]; then
        return 0
    fi

    if [[ "$prefix" == "/data/data/com.termux/files/usr" && -d "$prefix" ]] && command -v termux-info >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

is_ios_app_container_path() {
    local value="${1:-}"

    [[ "$value" =~ ^(/private)?/var/mobile/Containers/Data/Application/ ]]
}

is_ashell_shell() {
    local term_program="${TERM_PROGRAM:-}"
    local ostype="${OSTYPE:-}"
    local has_ashell_term=1
    local has_ios_container=1

    if [[ "$term_program" == "a-Shell" || "$term_program" == "a-Shell mini" ]]; then
        has_ashell_term=0
    fi

    if is_ios_app_container_path "${HOME:-}" || is_ios_app_container_path "${PWD:-}"; then
        has_ios_container=0
    fi

    if [[ $has_ashell_term -eq 0 && $has_ios_container -eq 0 ]]; then
        return 0
    fi

    if [[ "$ostype" == darwin* && $has_ios_container -eq 0 ]]; then
        if [[ -n "${ASHELL:-}" || -n "${SHORTCUTS:-}" ]]; then
            return 0
        fi
        if command -v pickFolder >/dev/null 2>&1 || command -v lg2 >/dev/null 2>&1; then
            return 0
        fi
    fi

    return 1
}

detect_platform() {
    if [[ -n "${DREAM_PLATFORM_OVERRIDE:-}" ]]; then
        echo "$DREAM_PLATFORM_OVERRIDE"
    elif is_termux_shell; then
        echo "android-termux"
    elif is_ashell_shell; then
        echo "ios-ashell"
    elif [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
    elif [[ "${OSTYPE:-}" == "msys"* || "${OSTYPE:-}" == "cygwin"* || "${OSTYPE:-}" == "win32"* ]]; then
        echo "windows"
    elif [[ "${OSTYPE:-}" == "darwin"* ]]; then
        echo "macos"
    elif [[ "${OSTYPE:-}" == linux* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}
