#!/usr/bin/env bash
# Run the real requirements phase on a simulated WSL host whose Windows side
# already listens on port 9000 (as a native app's websocket can), then the
# phase 06 resolver that persists WHISPER_PORT.
#
# A lean install (voice off) must still move the generated Whisper default off
# 9000: Whisper can be added from the Extensions Library later, and Docker
# Desktop then cannot publish 127.0.0.1:9000. A rerun moves a retained 9000,
# which earlier installers wrote as the generated default, but never a port
# the owner chose. Nor does it move the port of the installation's own
# running Whisper, which Docker Desktop publishes through a Windows listener.
# Variables below are consumed by the sourced phases.
# shellcheck disable=SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/source/lib"
cp "$ROOT/lib/safe-env.sh" "$tmp/source/lib/safe-env.sh"
unset WHISPER_PORT

# The Windows listener table, as powershell.exe reports it to WSL.
cat > "$tmp/bin/powershell.exe" <<'STUB'
#!/bin/bash
for port in ${ODS_TEST_WINDOWS_PORTS:-}; do
    printf '%s\r\n' "$port"
done
printf 'ODS_WINDOWS_PORT_SCAN_OK\r\n'
STUB
chmod +x "$tmp/bin/powershell.exe"
export PATH="$tmp/bin:$PATH"

# Phase 06's own resolver for the value it writes to .env.
phase06_resolver="$(sed -n \
    -e '/^    _env_get() {$/,/^    }$/p' \
    -e '/^    _env_get_explicit_first() {$/,/^    }$/p' \
    -e '/^    WHISPER_PORT_VALUE="\$(_env_get_explicit_first WHISPER_PORT "9000")"$/,/^    WHISPER_PORT="\$WHISPER_PORT_VALUE"$/p' \
    "$ROOT/installers/phases/06-directories.sh")"
if [[ "$phase06_resolver" != *'WHISPER_PORT_VALUE="$(_env_get_explicit_first WHISPER_PORT "9000")"'* ]]; then
    printf 'FAIL: phase 06 no longer resolves WHISPER_PORT where this test reads it\n' >&2
    exit 1
fi

# Settings: voice=, windows= (Windows listener ports), retained= (.env
# WHISPER_PORT), explicit= (caller's WHISPER_PORT), gpu=, wsl=, lemonade=, and
# whisper= (the ods-whisper container as "running|project|host ports|working
# dir"; @install is this installation; empty means no such container).
# Prints "<WHISPER_PORT after phase 04, or unset> <port phase 04 checks for
# Whisper> <WHISPER_PORT phase 06 persists> <REQUIREMENTS_MET>".
run_case() (
    # Command substitution clears errexit; a failed phase must fail the case.
    set -euo pipefail
    local voice=false windows='' retained='' explicit='' gpu=nvidia wsl=true lemonade=false whisper=''
    local setting
    for setting in "$@"; do
        case "$setting" in
            voice=*|windows=*|retained=*|explicit=*|gpu=*|wsl=*|lemonade=*|whisper=*) local "$setting" ;;
            *) printf 'unknown case setting: %s\n' "$setting" >&2; return 2 ;;
        esac
    done
    rm -rf "$tmp/install"
    mkdir -p "$tmp/install"
    if [[ -n "$retained" ]]; then
        printf 'WHISPER_PORT=%s\n' "$retained" > "$tmp/install/.env"
    fi
    if [[ -n "$explicit" ]]; then
        WHISPER_PORT="$explicit"
    fi
    export ODS_TEST_WINDOWS_PORTS="$windows" ODS_WSL_HOST_OVERRIDE="$wsl"
    SCRIPT_DIR="$tmp/source" INSTALL_DIR="$tmp/install"
    LOG_FILE="$tmp/requirements.log" PREFLIGHT_REPORT_FILE="$tmp/preflight.json"
    TIER=2 RAM_GB=64 DISK_AVAIL=200
    GPU_BACKEND="$gpu" GPU_VRAM=24000 GPU_NAME=fixture GPU_COUNT=1
    INTERACTIVE=false DRY_RUN=true
    CAP_PLATFORM_ID=wsl CAP_COMPOSE_OVERLAYS=docker-compose.base.yml
    ENABLE_VOICE="$voice" ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_COMFYUI=false
    EXTERNAL_LLM_URL='' ODS_MODE=local LEMONADE_EXTERNAL="$lemonade"
    # The service registry's manifest defaults, as install-core loads them.
    declare -A SERVICE_PORTS=([whisper]=9000 [tts]=8880 [open-webui]=3000 [llama-server]=8080)
    tier_rank() { printf '2\n'; }
    ods_progress() { :; }; chapter() { :; }; ai() { :; }
    log() { printf 'LOG: %s\n' "$*"; }; warn() { printf 'WARN: %s\n' "$*"; }
    ai_ok() { printf 'OK: %s\n' "$*"; }; ai_bad() { printf 'BAD: %s\n' "$*"; }
    ai_warn() { printf 'WARN: %s\n' "$*"; }
    # Only the stubbed Windows listener table may report a port as taken.
    pgrep() { return 1; }; lsof() { return 1; }; ss() { :; }; netstat() { :; }
    docker() {
        printf '%s\n' "$*" >> "$tmp/docker-calls"
        case "${1:-} ${2:-}" in
            'container inspect')
                # Answer only a template that asks for the fields in the
                # order phase 04 reads them.
                [[ -n "$whisper" && "${*: -1}" == ods-whisper && "${3:-}" == --format \
                    && "${4:-}" == '{{.State.Running}}|'*'"com.docker.compose.project" }}|'*'.NetworkSettings.Ports'*'|'*'"com.docker.compose.project.working_dir"'* ]] \
                    || return 1
                printf '%s\n' "${whisper//@install/$INSTALL_DIR}"
                ;;
        esac
    }
    source "$ROOT/installers/lib/detection.sh"
    source "$ROOT/installers/lib/external-services.sh"
    source "$ROOT/installers/phases/04-requirements.sh" >"$tmp/output"

    local after_phase04="${WHISPER_PORT-unset}" checked="${SERVICE_PORTS[whisper]}"
    _env_existing=""
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        _env_existing="$INSTALL_DIR/.env"
    fi
    LEMONADE_EXTERNAL_VALUE="$lemonade"
    source <(printf '%s\n' "$phase06_resolver") >>"$tmp/output"
    printf '%s %s %s %s\n' "$after_phase04" "$checked" "$WHISPER_PORT_VALUE" "$REQUIREMENTS_MET"
)

expect() {
    local label="$1" expected="$2" actual
    shift 2
    actual="$(run_case "$@")"
    if [[ "$actual" != "$expected" ]]; then
        printf 'FAIL: %s: expected "%s", got "%s"\n' "$label" "$expected" "$actual" >&2
        cat "$tmp/output" >&2
        exit 1
    fi
    printf 'PASS: %s\n' "$label"
}

expect 'Lean install reserves 9100 for a later Library add' '9100 9100 9100 true' windows=9000
expect 'Lean install falls back to 9001 when 9100 is taken' '9001 9001 9001 true' windows='9000 9100'
expect 'Free Windows port 9000 keeps the default' 'unset 9000 9000 true'
expect 'Rerun moves a retained generated 9000' '9100 9100 9100 true' windows=9000 retained=9000
expect 'Rerun keeps a retained owner port' 'unset 9500 9500 true' windows=9000 retained=9500
expect 'Voice rerun keeps and checks a retained owner port' 'unset 9500 9500 true' \
    voice=true windows=9000 retained=9500
expect 'An explicit WHISPER_PORT is used as given' '9500 9500 9500 true' windows=9000 explicit=9500
expect 'Voice install still moves the generated default' '9100 9100 9100 true' voice=true windows=9000
expect 'A native Linux host never consults Windows' 'unset 9000 9000 true' windows=9000 wsl=false
# AMD no longer runs Lemonade, whose router held host port 9000, so an AMD
# install keeps Whisper's default; a retired LEMONADE_EXTERNAL changes nothing.
expect 'Native AMD lean install keeps the default 9000' 'unset 9000 9000 true' gpu=amd wsl=false
expect 'WSL AMD lean install keeps a free alternate' '9001 9001 9001 true' \
    windows='9000 9100' gpu=amd lemonade=true

# The installation's own running Whisper is the Windows listener on its port.
expect 'Rerun keeps 9000 while its own Whisper publishes it' 'unset 9000 9000 true' \
    windows=9000 retained=9000 whisper='true|ods|9000 |@install'
expect 'Voice rerun keeps its own Whisper on 9000 without a conflict' 'unset 9000 9000 true' \
    voice=true windows=9000 retained=9000 whisper='true|ods|9000 9000 |@install'
expect 'Voice rerun does not report its own Whisper on a retained port' 'unset 9100 9100 true' \
    voice=true windows=9100 retained=9100 whisper='true|ods|9100 |@install'
expect 'A moved installation still owns its Whisper' 'unset 9000 9000 true' \
    windows=9000 retained=9000 whisper='true|ods|9000 |/previous/ods'
expect 'A non-ODS listener on 9000 still moves Whisper' '9100 9100 9100 true' \
    windows=9000 retained=9000
expect 'A created Whisper that could not publish does not hold 9000' '9100 9100 9100 true' \
    voice=true windows=9000 retained=9000 whisper='false|ods||@install'
expect 'A Whisper that is not running does not hold 9000' '9100 9100 9100 true' \
    voice=true windows=9000 retained=9000 whisper='false|ods|9000 |@install'
expect "Another Compose project's Whisper is not this installation's" '9100 9100 9100 true' \
    windows=9000 retained=9000 whisper='true|other|9000 |/srv/other-ods'
expect 'AMD hosts keep a running Whisper on 9000' 'unset 9000 9000 true' \
    voice=true windows=9000 retained=9000 gpu=amd wsl=false whisper='true|ods|9000 |@install'

run_case windows='9000 9100 9001' >/dev/null
if ! grep -q 'Whisper alternates 9100 and 9001 are unavailable' "$tmp/output"; then
    printf 'FAIL: no warning when every Whisper alternate is taken\n' >&2
    cat "$tmp/output" >&2
    exit 1
fi
printf 'PASS: Taken alternates are reported and leave the default\n'

if ! grep -q '^container inspect ' "$tmp/docker-calls"; then
    printf 'FAIL: phase 04 never inspected the Whisper container\n' >&2
    exit 1
fi
if grep -qvE '^(ps|container inspect) ' "$tmp/docker-calls"; then
    printf 'FAIL: phase 04 ran a Docker command that does not only read state:\n' >&2
    grep -vE '^(ps|container inspect) ' "$tmp/docker-calls" >&2
    exit 1
fi
printf 'PASS: Phase 04 only reads Docker state\n'
