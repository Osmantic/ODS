#!/usr/bin/env bash
# _get_host_logical_cpus must not report 1 on macOS (#5641).
#
# It probed only nproc and /proc/cpuinfo, both Linux-only, so every macOS host
# fell through to the literal "1". That value is the fallback inside
# _get_docker_available_cpus, which caps the per-service CPU budget — so an
# 11-core Apple-silicon box provisioned the whole stack for one core.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT/ods-cli"

PASS=0; FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

FUNCS="$(mktemp)"
TMPBIN="$(mktemp -d)"
trap 'rm -rf "$FUNCS" "$TMPBIN"' EXIT

awk '/^_get_host_logical_cpus\(\)/,/^}/' "$ODS_CLI" > "$FUNCS"
[[ -s "$FUNCS" ]] || { echo "[FAIL] could not extract _get_host_logical_cpus"; exit 1; }

# run_with <nproc-output|-> <sysctl-output|-> <cpuinfo-count|->
# "-" means that probe is unavailable on the simulated host.
run_with() {
    local nproc_out="$1" sysctl_out="$2" cpuinfo="$3" fake_root="$TMPBIN/root"
    rm -rf "$TMPBIN/bin" "$fake_root"; mkdir -p "$TMPBIN/bin" "$fake_root"

    # Always shim, never merely omit: the CI runner has a real /usr/bin/nproc
    # further down PATH, so leaving it out simulates nothing. "-" means the
    # probe fails, which is what makes the `||` chain advance.
    if [[ "$nproc_out" != "-" ]]; then
        printf '#!/bin/sh\necho %s\n' "$nproc_out" > "$TMPBIN/bin/nproc"
    else
        printf '#!/bin/sh\nexit 127\n' > "$TMPBIN/bin/nproc"
    fi
    chmod +x "$TMPBIN/bin/nproc"
    if [[ "$sysctl_out" != "-" ]]; then
        printf '#!/bin/sh\necho %s\n' "$sysctl_out" > "$TMPBIN/bin/sysctl"
    else
        # Linux: sysctl exists but has no hw.logicalcpu key -> non-zero exit.
        printf '#!/bin/sh\nexit 1\n' > "$TMPBIN/bin/sysctl"
    fi
    chmod +x "$TMPBIN/bin/sysctl"

    # grep is shimmed so /proc/cpuinfo can be simulated without touching /proc.
    if [[ "$cpuinfo" != "-" ]]; then
        printf '#!/bin/sh\necho %s\n' "$cpuinfo" > "$TMPBIN/bin/grep"
    else
        printf '#!/bin/sh\nexit 2\n' > "$TMPBIN/bin/grep"
    fi
    chmod +x "$TMPBIN/bin/grep"

    PATH="$TMPBIN/bin:$PATH" bash -c "source '$FUNCS'; _get_host_logical_cpus"
}

# macOS: no nproc, no /proc/cpuinfo, sysctl reports the truth.
got="$(run_with - 11 -)"
if [[ "$got" == "11" ]]; then
    pass "macOS host reports its real logical CPU count ($got)"
else
    fail "macOS host reported '$got', expected 11"
fi

# Linux: nproc wins, ordering unchanged.
got="$(run_with 8 11 4)"
if [[ "$got" == "8" ]]; then
    pass "Linux still prefers nproc ($got)"
else
    fail "Linux preferred '$got', expected nproc's 8"
fi

# Linux without nproc: falls through sysctl (no such key) to /proc/cpuinfo.
got="$(run_with - - 4)"
if [[ "$got" == "4" ]]; then
    pass "Linux without nproc falls back to /proc/cpuinfo ($got)"
else
    fail "expected /proc/cpuinfo's 4, got '$got'"
fi

# Nothing available: the documented last resort.
got="$(run_with - - -)"
if [[ "$got" == "1" ]]; then
    pass "unknown host still degrades to 1"
else
    fail "expected 1 on an unprobeable host, got '$got'"
fi

# A non-numeric probe result must not escape as a CPU count.
got="$(run_with - "not-a-number" -)"
if [[ "$got" == "1" ]]; then
    pass "non-numeric probe output is rejected"
else
    fail "non-numeric output leaked through as '$got'"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
