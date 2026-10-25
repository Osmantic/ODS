#!/usr/bin/env bash
# Run the actual phase; a required model bind cannot reach Continue anyway.
# Helpers and variables below are used by the sourced production phase.
# shellcheck disable=SC2034,SC2329
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/source/lib" "$tmp/install"
cp "$ROOT/lib/safe-env.sh" "$tmp/source/lib/"

run_case() (
    set -euo pipefail
    local windows='' linux='' explicit='' retained='' own='' mode=local native='' external='' interactive=false sudo_docker=false
    local setting
    for setting in "$@"; do local "$setting"; done
    SCRIPT_DIR="$tmp/source" INSTALL_DIR="$tmp/install"
    LOG_FILE="$tmp/log" PREFLIGHT_REPORT_FILE="$tmp/preflight.json"
    TIER=1 RAM_GB=64 DISK_AVAIL=200 GPU_BACKEND=cpu GPU_VRAM=0 GPU_NAME=CPU GPU_COUNT=0
    INTERACTIVE="$interactive" DRY_RUN=false
    ENABLE_OPEN_WEBUI=false ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_COMFYUI=false
    ODS_MODE="$mode" NATIVE_LLM_BASE_URL="$native" EXTERNAL_LLM_URL="$external"
    unset OLLAMA_PORT
    [[ -z "$explicit" ]] || OLLAMA_PORT="$explicit"
    printf 'OLLAMA_PORT=%s\n' "$retained" >"$INSTALL_DIR/.env"
    declare -A SERVICE_PORTS=([llama-server]=8080 [whisper]=9000)
    tier_rank() { echo 1; }; ods_progress() { :; }; chapter() { :; }; ai() { :; }
    ai_ok() { :; }; ai_bad() { :; }; ai_warn() { :; }; log() { :; }
    warn() { printf '%s\n' "$*"; }
    pgrep() { return 1; }
    lsof() {
        [[ -n "$linux" && " $* " == *" $linux "* ]] || return 1
        [[ "$1" != -t ]] || echo 987654
    }
    ss() { :; }; netstat() { :; }
    # lsof receives '-i :PORT ...'; only an exact test listener matches.
    ods_windows_host_port_in_use() { [[ " $windows " == *" $1 "* ]]; }
    sudo() { [[ "$1" == -n ]]; shift; "$@"; }
    docker() {
        case "$1 ${2:-}" in
            'container inspect')
                [[ "${*: -1}" == ods-llama-server && -n "$own" ]] || return 1
                printf '%s\n' "${own//@install/$INSTALL_DIR}" ;;
            'ps --filter') echo foreign-id ;;
            'inspect --format') echo "$INSTALL_DIR|ods" ;;
            *) echo "unexpected Docker mutation: $*" >&2; return 88 ;;
        esac
    }
    [[ "$sudo_docker" != true ]] || DOCKER_CMD='sudo docker'
    source "$ROOT/installers/lib/external-services.sh"
    source "$ROOT/installers/phases/04-requirements.sh"
    printf 'ADMITTED %s %s\n' "${OLLAMA_PORT:-unset}" "${SERVICE_PORTS[llama-server]}"
)

expect() {
    local label="$1" expected="$2" status
    shift 2
    set +e
    run_case "$@" >"$tmp/output" 2>&1
    status=$?
    set -e
    if [[ "$expected" == blocked ]]; then
        # A missing tool, unexpected Docker command or unrelated phase error
        # must not masquerade as a successful collision/validation refusal.
        if [[ "$status" -ne 1 ]] || grep -q ADMITTED "$tmp/output" \
            || ! grep -qE '^(Required local model port [0-9]+ is in use|OLLAMA_PORT must be a port from 1 to 65535)' "$tmp/output"; then
            cat "$tmp/output"; echo "FAIL $label"; exit 1
        fi
    else
        if [[ "$status" -ne 0 ]] || ! grep -qF "$expected" "$tmp/output"; then
            cat "$tmp/output"; echo "FAIL $label"; exit 1
        fi
    fi
    printf 'PASS: %s\n' "$label"
}

expect 'Windows Ollama port stops noninteractive install' blocked windows=11434
grep -q 'quit it from the system tray' "$tmp/output"
expect 'Continue anyway cannot waive required inference port' blocked windows=11434 interactive=true
expect 'Native Linux listener also stops install' blocked linux=:11434
expect 'Free default is admitted and selected consistently' 'ADMITTED 11434 11434'
expect 'Explicit free port avoids occupied default' 'ADMITTED 11435 11435' windows=11434 explicit=11435
expect 'Retained quoted override is checked' blocked windows=12434 "retained='12434'"
expect 'Explicit port wins over retained port' 'ADMITTED 13434 13434' windows=12434 retained=12434 explicit=13434
expect 'Busy explicit port is never rewritten' blocked windows=11435 explicit=11435
for invalid in 0 65536 -1 01234 abc '1;id'; do
    expect "Reject invalid port $invalid" blocked "explicit=$invalid"
done
expect 'Cloud mode needs no local inference binding' 'ADMITTED unset 8080' windows=11434 mode=cloud
expect 'External API needs no local inference binding' 'ADMITTED unset 8080' windows=11434 external=http://provider:8080
expect 'Native runtime owns its own binding' 'ADMITTED unset 8080' windows=11434 native=http://host:1234
expect 'Own running inference container permits rerun' 'ADMITTED 11434 11434' windows=11434 own='true|ods|llama-server|@install|11434 '
expect 'sudo Docker rerun can inspect owned container' 'ADMITTED 11434 11434' windows=11434 own='true|ods|llama-server|@install|11434 ' sudo_docker=true
expect 'Foreign same-project service cannot bypass port admission' blocked windows=11434 own='true|ods|other|@install|11434 '
expect 'Foreign installation cannot bypass port admission' blocked windows=11434 own='true|ods|llama-server|/other|11434 '
expect 'Stopped container is not a listener owner' blocked windows=11434 own='false|ods|llama-server|@install|11434 '
expect 'Container-side port is not a host binding' blocked windows=11434 own='true|ods|llama-server|@install|8080 '
