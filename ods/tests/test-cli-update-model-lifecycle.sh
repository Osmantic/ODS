#!/usr/bin/env bash
# `ods update` restarts the host agent and recreates the stack. It must not
# start while the agent's model lifecycle is busy (a model switch or a Portal
# access check) or an interrupted switch awaits recovery: either would be left
# half-applied. Exercises the real cmd_update with a stubbed agent; nothing
# past the pre-update snapshot runs.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_CLI_UNDER_TEST:-$ROOT_DIR/ods-cli}"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

extract() {
    awk -v name="$1" '$0 ~ "^" name "\\(\\) \\{" {copy=1} copy{print} copy && /^}$/{exit}' "$TARGET"
}

# $1 = /v1/model/status body ("" = agent unreachable), $2 = /v1/model/recovery body.
# COMMAND=restart (with RESTART_ARGS) runs cmd_restart instead of cmd_update.
run_update() {
    local status="$1" recovery="$2" dir="$FIXTURE/run"
    rm -rf "$dir"; mkdir -p "$dir"
    printf 'ODS_VERSION=1.0.0\nODS_AGENT_KEY=test-only\n' > "$dir/.env"
    printf '#!/bin/sh\nexit 0\n' > "$dir/ods-update.sh"
    chmod +x "$dir/ods-update.sh"
    (
        set -euo pipefail
        INSTALL_DIR="$dir"
        RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""
        check_install() { :; }
        load_env() { :; }
        log() { printf '%s\n' "$*"; }
        log_error() { printf 'ERROR: %s\n' "$*"; }
        warn() { printf 'WARN: %s\n' "$*"; }
        error() { log_error "$*"; exit 1; }
        _check_version_compat() { return 0; }
        _env_get_raw() { sed -n "s/^$1=//p" "$INSTALL_DIR/.env"; }
        _agent_probe_host() { printf '127.0.0.1'; }
        curl() {
            cat >/dev/null
            [[ -n "$STATUS_BODY" ]] || return 7
            case "${!#}" in
                */v1/model/status) printf '%s' "$STATUS_BODY" ;;
                */v1/model/recovery) [[ -n "$RECOVERY_BODY" ]] || return 28; printf '%s' "$RECOVERY_BODY" ;;
                *) return 22 ;;
            esac
        }
        # The pre-update snapshot is the first change; stop there.
        bash() { echo "UPDATE-PROCEEDED"; exit 99; }
        STATUS_BODY="$status" RECOVERY_BODY="$recovery"
        eval "$(extract _agent_model_lifecycle_busy)"
        eval "$(extract _ods_cli_refuse_under_model_lifecycle)"
        eval "$(extract cmd_update)"
        eval "$(extract cmd_restart)"
        if [[ "${COMMAND:-update}" == restart ]]; then
            ensure_llama_cpu_budget() { :; }
            resolve_service() { printf '%s' "$1"; }
            _ods_cli_macos_native_llama() { return 1; }
            get_compose_flags() { printf -- '-f docker-compose.base.yml'; }
            _ods_cli_network_access_enabled() { return 1; }
            # Repairing bind-mount ownership is the first change after the
            # guard; stop there.
            _ods_cli_repair_rootless_ownership() { echo "RESTART-PROCEEDED"; exit 99; }
            cmd_restart "${RESTART_ARGS[@]}" </dev/null
        else
            cmd_update </dev/null
        fi
    )
}

expect() {
    local want="$1" label="$2" pattern="$3"; shift 3
    local rc=0 output
    output=$(run_update "$@" 2>&1) || rc=$?
    if [[ "$rc" != "$want" || "$output" != *"$pattern"* ]]; then
        echo "[FAIL] $label: expected exit $want with '$pattern', got exit $rc" >&2
        printf '%s\n' "$output" >&2
        exit 1
    fi
    echo "[PASS] $label"
}

idle='{"pending":false,"phase":"idle"}'
expect 1 "a model switch in progress blocks the update" "model_activation for qwen2.5-0.5b-instruct-q4 is in progress" \
    '{"status":"idle","lifecycleActive":true,"activeOperation":"model_activation","activeTarget":"qwen2.5-0.5b-instruct-q4"}' "$idle"
expect 1 "a Portal access check in progress blocks the update" "pixel_startup_reproof is in progress" \
    '{"status":"idle","lifecycleActive":true,"activeOperation":"pixel_startup_reproof","activeTarget":null}' "$idle"
expect 1 "a running switch is not also reported as interrupted" "model_activation for qwen2.5-0.5b-instruct-q4 is in progress. Let it finish" \
    '{"status":"idle","lifecycleActive":true,"activeOperation":"model_activation","activeTarget":"qwen2.5-0.5b-instruct-q4"}' '{"pending":true,"phase":"held","transactionId":"abc"}'
expect 1 "an active switch refuses even when the recovery request times out" "model_activation for qwen2.5-0.5b-instruct-q4 is in progress" \
    '{"status":"idle","lifecycleActive":true,"activeOperation":"model_activation","activeTarget":"qwen2.5-0.5b-instruct-q4"}' ""
expect 1 "an interrupted switch awaiting recovery blocks the update" "waiting for recovery (ods model recover)" \
    '{"status":"idle"}' '{"pending":true,"phase":"held","transactionId":"abc"}'
expect 99 "an idle lifecycle lets the update continue" "UPDATE-PROCEEDED" '{"status":"idle"}' "$idle"
expect 99 "an unreachable agent warns and continues" "Could not ask the host agent" "" ""

busy='{"status":"idle","lifecycleActive":true,"activeOperation":"model_activation","activeTarget":"qwen3.5-2b-q4"}'
COMMAND=restart RESTART_ARGS=()
expect 1 "a full restart under a model switch is refused" "Restart not started: model_activation for qwen3.5-2b-q4 is in progress" "$busy" "$idle"
RESTART_ARGS=(llama-server)
expect 1 "a llama-server restart under a model switch is refused" "Restart not started" "$busy" "$idle"
RESTART_ARGS=(searxng)
expect 99 "restarting an unrelated service is not blocked" "RESTART-PROCEEDED" "$busy" "$idle"
RESTART_ARGS=()
expect 99 "a full restart with an idle lifecycle continues" "RESTART-PROCEEDED" '{"status":"idle"}' "$idle"
