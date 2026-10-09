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

run_vm_check_block="$(sed -n '/^run_vm_check() {/,/^}/p' "$TARGET")"
[[ -n "$run_vm_check_block" ]] || fail "could not locate run_vm_check"

# Exercise the real function with fake Incus responses. Calling it in an OR
# list matches run_lane, where errexit cannot supply missing exit handling.
check_vm_result() (
    local first_exit="$1" retry_exit="$2" expected_exit="$3"
    local expected_calls="$4" expected_reboots="$5"
    local exec_calls=0 reboot_calls=0 rc=0
    local -A EXPECTED_PKG=([test]=apt)
    local -A LABELS=([test]="Test VM")
    vm_boot_id() { printf '%s\n' 'test-boot-id'; }
    log() { :; }
    wait_for_reboot() { reboot_calls=$((reboot_calls + 1)); }
    incus() {
        [[ "$1" == exec ]] || fail "unexpected Incus operation: $1"
        exec_calls=$((exec_calls + 1))
        if ((exec_calls == 1)); then
            return "$first_exit"
        fi
        return "$retry_exit"
    }
    eval "$run_vm_check_block"
    run_vm_check test-vm test run || rc=$?
    [[ "$rc" == "$expected_exit" ]] \
        || fail "VM exit $first_exit / retry $retry_exit returned $rc, expected $expected_exit"
    [[ "$exec_calls" == "$expected_calls" && "$reboot_calls" == "$expected_reboots" ]] \
        || fail "VM exit $first_exit used $exec_calls checks and $reboot_calls reboot waits"
)

check_vm_result 0 0 0 1 0
check_vm_result 1 0 1 1 0
check_vm_result 137 0 137 1 0
pass "successful checks and ordinary failures preserve their exit status"
check_vm_result 75 0 0 2 1
check_vm_result 75 23 23 2 1
check_vm_result 75 75 75 2 1
pass "reboot requests wait and retry once, preserving retry failures"

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
