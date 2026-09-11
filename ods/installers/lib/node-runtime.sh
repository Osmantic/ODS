#!/usr/bin/env bash
# Validate that Node.js tooling belongs to the Linux host and is new enough for
# ODS host agents. WSL inherits Windows PATH entries by default; accepting
# /mnt/c/.../npm without a Linux node silently installs into the wrong OS.

ods_linux_node_tools_available() {
    local node_bin="${1:-}" npm_bin="${2:-}" node_real npm_real major
    [[ -n "$node_bin" ]] || node_bin="$(command -v node 2>/dev/null || true)"
    [[ -n "$npm_bin" ]] || npm_bin="$(command -v npm 2>/dev/null || true)"
    [[ "$node_bin" == /* && "$npm_bin" == /* && -x "$node_bin" && -x "$npm_bin" ]] || return 1
    node_real="$(readlink -f -- "$node_bin" 2>/dev/null || true)"
    npm_real="$(readlink -f -- "$npm_bin" 2>/dev/null || true)"
    [[ -n "$node_real" && -n "$npm_real" ]] || return 1
    case "$node_real:$npm_real" in
        /mnt/[A-Za-z]/*|*:/mnt/[A-Za-z]/*) return 1 ;;
    esac
    major="$($node_bin -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
    [[ "$major" =~ ^[0-9]+$ && "$major" -ge 20 ]]
}
