#!/usr/bin/env bash
# Regression: ods-cli's _get_host_logical_cpus() must detect the host core
# count on macOS, not silently fall back to 1. nproc and /proc/cpuinfo are
# Linux-only; without a sysctl probe in between, every macOS host reports a
# single logical CPU, which starves the llama-server/bundled-service CPU
# budget whenever Docker's own NCPU is unavailable.
set -euo pipefail

# ods-cli requires Bash 4+; macOS ships 3.2.
if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$modern_bash" ]] && exec "$modern_bash" "$0" "$@"
    done
    printf '[SKIP] ods-cli requires Bash 4+\n'
    exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

[[ -f "$ODS_CLI" ]] || fail "missing $ODS_CLI"

# ods-cli has no `main` source guard, but its `version` dispatch just echoes and
# falls through, so sourcing it under `set -- version` leaves every helper
# defined without running a command. Run in a subshell so the stub PATH and the
# sourced globals never leak between cases.
host_cpus_with_stubs() {
    local stub_dir="$1"
    (
        set -- version
        export PATH="$stub_dir:$PATH"
        # shellcheck source=/dev/null
        source "$ODS_CLI" >/dev/null 2>&1
        _get_host_logical_cpus
    )
}

# Case 1: nproc absent, sysctl present -> the sysctl value wins (before the
# Linux /proc/cpuinfo probe), on any host. This is the macOS path.
stub_dir="$(mktemp -d)"
trap 'rm -rf "$stub_dir"' EXIT
printf '#!/bin/sh\nexit 127\n' > "$stub_dir/nproc"       # simulate "nproc: not found"
printf '#!/bin/sh\necho 7\n'   > "$stub_dir/sysctl"      # hw.logicalcpu -> 7
chmod +x "$stub_dir/nproc" "$stub_dir/sysctl"

result="$(host_cpus_with_stubs "$stub_dir")" || fail "_get_host_logical_cpus errored"
[[ "$result" == "7" ]] \
    || fail "expected the sysctl core count (7) when nproc is unavailable, got '$result'"
pass "sysctl hw.logicalcpu is used when nproc is unavailable"

# Case 2: on a real macOS host, the reported count matches sysctl exactly and is
# not the degenerate 1 (unless the Mac genuinely has one core).
if [[ "$(uname)" == "Darwin" ]]; then
    real="$(sysctl -n hw.logicalcpu)"
    result="$(set -- version; source "$ODS_CLI" >/dev/null 2>&1; _get_host_logical_cpus)"
    [[ "$result" == "$real" ]] \
        || fail "on macOS expected $real logical CPUs, got '$result'"
    pass "real macOS host reports its true logical CPU count ($real)"
else
    pass "skipping real-macOS assertion on $(uname)"
fi

printf 'ods-cli host CPU fallback tests passed.\n'
