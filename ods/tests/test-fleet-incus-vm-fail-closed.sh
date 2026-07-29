#!/usr/bin/env bash
# Regression: every Incus VM lane must fail closed, and RHEL-family VM
# provisioning must install the extra modules required by Docker networking.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/tests/fleet-incus-vm.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$TARGET" ]] || fail "missing $TARGET"

run_lane_block="$(
    sed -n '/^run_lane() {/,/^}/p' "$TARGET"
)"
[[ -n "$run_lane_block" ]] || fail "could not locate run_lane"

grep -qF 'run_vm_check "$vm" "$lane" "$installer_mode" || check_rc=$?' \
    <<<"$run_lane_block" \
    || fail "run_lane must capture a failed VM check explicitly"
pass "run_lane captures the VM check exit code"

grep -qF 'if ((check_rc != 0)); then' <<<"$run_lane_block" \
    || fail "run_lane must test the captured VM check exit code"
grep -qF 'fail "${LABELS[$lane]} VM validation failed (rc=$check_rc)"' \
    <<<"$run_lane_block" \
    || fail "run_lane must fail the matrix when a VM check fails"
pass "a failed VM check cannot be overwritten by VM cleanup"

dnf_block="$(
    sed -n '/^install_dnf_deps() {/,/^}/p' "$TARGET"
)"
[[ -n "$dnf_block" ]] || fail "could not locate install_dnf_deps"

grep -qF 'if ! modprobe -n xt_addrtype' <<<"$dnf_block" \
    || fail "RHEL-family setup must detect the Docker addrtype kernel module"
grep -qF '"kernel-modules-extra-$(uname -r)"' <<<"$dnf_block" \
    || fail "RHEL-family setup must install extra modules for the running kernel"
pass "RHEL-family setup provisions Docker bridge-networking modules"

echo "[OK] fleet-incus-vm.sh fails closed and provisions required kernel modules"
