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

# --- regression: run_vm_check rc handling (append) ---
[[ -f "$TARGET" ]] || fail "target missing: $TARGET"

run_vm_check_src="$(sed -n '/^run_vm_check()/,/^}/p' "$TARGET")"
[[ -n "$run_vm_check_src" ]] || fail "could not extract run_vm_check from $TARGET"

# in-memory stubs
incus_calls=0
incus_rcs=()
incus() {
    incus_calls=$((incus_calls + 1))
    local r="${incus_rcs[$((incus_calls - 1))]:-0}"
    return "$r"
}
vm_boot_id() { printf 'boot-%s\n' "$1"; }
wait_for_reboot() { reboot_calls=$((reboot_calls + 1)); return 0; }
log() { :; }

reboot_calls=0
# These arrays are consumed by the function loaded from TARGET.
# shellcheck disable=SC2034
declare -A EXPECTED_PKG=([0]=pkgA)
# shellcheck disable=SC2034
declare -A LABELS=([0]=laneA)

# load actual definition
eval "$run_vm_check_src"

reset_stubs() {
    incus_calls=0
    reboot_calls=0
    incus_rcs=("$@")
}

# Case 1: ordinary command exits 23 -> returns 23
reset_stubs 23
if run_vm_check vm1 0 0; then st=0; else st=$?; fi
[[ "$st" -eq 23 ]] || fail "case1: expected 23 got $st"
[[ "$incus_calls" -eq 1 ]] || fail "case1: expected 1 incus call got $incus_calls"
[[ "$reboot_calls" -eq 0 ]] || fail "case1: unexpected reboot"

# Case 2: success 0 -> returns 0, no reboot
reset_stubs 0
if run_vm_check vm1 0 0; then st=0; else st=$?; fi
[[ "$st" -eq 0 ]] || fail "case2: expected 0 got $st"
[[ "$incus_calls" -eq 1 ]] || fail "case2: expected 1 incus call got $incus_calls"
[[ "$reboot_calls" -eq 0 ]] || fail "case2: unexpected reboot"

# Case 3: first 75 then 0 -> reboot exactly once, two incus calls, returns 0
reset_stubs 75 0
if run_vm_check vm1 0 0; then st=0; else st=$?; fi
[[ "$st" -eq 0 ]] || fail "case3: expected 0 got $st"
[[ "$incus_calls" -eq 2 ]] || fail "case3: expected 2 incus calls got $incus_calls"
[[ "$reboot_calls" -eq 1 ]] || fail "case3: expected 1 reboot got $reboot_calls"

# Case 4: first 75 then 19 -> returns 19
reset_stubs 75 19
if run_vm_check vm1 0 0; then st=0; else st=$?; fi
[[ "$st" -eq 19 ]] || fail "case4: expected 19 got $st"
[[ "$incus_calls" -eq 2 ]] || fail "case4: expected 2 incus calls got $incus_calls"
[[ "$reboot_calls" -eq 1 ]] || fail "case4: expected 1 reboot got $reboot_calls"

pass "run_vm_check rc regression"

# Globals below are consumed by the real functions loaded from TARGET.
# shellcheck disable=SC2034
test_reboot_payload_persistence() (
    unset EXPECTED_PKG LABELS
    declare -A IMAGES=([arch]=images:arch) EXPECTED_PKG=([arch]=pacman) LABELS=([arch]=Arch)
    declare -A vm_files=()
    local PREFIX=fake RUN_ID=test CPU=2 MEMORY=4GiB KEEP_VMS=true
    local RUN_INSTALLER_DRY_RUN=true PAYLOAD=payload VM_CHECK=check
    local check_calls=0 reboot_calls=0 guest_archive
    local -a CREATED_VMS=()
    # Read only the guest archive operand; never run guest setup on the host.
    guest_archive="$(sed -n '/^extract_source() {/,/^}/p' "$TARGET" |
        sed -n 's/^[[:space:]]*tar -xzf \([^ ]*\) .*/\1/p')"
    [[ -n "$guest_archive" ]] || fail "guest archive operand missing"
    eval "$run_lane_block"
    incus() {
        case "$1" in
            init|config|start) return 0 ;;
            file)
                [[ "$2" == push ]] || fail "unexpected file operation"
                vm_files["/${4#*/}"]=1
                ;;
            exec)
                [[ "$3" == -- ]] || fail "unexpected exec shape"
                if [[ "$4" == chmod ]]; then
                    [[ ${vm_files[$6]:-} == 1 ]] || fail "chmod target was not staged"
                    return 0
                fi
                [[ ${vm_files[$4]:-} == 1 ]] || fail "check script disappeared after reboot"
                check_calls=$((check_calls + 1))
                if ((check_calls == 1)); then return 75; fi
                [[ ${vm_files[$guest_archive]:-} == 1 ]] || fail "guest source archive disappeared after reboot"
                ;;
            *) fail "unexpected Incus command" ;;
        esac
    }
    wait_for_exec() { :; }
    wait_for_reboot() {
        local path
        reboot_calls=$((reboot_calls + 1))
        for path in "${!vm_files[@]}"; do
            if [[ "$path" == /tmp/* ]]; then unset 'vm_files[$path]'; fi
        done
    }
    run_lane arch
    [[ "$check_calls" == 2 && "$reboot_calls" == 1 ]] || fail "reboot retry was not completed"
    pass "staged check and guest source survive a tmpfs reboot"
)
test_reboot_payload_persistence

echo "[OK] fleet-incus-vm.sh propagates command failures and provisions required kernel modules"
