#!/usr/bin/env bats
# ============================================================================
# BATS tests for _compose_run_with_summary in ods-cli.
# ============================================================================
# Guards against re-breakage of PR #406 (compose wrapper surfaces a compact
# summary on success, an error banner + grep'd keywords + log path on
# failure, and propagates the compose exit code so `ods restart/stop/start`
# aborts under `set -e`).
#
# Test strategy:
#   - Extract _compose_run_with_summary + its helper (log/success/warn/
#     log_error) text from ods-cli.
#   - PATH-inject a `docker` stub that can be told to exit 0 / exit 1 with
#     error-keyword output / exit 1 with no-keyword output.
#   - Run the wrapper and assert on printed output + return code.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    export TMPDIR_TEST="$BATS_TEST_TMPDIR"
    export TMPDIR="$TMPDIR_TEST"
    mkdir -p "$TMPDIR_TEST/bin"

    # Color vars used by log/success/warn/log_error — stub to empty so
    # assert_output matches plain strings without escape sequences.
    export RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""

    # Minimal logger helpers that match ods-cli's line 45–50 definitions.
    # We redefine rather than extract because (a) they're trivial and (b)
    # ods-cli's real definitions pull in color vars we've already stubbed.
    log()       { echo "[ods] $1"; }
    success()   { echo "✓ $1"; }
    warn()      { echo "⚠ $1"; }
    log_error() { echo "✗ $1" >&2; }
    _check_docker_access() { :; }
    export -f log success warn log_error _check_docker_access

    # Extract _compose_run_with_summary from ods-cli.
    local _cli="$BATS_TEST_DIRNAME/../../ods-cli"
    eval "$(awk '/^_compose_run_with_summary\(\) \{/,/^\}$/' "$_cli")"

    # docker stub — behavior driven by DOCKER_STUB_MODE env var:
    #   success     → exit 0 with benign stdout
    #   fail-keyword→ exit 1, stdout contains 'error', 'failed', 'unhealthy', 'dependency'
    #   fail-nomatch→ exit 1, stdout has no error keywords
    cat > "$TMPDIR_TEST/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "DOCKER_ARGS: $*" >> "$DOCKER_CALL_LOG"
case "${DOCKER_STUB_MODE:-success}" in
    success)
        echo "Network ods-net created"
        echo "Container ods-foo started"
        exit 0
        ;;
    fail-keyword)
        echo "Container ods-foo starting"
        echo "Error response from daemon: service ods-foo failed to start"
        echo "unhealthy container ods-bar"
        echo "dependency ods-baz not ready"
        exit 1
        ;;
    fail-nomatch)
        echo "Container ods-foo starting"
        echo "network busy (try again later)"
        exit 1
        ;;
    fail-port-once)
        state_file="$TMPDIR_TEST/port-retry-count"
        count=0
        [[ -f "$state_file" ]] && count=$(cat "$state_file")
        count=$((count + 1))
        echo "$count" > "$state_file"
        if [[ "$count" -eq 1 ]]; then
            echo "Error response from daemon: failed to set up container networking: Bind for 127.0.0.1:11434 failed: port is already allocated"
            exit 1
        fi
        echo "Container ods-foo started"
        exit 0
        ;;
    fail-startup-once)
        state_file="$TMPDIR_TEST/startup-retry-count"
        count=0
        [[ -f "$state_file" ]] && count=$(cat "$state_file")
        count=$((count + 1))
        echo "$count" > "$state_file"
        if [[ "$count" -eq 1 ]]; then
            echo "dependency failed to start: container ods-llama-server exited (137)"
            exit 1
        fi
        echo "Container ods-llama-server started"
        exit 0
        ;;
    fail-startup-twice)
        state_file="$TMPDIR_TEST/startup-retry-count"
        count=0
        [[ -f "$state_file" ]] && count=$(cat "$state_file")
        count=$((count + 1))
        echo "$count" > "$state_file"
        if [[ "$count" -le 2 ]]; then
            echo "dependency failed to start: Error response from daemon: No such container: stale-compose-id"
            exit 1
        fi
        echo "Container ods-llama-server started"
        exit 0
        ;;
    fail-docker-sock)
        echo "unable to get image 'ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.4': permission denied while trying to connect to the docker API at unix:///var/run/docker.sock"
        exit 1
        ;;
    *)
        echo "unknown DOCKER_STUB_MODE: $DOCKER_STUB_MODE" >&2
        exit 127
        ;;
esac
STUB
    chmod +x "$TMPDIR_TEST/bin/docker"
    export PATH="$TMPDIR_TEST/bin:$PATH"
    export DOCKER_CALL_LOG="$TMPDIR_TEST/docker.log"
    export ODS_COMPOSE_STARTUP_RETRY_DELAY=0
    : > "$DOCKER_CALL_LOG"
}

teardown() {
    rm -rf "$TMPDIR_TEST/bin" "$TMPDIR_TEST/docker.log"
}

# ── success path ────────────────────────────────────────────────────────────

@test "wrapper: success prints compact '<verb> — done' banner and returns 0" {
    export DOCKER_STUB_MODE=success
    run _compose_run_with_summary "Restarting all services" up -d
    assert_success
    assert_output --partial "Restarting all services..."
    assert_output --partial "Restarting all services — done"
}

@test "wrapper: success removes the compose log tmpfile" {
    export DOCKER_STUB_MODE=success
    # Track how many mktemp-produced files exist before & after. We rely on
    # $TMPDIR being our per-test tempdir; the wrapper uses `mktemp` with no args.
    local before=$(find "$TMPDIR_TEST" -maxdepth 1 -name 'tmp.*' -type f 2>/dev/null | wc -l)
    run _compose_run_with_summary "Starting all services" up -d
    assert_success
    local after=$(find "$TMPDIR_TEST" -maxdepth 1 -name 'tmp.*' -type f 2>/dev/null | wc -l)
    # Zero net new temp files (create + rm).
    [ "$after" -le "$before" ]
}

@test "wrapper: success does NOT print error banner or log path" {
    export DOCKER_STUB_MODE=success
    run _compose_run_with_summary "Starting all services" up -d
    assert_success
    refute_output --partial "Full compose output:"
    refute_output --partial "failed:"
}

# ── failure path, matching keywords (PR #406) ───────────────────────────────

@test "wrapper: failure prints error banner, matched lines, and log path" {
    export DOCKER_STUB_MODE=fail-keyword
    run _compose_run_with_summary "Restarting service x" up -d x
    assert_failure
    assert_output --partial "Restarting service x failed:"
    # At least one error-keyword line should be surfaced (indented two spaces).
    assert_output --partial "Error response from daemon"
    assert_output --partial "Full compose output:"
}

@test "wrapper: failure propagates the compose exit code (1)" {
    export DOCKER_STUB_MODE=fail-keyword
    run _compose_run_with_summary "Restarting" up -d
    # docker stub exits 1; wrapper must return 1.
    [ "$status" -eq 1 ]
}

@test "wrapper: failure preserves the compose log file (not auto-removed)" {
    export DOCKER_STUB_MODE=fail-keyword
    run _compose_run_with_summary "Restarting" up -d
    assert_failure
    # Extract the "Full compose output: /tmp/tmp.XXXXXX" path from output.
    local log_line
    log_line=$(printf '%s\n' "$output" | grep -E 'Full compose output:' | head -1)
    [ -n "$log_line" ]
    local log_path="${log_line##*: }"
    [ -f "$log_path" ]
    rm -f "$log_path"
}

# ── failure path, zero keyword matches (nounset/pipefail-hardening) ─────────

# These two tests must execute the wrapper under `set -euo pipefail` because
# the `|| warn "(no error keywords matched...)"` branch only fires when
# grep's exit-1 propagates through the pipeline — and only pipefail makes
# grep the pipeline's final exit code. ods-cli line 6 IS `set -euo pipefail`
# in production; bats' setup() doesn't inherit that, so we set it explicitly
# via the subshell.

@test "wrapper: failure with no keyword match fires the warn fallback (under pipefail)" {
    export DOCKER_STUB_MODE=fail-nomatch
    # Re-extract the wrapper inside a pipefail-enabled subshell.
    run bash -c '
        set -euo pipefail
        RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""
        log()       { echo "[ods] $1"; }
        success()   { echo "✓ $1"; }
        warn()      { echo "⚠ $1"; }
        log_error() { echo "✗ $1" >&2; }
        _check_docker_access() { :; }
        eval "$(awk "/^_compose_run_with_summary\(\) \{/,/^\}$/" "'"$BATS_TEST_DIRNAME/../../ods-cli"'")"
        _compose_run_with_summary "Stopping service y" down y
    '
    assert_failure
    assert_output --partial "Stopping service y failed:"
    assert_output --partial "(no error keywords matched in compose log)"
    assert_output --partial "Full compose output:"
}

@test "wrapper: failure with no keyword match still propagates exit code" {
    export DOCKER_STUB_MODE=fail-nomatch
    run bash -c '
        set -euo pipefail
        RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""
        log()       { echo "[ods] $1"; }
        success()   { echo "✓ $1"; }
        warn()      { echo "⚠ $1"; }
        log_error() { echo "✗ $1" >&2; }
        _check_docker_access() { :; }
        eval "$(awk "/^_compose_run_with_summary\(\) \{/,/^\}$/" "'"$BATS_TEST_DIRNAME/../../ods-cli"'")"
        _compose_run_with_summary "Stopping" down
    '
    [ "$status" -eq 1 ]
}

@test "wrapper: docker socket permission denied is surfaced with a fix hint" {
    export DOCKER_STUB_MODE=fail-docker-sock
    run _compose_run_with_summary "Restarting all services" up -d
    assert_failure
    assert_output --partial "permission denied while trying to connect to the docker API"
    assert_output --partial "Docker socket permission denied"
    assert_output --partial "sudo usermod -aG docker"
    assert_output --partial "newgrp docker"
    assert_output --partial "Full compose output:"
}

# ── docker compose args passthrough ─────────────────────────────────────────

@test "wrapper: compose up retries once on transient port allocation race" {
    export DOCKER_STUB_MODE=fail-port-once
    export ODS_COMPOSE_PORT_RETRY_DELAY=0
    run _compose_run_with_summary "Restarting all services" up -d
    assert_success
    assert_output --partial "Docker reported a port allocation race"
    assert_output --partial "Restarting all services"
    assert_output --partial "done"
    run grep -c '^DOCKER_ARGS:' "$DOCKER_CALL_LOG"
    assert_output "2"
}

@test "wrapper: non-up command does not retry on port allocation failure" {
    export DOCKER_STUB_MODE=fail-port-once
    export ODS_COMPOSE_PORT_RETRY_DELAY=0
    run _compose_run_with_summary "Stopping all services" down
    assert_failure
    run grep -c '^DOCKER_ARGS:' "$DOCKER_CALL_LOG"
    assert_output "1"
}

@test "wrapper: compose up retries once on transient dependency startup failure" {
    export DOCKER_STUB_MODE=fail-startup-once
    run _compose_run_with_summary "Restarting all services" up -d
    assert_success
    assert_output --partial "Docker reported a transient service startup failure"
    assert_output --partial "Restarting all services"
    assert_output --partial "done"
    run grep -c '^DOCKER_ARGS:' "$DOCKER_CALL_LOG"
    assert_output "2"
}

@test "wrapper: compose up retries repeated transient dependency startup failures" {
    export DOCKER_STUB_MODE=fail-startup-twice
    export ODS_COMPOSE_STARTUP_RETRY_ATTEMPTS=3
    run _compose_run_with_summary "Restarting all services" up -d
    assert_success
    assert_output --partial "retrying (1/2)"
    assert_output --partial "retrying (2/2)"
    assert_output --partial "Restarting all services"
    assert_output --partial "done"
    run grep -c '^DOCKER_ARGS:' "$DOCKER_CALL_LOG"
    assert_output "3"
}

@test "wrapper: all args after verb are passed to docker compose" {
    export DOCKER_STUB_MODE=success
    run _compose_run_with_summary "Up" -f extra.yml up -d my-service
    assert_success
    # Stub records its argv to DOCKER_CALL_LOG; first arg must be `compose`
    # because the wrapper calls `docker compose <args>`.
    run cat "$DOCKER_CALL_LOG"
    assert_output --partial "DOCKER_ARGS: compose --progress quiet -f extra.yml up -d my-service"
}

# ── propagation into callers (cmd_restart/cmd_stop/cmd_start) ───────────────

@test "wrapper: caller returns wrapper's non-zero exit (smoke)" {
    export DOCKER_STUB_MODE=fail-keyword
    # Simulate `cmd_restart`: wrapper is the last statement, so its exit
    # code becomes the function's exit code.
    _simulated_caller() {
        _compose_run_with_summary "Restarting all services" up -d
    }
    run _simulated_caller
    [ "$status" -eq 1 ]
}

@test "wrapper: caller returns 0 on success (smoke)" {
    export DOCKER_STUB_MODE=success
    _simulated_caller() {
        _compose_run_with_summary "Restarting all services" up -d
    }
    run _simulated_caller
    [ "$status" -eq 0 ]
}
