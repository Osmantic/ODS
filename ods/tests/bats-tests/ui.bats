#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/ui.sh
# ============================================================================
# Tests: ai(), ai_ok(), ai_warn(), ai_bad(), signal(), chapter(),
#        show_phase(), show_hardware_summary(), show_tier_recommendation(),
#        download_part_bytes(), format_download_progress(),
#        report_active_download_preserved(), cancel_active_download(),
#        spin_task() labels
#
# Note: type_line, type_line_dramatic, show_stranger_boot, pull_with_progress,
#       show_install_menu, show_success_card have side effects (sleep, read,
#       curl, docker) and are NOT tested here. spin_task is covered only for
#       its label rendering, against a short-lived background task.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Disable interactive mode (prevents sleep/typing effects)
    export INTERACTIVE="false"
    export DRY_RUN="true"

    # Define color vars as empty strings for clean test output
    export GRN=""
    export BGRN=""
    export DGRN=""
    export AMB=""
    export WHT=""
    export RED=""
    export DIM=""
    export NC=""
    export CURSOR=""
    export VERSION="2.4.0"

    export LOG_FILE="$BATS_TEST_TMPDIR/ui-test.log"
    touch "$LOG_FILE"

    # Stub bootline() and install_elapsed() to avoid sourcing logging.sh
    bootline() { echo "────────"; }
    export -f bootline

    install_elapsed() { echo "0m 00s"; }
    export -f install_elapsed

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/ui.sh"
}

# ── ai ──────────────────────────────────────────────────────────────────────

@test "ai: outputs the ▸ marker and message" {
    run ai "test message"
    assert_success
    assert_output --partial "▸"
    assert_output --partial "test message"
}

@test "ai: appends to LOG_FILE" {
    ai "logged message"
    run cat "$LOG_FILE"
    assert_output --partial "▸"
    assert_output --partial "logged message"
}

# ── ai_ok ───────────────────────────────────────────────────────────────────

@test "ai_ok: outputs the ✓ marker and message" {
    run ai_ok "success message"
    assert_success
    assert_output --partial "✓"
    assert_output --partial "success message"
}

@test "ai_ok: appends to LOG_FILE" {
    ai_ok "ok logged"
    run cat "$LOG_FILE"
    assert_output --partial "✓"
    assert_output --partial "ok logged"
}

# ── ai_warn ─────────────────────────────────────────────────────────────────

@test "ai_warn: outputs the ⚠ marker and message" {
    run ai_warn "warning message"
    assert_success
    assert_output --partial "⚠"
    assert_output --partial "warning message"
}

@test "ai_warn: appends to LOG_FILE" {
    ai_warn "warn logged"
    run cat "$LOG_FILE"
    assert_output --partial "⚠"
    assert_output --partial "warn logged"
}

# ── ai_bad ──────────────────────────────────────────────────────────────────

@test "ai_bad: outputs the ✗ marker and message" {
    run ai_bad "error message"
    assert_success
    assert_output --partial "✗"
    assert_output --partial "error message"
}

@test "ai_bad: appends to LOG_FILE" {
    ai_bad "bad logged"
    run cat "$LOG_FILE"
    assert_output --partial "✗"
    assert_output --partial "bad logged"
}

# ── signal ──────────────────────────────────────────────────────────────────

@test "signal: outputs the flourish pattern and message" {
    run signal "signal message"
    assert_success
    assert_output --partial "░▒▓█▓▒░"
    assert_output --partial "signal message"
}

@test "signal: appends to LOG_FILE" {
    signal "signal logged"
    run cat "$LOG_FILE"
    assert_output --partial "░▒▓█▓▒░"
    assert_output --partial "signal logged"
}

# ── chapter ─────────────────────────────────────────────────────────────────

@test "chapter: outputs section title" {
    run chapter "MY SECTION"
    assert_success
    assert_output --partial "MY SECTION"
}

# ── show_phase ──────────────────────────────────────────────────────────────

@test "show_phase: outputs phase number, total, and name" {
    run show_phase 3 13 "FEATURES" "~30s"
    assert_success
    assert_output --partial "PHASE 3/13"
    assert_output --partial "FEATURES"
}

@test "show_phase: includes estimate when provided" {
    run show_phase 1 13 "PREFLIGHT" "~10s"
    assert_success
    assert_output --partial "~10s"
}

@test "show_phase: omits estimate when empty" {
    run show_phase 5 13 "DOCKER" ""
    assert_success
    assert_output --partial "PHASE 5/13"
    assert_output --partial "DOCKER"
}

# ── show_hardware_summary ──────────────────────────────────────────────────

@test "show_hardware_summary: outputs GPU name" {
    run show_hardware_summary "NVIDIA RTX 4090" "24564" "AMD Ryzen 9" "64" "500"
    assert_success
    assert_output --partial "NVIDIA RTX 4090"
}

@test "show_hardware_summary: outputs VRAM value" {
    run show_hardware_summary "NVIDIA RTX 4090" "24564" "AMD Ryzen 9" "64" "500"
    assert_output --partial "24564"
    assert_output --partial "GB"
}

@test "show_hardware_summary: outputs CPU info" {
    run show_hardware_summary "NVIDIA RTX 4090" "24564" "AMD Ryzen 9" "64" "500"
    assert_output --partial "AMD Ryzen 9"
}

@test "show_hardware_summary: outputs RAM value" {
    run show_hardware_summary "NVIDIA RTX 4090" "24564" "AMD Ryzen 9" "64" "500"
    assert_output --partial "64"
}

@test "show_hardware_summary: outputs disk value" {
    run show_hardware_summary "NVIDIA RTX 4090" "24564" "AMD Ryzen 9" "64" "500"
    assert_output --partial "500"
    assert_output --partial "available"
}

@test "show_hardware_summary: handles missing GPU gracefully" {
    run show_hardware_summary "" "" "Intel Core i7" "32" "250"
    assert_success
    assert_output --partial "Not detected"
}

@test "show_hardware_summary: includes HARDWARE SCAN RESULTS header" {
    run show_hardware_summary "RTX 3090" "24576" "Ryzen 7" "32" "100"
    assert_output --partial "HARDWARE SCAN RESULTS"
}

# ── show_tier_recommendation ───────────────────────────────────────────────

@test "show_tier_recommendation: outputs tier number" {
    run show_tier_recommendation 3 "qwen3-30b-a3b" "30" "3"
    assert_success
    assert_output --partial "TIER 3"
}

@test "show_tier_recommendation: outputs model name" {
    run show_tier_recommendation 3 "qwen3-30b-a3b" "30" "3"
    assert_output --partial "qwen3-30b-a3b"
}

@test "show_tier_recommendation: outputs speed" {
    run show_tier_recommendation 3 "qwen3-30b-a3b" "30" "3"
    assert_output --partial "30"
    assert_output --partial "tokens/second"
}

@test "show_tier_recommendation: outputs concurrent users" {
    run show_tier_recommendation 3 "qwen3-30b-a3b" "30" "3"
    assert_output --partial "3"
    assert_output --partial "concurrent"
}

@test "show_tier_recommendation: includes CLASSIFICATION header" {
    run show_tier_recommendation 4 "qwen3-30b-a3b" "40" "5"
    assert_output --partial "CLASSIFICATION"
}

# ── LORE_MESSAGES ───────────────────────────────────────────────────────────

@test "check_service: exits early when managed container has exited" {
    export DRY_RUN="false"
    export DOCKER_CMD="docker"
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/timeout" <<'MOCK'
#!/bin/bash
exit 7
MOCK
    cat > "$BATS_TEST_TMPDIR/bin/docker" <<'MOCK'
#!/bin/bash
if [[ "$1" == "inspect" ]]; then
    echo "exited"
    exit 0
fi
exit 1
MOCK
    chmod +x "$BATS_TEST_TMPDIR/bin/timeout" "$BATS_TEST_TMPDIR/bin/docker"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

    run check_service "llama-server" "http://127.0.0.1:8080/health" 3 1 "ods-llama-server"
    assert_failure
    assert_output --partial "container exited"
    assert_output --partial "not retrying"
}

@test "check_service: supports sudo docker command for container state" {
    export DRY_RUN="false"
    export DOCKER_CMD="sudo docker"
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/timeout" <<'MOCK'
#!/bin/bash
exit 7
MOCK
    cat > "$BATS_TEST_TMPDIR/bin/sudo" <<'MOCK'
#!/bin/bash
if [[ "$1" == "docker" && "$2" == "inspect" ]]; then
    echo "dead"
    exit 0
fi
exit 1
MOCK
    chmod +x "$BATS_TEST_TMPDIR/bin/timeout" "$BATS_TEST_TMPDIR/bin/sudo"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

    run check_service "llama-server" "http://127.0.0.1:8080/health" 3 1 "ods-llama-server"
    assert_failure
    assert_output --partial "container dead"
    assert_output --partial "not retrying"
}

@test "LORE_MESSAGES: array is non-empty" {
    [[ ${#LORE_MESSAGES[@]} -gt 0 ]]
}

@test "LORE_MESSAGES: contains at least 10 messages" {
    [[ ${#LORE_MESSAGES[@]} -ge 10 ]]
}

# ── DIVIDER ─────────────────────────────────────────────────────────────────

@test "DIVIDER: is set and non-empty" {
    [[ -n "$DIVIDER" ]]
}

# ── download progress ───────────────────────────────────────────────────────

@test "format_download_progress: reports downloaded MB when the size is unknown" {
    run format_download_progress 416284672 0
    assert_success
    assert_output "397 MB"
}

@test "format_download_progress: adds total and percent when the size is known" {
    run format_download_progress 416284672 1221
    assert_success
    assert_output "397 MB / 1221 MB (32%)"
}

@test "format_download_progress: pinned bootstrap artifact completes at 100 percent" {
    run format_download_progress 1280835840 1221
    assert_success
    assert_output "1221 MB / 1221 MB (100%)"
}

@test "format_download_progress: never renders more than 100% for a stale estimate" {
    run format_download_progress 2097152000 1500
    assert_success
    assert_output --partial "(100%)"
}

@test "format_download_progress: treats missing or non-numeric input as zero" {
    run format_download_progress "" ""
    assert_success
    assert_output "0 MB"

    run format_download_progress "abc" "xyz"
    assert_success
    assert_output "0 MB"
}

@test "download_part_bytes: reads the size of an in-flight download" {
    local part="$BATS_TEST_TMPDIR/model.gguf.part"
    printf '%0.sx' $(seq 1 2048) > "$part"

    run download_part_bytes "$part"
    assert_success
    assert_output "2048"
}

@test "download_part_bytes: reads zero before curl creates the file" {
    run download_part_bytes "$BATS_TEST_TMPDIR/not-created-yet.part"
    assert_success
    assert_output "0"
}

@test "report_active_download_preserved: reports resumable bytes" {
    local part="$BATS_TEST_TMPDIR/resumable.gguf.part"
    head -c 5242880 /dev/zero > "$part"

    run report_active_download_preserved "$part" 10
    assert_success
    assert_output --partial "Partial download preserved: 5 MB / 10 MB (50%)"
    assert_output --partial "Re-run the installer to resume it."
}

@test "report_active_download_preserved: is silent without resumable bytes" {
    run report_active_download_preserved "$BATS_TEST_TMPDIR/missing.gguf.part" 10
    assert_success
    assert_output ""
}

@test "cancel_active_download: stops the owned process before reporting its part" {
    local part="$BATS_TEST_TMPDIR/cancelled.gguf.part"
    local rendered="$BATS_TEST_TMPDIR/cancelled.out"
    head -c 5242880 /dev/zero > "$part"

    sleep 30 &
    local download_pid=$!
    ODS_ACTIVE_DOWNLOAD_PID="$download_pid"
    ODS_ACTIVE_DOWNLOAD_PART="$part"
    ODS_ACTIVE_DOWNLOAD_TOTAL_MB=10

    cancel_active_download > "$rendered"

    ! kill -0 "$download_pid" 2>/dev/null
    [[ -z "$ODS_ACTIVE_DOWNLOAD_PID" ]]
    grep -qF "Partial download preserved: 5 MB / 10 MB (50%)" "$rendered"
}

# spin_task waits on a background pid, so these redirect its output to a file
# instead of using `run` or command substitution: both fork a subshell, which
# cannot wait on a child of the test shell.
@test "spin_task: shows the download counter when a part file is supplied" {
    local part="$BATS_TEST_TMPDIR/spin.gguf.part"
    head -c 5242880 /dev/zero > "$part"

    sleep 2 &
    local task_pid=$!
    local rendered="$BATS_TEST_TMPDIR/spin-progress.out"
    spin_task "$task_pid" "Downloading model.gguf" "$part" 10 > "$rendered"

    grep -qF "Downloading model.gguf" "$rendered"
    grep -qF "5 MB / 10 MB (50%)" "$rendered"
}

@test "spin_task: keeps the plain label when no part file is supplied" {
    sleep 2 &
    local task_pid=$!
    local rendered="$BATS_TEST_TMPDIR/spin-plain.out"
    spin_task "$task_pid" "Working" > "$rendered"

    grep -qF "Working" "$rendered"
    ! grep -qF " MB" "$rendered"
}
