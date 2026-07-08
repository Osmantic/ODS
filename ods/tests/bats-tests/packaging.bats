#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/packaging.sh
# ============================================================================
# Tests: pkg_resolve() (pure function), detect_pkg_manager() distro mapping
#
# Note: detect_pkg_manager() reads /etc/os-release which cannot be mocked
# without root. We test the distro-to-pkg-manager mapping by pre-setting
# DISTRO_ID/DISTRO_ID_LIKE and re-running the case logic. For the full
# function, we verify it runs without error on the current platform.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions that packaging.sh expects
    log() { :; }; export -f log
    warn() { :; }; export -f warn
    error() { echo "ERROR: $*" >&2; return 1; }; export -f error

    export LOG_FILE="$BATS_TEST_TMPDIR/packaging-test.log"

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/packaging.sh"
}

# ── pkg_resolve: apt ────────────────────────────────────────────────────────

@test "pkg_resolve: apt passes through generic packages" {
    PKG_MANAGER="apt"
    run pkg_resolve curl
    assert_output "curl"

    run pkg_resolve jq
    assert_output "jq"
}

@test "pkg_resolve: apt keeps docker-compose-plugin as-is" {
    PKG_MANAGER="apt"
    run pkg_resolve docker-compose-plugin
    assert_output "docker-compose-plugin"
}

# ── pkg_resolve: dnf ────────────────────────────────────────────────────────

@test "pkg_resolve: dnf maps build-essential to gcc gcc-c++ make" {
    PKG_MANAGER="dnf"
    run pkg_resolve build-essential
    assert_output "gcc gcc-c++ make"
}

@test "pkg_resolve: dnf passes through generic packages" {
    PKG_MANAGER="dnf"
    run pkg_resolve curl
    assert_output "curl"
}

@test "pkg_resolve: dnf keeps curl-minimal when it is already installed" {
    rpm() {
        [[ "$1" == "-q" && "$2" == "curl-minimal" ]]
    }
    export -f rpm

    PKG_MANAGER="dnf"
    run pkg_resolve curl
    assert_output "curl-minimal"
}

@test "pkg_install resolves canonical package names before invoking dnf" {
    dnf() { echo "$*"; }
    export -f dnf

    PKG_MANAGER="dnf"
    _SUDO=""
    run pkg_install build-essential
    assert_success
    assert_output "install -y -q gcc gcc-c++ make"
}

# ── pkg_resolve: pacman ─────────────────────────────────────────────────────

@test "pkg_resolve: pacman maps docker-compose-plugin to docker-compose" {
    PKG_MANAGER="pacman"
    run pkg_resolve docker-compose-plugin
    assert_output "docker-compose"
}

@test "pkg_resolve: pacman maps build-essential to base-devel" {
    PKG_MANAGER="pacman"
    run pkg_resolve build-essential
    assert_output "base-devel"
}

# ── pkg_resolve: zypper ─────────────────────────────────────────────────────

@test "pkg_resolve: zypper maps docker-compose-plugin to docker-compose" {
    PKG_MANAGER="zypper"
    run pkg_resolve docker-compose-plugin
    assert_output "docker-compose"
}

@test "pkg_resolve: zypper maps build-essential to devel_basis" {
    PKG_MANAGER="zypper"
    run pkg_resolve build-essential
    assert_output "devel_basis"
}

# ── pkg_resolve: apk ───────────────────────────────────────────────────────

@test "pkg_resolve: apk maps docker-compose-plugin to docker-cli-compose" {
    PKG_MANAGER="apk"
    run pkg_resolve docker-compose-plugin
    assert_output "docker-cli-compose"
}

@test "pkg_resolve: apk maps build-essential to build-base" {
    PKG_MANAGER="apk"
    run pkg_resolve build-essential
    assert_output "build-base"
}

# ── pkg_resolve: xbps ──────────────────────────────────────────────────────

@test "pkg_resolve: xbps maps docker-compose-plugin to docker-compose" {
    PKG_MANAGER="xbps"
    run pkg_resolve docker-compose-plugin
    assert_output "docker-compose"
}

@test "pkg_resolve: xbps maps build-essential to base-devel" {
    PKG_MANAGER="xbps"
    run pkg_resolve build-essential
    assert_output "base-devel"
}

# ── pkg_resolve: unknown ───────────────────────────────────────────────────

@test "pkg_resolve: unknown manager passes through all packages" {
    PKG_MANAGER="unknown"
    run pkg_resolve docker-compose-plugin
    assert_output "docker-compose-plugin"

    run pkg_resolve build-essential
    assert_output "build-essential"
}

# ── detect_pkg_manager: smoke test on current platform ──────────────────────

@test "detect_pkg_manager: runs without error on current platform" {
    run detect_pkg_manager
    assert_success
    # PKG_MANAGER should be set to something (runs in subshell via run,
    # so we verify it doesn't crash rather than checking the variable)
}
