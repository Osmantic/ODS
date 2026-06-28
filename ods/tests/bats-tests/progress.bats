#!/usr/bin/env bats
# ============================================================================
# BATS tests for lib/progress.sh
# ============================================================================
# Tests: format_duration(), estimate_download_time(), draw_progress_bar()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub color variables as empty strings (progress.sh uses CYAN, NC,
    # GREEN, BOLD, BLUE in printf calls)
    export CYAN=""
    export NC=""
    export GREEN=""
    export BOLD=""
    export BLUE=""

    # progress.sh uses `declare -A` (Bash 4+ associative arrays) at file
    # level, which fails on macOS's Bash 3.x.  Extract only the pure
    # functions we test by sourcing through sed, skipping the declare -A
    # block and everything after it that depends on associative arrays.
    eval "$(sed -n '1,/^declare -A/{ /^declare -A/d; p; }' \
        "$BATS_TEST_DIRNAME/../../lib/progress.sh")"
}

# ── format_duration ─────────────────────────────────────────────────────────

@test "format_duration: seconds under 60" {
    run format_duration 0
    assert_output "0s"

    run format_duration 1
    assert_output "1s"

    run format_duration 30
    assert_output "30s"

    run format_duration 59
    assert_output "59s"
}

@test "format_duration: exact minutes" {
    run format_duration 60
    assert_output "1m"

    run format_duration 120
    assert_output "2m"

    run format_duration 300
    assert_output "5m"
}

@test "format_duration: minutes with remainder are truncated" {
    # 90 seconds = 1m (integer division, remainder dropped)
    run format_duration 90
    assert_output "1m"

    run format_duration 150
    assert_output "2m"
}

@test "format_duration: hours and minutes" {
    run format_duration 3600
    assert_output "1h 0m"

    run format_duration 3720
    assert_output "1h 2m"

    run format_duration 7200
    assert_output "2h 0m"

    run format_duration 7380
    assert_output "2h 3m"
}

# ── estimate_download_time ──────────────────────────────────────────────────

@test "estimate_download_time: 1GB at default 50Mbps" {
    # 1 GB = 1024 MB, 1024 * 8 / 50 = 163 seconds = 2m
    run estimate_download_time 1
    assert_output "2m"
}

@test "estimate_download_time: 5GB at 100Mbps" {
    # 5 GB = 5120 MB, 5120 * 8 / 100 = 409 seconds = 6m
    run estimate_download_time 5 100
    assert_output "6m"
}

@test "estimate_download_time: large model at slow speed" {
    # 50 GB at 10 Mbps: 50 * 1024 * 8 / 10 = 40960 seconds = 11h 22m
    run estimate_download_time 50 10
    assert_output "11h 22m"
}

# ── draw_progress_bar ───────────────────────────────────────────────────────

@test "draw_progress_bar: 0% shows all empty blocks" {
    run draw_progress_bar 0 100 10 "Test"
    # Should contain 0% and the label
    assert_output --partial "0%"
    assert_output --partial "Test"
}

@test "draw_progress_bar: 100% shows all filled blocks" {
    run draw_progress_bar 100 100 10 "Test"
    assert_output --partial "100%"
}

@test "draw_progress_bar: 50% shows correct percentage" {
    run draw_progress_bar 50 100 10 "Test"
    assert_output --partial "50%"
}

@test "draw_progress_bar: handles zero total without division by zero" {
    # total=0 should be guarded to total=1
    run draw_progress_bar 0 0 10 "Test"
    assert_success
    assert_output --partial "0%"
}

# ── complete_progress_bar ───────────────────────────────────────────────────

@test "complete_progress_bar: shows 100%" {
    run complete_progress_bar "Download" 10
    assert_output --partial "100%"
    assert_output --partial "Download"
}
