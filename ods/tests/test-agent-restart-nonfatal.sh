#!/usr/bin/env bash
# Exercise the real session-fallback function, with no host process/network use.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_CLI_UNDER_TEST:-$ROOT_DIR/ods-cli}"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

for scenario in missing-python missing-script unhealthy; do
    mkdir -p "$FIXTURE/$scenario/data" "$FIXTURE/$scenario/bin"
    if [[ "$scenario" == unhealthy ]]; then
        touch "$FIXTURE/$scenario/bin/ods-host-agent.py"
    fi
    if ! output=$(
        INSTALL_DIR="$FIXTURE/$scenario"
        ODS_AGENT_FORCE_SESSION=true
        check_install() { :; }
        load_env() { :; }
        log() { printf '%s\n' "$*"; }
        log_error() { printf '%s\n' "$*" >&2; }
        error() { log_error "$*"; exit 1; }
        warn() { log_error "$*"; }
        success() { log "$*"; }
        _agent_probe_host() { echo 127.0.0.1; }
        curl() { return 1; }
        nohup() { return 1; }
        kill() { return 1; }
        sleep() { :; }
        command() {
            if [[ "$scenario" == missing-python && "$*" == '-v python3' ]]; then return 1; fi
            builtin command "$@"
        }
        source <(awk '/^cmd_agent\(\) \{/{copy=1} copy{print} copy && /^}$/{exit}' "$TARGET")
        cmd_agent restart || warn 'Host agent restart failed (non-fatal)'
        echo 'Update complete'
    ); then
        echo "[FAIL] $scenario exited the guarded update" >&2
        exit 1
    fi
    [[ "$output" == *'Update complete'* ]] || exit 1
    echo "[PASS] $scenario returns control to the guarded update"
done
