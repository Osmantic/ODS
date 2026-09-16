#!/usr/bin/env bats

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    log() { :; }
    warn() { printf '%s\n' "$*"; }
    export LOG_FILE="$BATS_TEST_TMPDIR/packages.log"
    source "$BATS_TEST_DIRNAME/../../installers/lib/packaging.sh"
    _SUDO=""
    PKG_RETRY_ATTEMPTS=3
    PKG_RETRY_DELAY=0
    # Keep all OS writes behind inert command fixtures.
    _pkg_prepare_pacman_keyrings() { :; }
    _pkg_configure_zypper_ci_network() { :; }
    unset CI GITHUB_ACTIONS
    export CALLS="$BATS_TEST_TMPDIR/calls"
    : >"$CALLS"
    pacman() { printf '%s\n' "$*" >>"$CALLS"; printf 'signature rejected\n' >&2; return 37; }
    zypper() { printf '%s\n' "$*" >>"$CALLS"; printf 'repository unavailable\n' >&2; return 106; }
}

@test "pacman install preserves failure after exhausting configured attempts" {
    PKG_MANAGER=pacman
    run pkg_install jq rsync
    assert_failure 37
    [ "$(wc -l <"$CALLS")" -eq 3 ]
    grep -F -- '-S --noconfirm --needed jq rsync' "$CALLS"
    grep -F 'signature rejected' "$LOG_FILE"
}

@test "zypper install preserves repository failure and its diagnostics" {
    PKG_MANAGER=zypper
    run pkg_install jq rsync
    assert_failure 106
    [ "$(wc -l <"$CALLS")" -eq 3 ]
    grep -F -- '--non-interactive install -y jq rsync' "$CALLS"
    grep -F 'repository unavailable' "$LOG_FILE"
}

@test "pacman update preserves failure for a single attempt" {
    PKG_MANAGER=pacman
    PKG_RETRY_ATTEMPTS=1
    run pkg_update
    assert_failure 37
    assert_output ''
    [ "$(wc -l <"$CALLS")" -eq 1 ]
    grep -F -- '-Syyu --noconfirm' "$CALLS"
}

@test "zypper refresh returns the final command status" {
    PKG_MANAGER=zypper
    run pkg_update
    assert_failure 106
    [ "$(wc -l <"$CALLS")" -eq 3 ]
    grep -F -- '--non-interactive --gpg-auto-import-keys refresh' "$CALLS"
}

@test "a successful later package attempt still succeeds and stops immediately" {
    PKG_MANAGER=zypper
    zypper() {
        printf '%s\n' "$*" >>"$CALLS"
        if [ "$(wc -l <"$CALLS")" -eq 1 ]; then return 106; fi
        return 0
    }
    run pkg_install jq
    assert_success
    [ "$(wc -l <"$CALLS")" -eq 2 ]
}

@test "package failure terminates a strict installer caller before the next phase" {
    # BATS run executes functions in a conditional context; use a fresh shell
    # to exercise the installer's actual errexit boundary.
    run bash -c '
        set -euo pipefail
        source "$1"
        _SUDO=""
        PKG_MANAGER=pacman
        PKG_RETRY_ATTEMPTS=1
        log() { :; }
        _pkg_prepare_pacman_keyrings() { :; }
        pacman() { return 37; }
        pkg_install jq
        printf "next phase must not run\n"
    ' -- "$BATS_TEST_DIRNAME/../../installers/lib/packaging.sh"
    assert_failure 37
    refute_output --partial 'next phase must not run'
}
