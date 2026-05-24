#!/usr/bin/env bats

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export HOME="/tmp/dream-dispatch-test-home"
    unset DREAM_PLATFORM_OVERRIDE TERMUX_VERSION PREFIX TERM_PROGRAM ASHELL SHORTCUTS
    source "$BATS_TEST_DIRNAME/../../installers/common.sh"
    source "$BATS_TEST_DIRNAME/../../installers/dispatch.sh"
}

@test "detect_platform: recognizes Termux with Termux prefix" {
    export TERMUX_VERSION="0.118.3"
    export PREFIX="/data/data/com.termux/files/usr"

    run detect_platform
    assert_success
    assert_output "android-termux"
}

@test "detect_platform: TERMUX_VERSION alone does not override host platform" {
    export TERMUX_VERSION="0.118.3"
    export PREFIX="/usr/local"

    run detect_platform
    assert_success
    refute_output "android-termux"
}

@test "detect_platform: recognizes a-Shell with iOS container path" {
    export TERM_PROGRAM="a-Shell"
    export HOME="/private/var/mobile/Containers/Data/Application/123/Documents"

    run detect_platform
    assert_success
    assert_output "ios-ashell"
}

@test "detect_platform: ASHELL alone does not route desktop shells to iOS" {
    export ASHELL="1"
    export HOME="/Users/example"

    run detect_platform
    assert_success
    refute_output "ios-ashell"
}

@test "detect_platform: supports explicit override" {
    export DREAM_PLATFORM_OVERRIDE="android-termux"

    run detect_platform
    assert_success
    assert_output "android-termux"
}

@test "resolve_installer_target: routes Termux to the mobile installer" {
    export DREAM_PLATFORM_OVERRIDE="android-termux"

    run resolve_installer_target
    assert_success
    assert_output --partial "/installers/mobile/install-mobile.sh"
}

@test "resolve_installer_target: routes a-Shell to the mobile installer" {
    export DREAM_PLATFORM_OVERRIDE="ios-ashell"

    run resolve_installer_target
    assert_success
    assert_output --partial "/installers/mobile/install-mobile.sh"
}

@test "resolve_installer_target: keeps desktop Linux on install-core" {
    export DREAM_PLATFORM_OVERRIDE="linux"

    run resolve_installer_target
    assert_success
    assert_output --partial "/install-core.sh"
}

@test "detect_platform: treats non-gnu Linux OSTYPE values as Linux" {
    unset DREAM_PLATFORM_OVERRIDE
    OSTYPE="linux"

    run detect_platform
    assert_success
    assert_output "linux"
}
