#!/usr/bin/env bash
# Exercise the real parent/child CLI dispatch with disposable state. Existing
# native lifecycle tests separately prove the real lease and child authority.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAC_CLI="${ODS_MAC_CLI_UNDER_TEST:-$ROOT_DIR/installers/macos/ods-macos.sh}"
CLI="$ROOT_DIR/ods-cli"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT
extract() { awk -v name="$2" '$0 == name "() {" { copy=1 } copy { print } copy && /^}$/ { exit }' "$1"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

for scenario in mismatch matching child-failed validation-failed refresh-failed service-only mode-changed; do
    install="$FIXTURE/$scenario"
    mkdir -p "$install/bin"
    touch "$install/bin/ods-host-agent.py" "$install/ods-cli"
    printf 'ODS_MODE=local\nODS_AGENT_KEY=fixture-private-key\n' > "$install/.env"
    printf 'cloud\n' > "$install/effective"
    [[ "$scenario" != matching ]] || printf 'local\n' > "$install/effective"
    : > "$install/trace"
    if output=$(
        exec 2>&1
        INSTALL_DIR="$install" NATIVE_LIFECYCLE_CHILD=false ODS_PYTHON_CMD=transaction
        trace() { printf '%s\n' "$*" >> "$INSTALL_DIR/trace"; }
        test_install() { :; }
        ensure_llama_cpu_budget() { :; }
        get_compose_flags() { printf '%s\n' '-f installers/macos/pixel-native.compose.yaml.disabled'; }
        network_access_is_enabled() { return 1; }
        macos_wait_for_bootstrap_compose_safe() { :; }
        macos_maybe_resume_bootstrap_upgrade() { [[ "$NATIVE_LIFECYCLE_CHILD" == true ]] || trace resume; }
        start_native_llama() { :; }
        read_env_value() { sed -n "s/^$2=//p" "$1"; }
        _env_get_raw() { read_env_value "$INSTALL_DIR/.env" "$1"; }
        _agent_probe_host() { printf '127.0.0.1\n'; }
        ai() { :; }; ai_ok() { :; }; ai_err() { printf '%s\n' "$*" >&2; }; ai_warn() { ai_err "$*"; }
        log() { :; }; success() { :; }; log_error() { ai_err "$*"; }; sleep() { :; }
        dashboard_compose_up() {
            trace "compose:$NATIVE_LIFECYCLE_CHILD"
            [[ "$scenario" != child-failed ]]
        }
        transaction() {
            [[ "$*" == *'--lifecycle-action restart'* ]] || return 64
            trace held
            (NATIVE_LIFECYCLE_CHILD=true; cmd_restart "$service") || return $?
            trace validated
            [[ "$scenario" != validation-failed ]] || return 65
            [[ "$scenario" != mode-changed ]] || printf 'ODS_MODE=hybrid\nODS_AGENT_KEY=fixture-private-key\n' > "$INSTALL_DIR/.env"
            trace released
        }
        curl() {
            trace "snapshot:$NATIVE_LIFECYCLE_CHILD"
            [[ "$*" != *fixture-private-key* ]] || return 66
            [[ "$(cat)" == 'Authorization: Bearer fixture-private-key' ]] || return 67
            printf '{"configuredMode":"%s","effectiveMode":"%s"}\n' "$(_env_get_raw ODS_MODE)" "$(cat "$INSTALL_DIR/effective")"
        }
        cmd_agent() {
            [[ "$1" == restart ]] || return 68
            trace "agent:$NATIVE_LIFECYCLE_CHILD"
            [[ "$scenario" != refresh-failed ]] || return 69
            _env_get_raw ODS_MODE > "$INSTALL_DIR/effective"
        }
        bash() {
            [[ "$1 $2 $3" == "$INSTALL_DIR/ods-cli agent refresh-mode" ]] || return 70
            _agent_refresh_mode "${4:-}"
        }
        for function in _agent_mode_snapshot _agent_refresh_mode; do source <(extract "$CLI" "$function"); done
        for function in native_lifecycle_selected run_native_lifecycle cmd_restart; do source <(extract "$MAC_CLI" "$function"); done
        service=''
        [[ "$scenario" != service-only ]] || service=pixel-native-ingress
        cmd_restart "$service"
    ); then rc=0; else rc=$?; fi
    [[ "$output" != *fixture-private-key* ]] || fail "$scenario leaked the credential"
    ! grep -Eq '^(snapshot|agent):true$' "$install/trace" || fail "$scenario refreshed inside the sealed child"
    case "$scenario" in
        mismatch)
            [[ "$rc" == 0 && "$(cat "$install/effective")" == local ]] || fail "$scenario did not apply the agent mode: $output"
            expected=$'held\ncompose:true\nvalidated\nreleased\nsnapshot:false\nagent:false\nsnapshot:false\nresume'
            ;;
        matching)
            [[ "$rc" == 0 ]] || fail "$scenario failed: $output"
            expected=$'held\ncompose:true\nvalidated\nreleased\nsnapshot:false\nresume'
            ;;
        service-only)
            [[ "$rc" == 0 ]] || fail "$scenario failed: $output"
            expected=$'held\ncompose:true\nvalidated\nreleased'
            ;;
        child-failed)
            [[ "$rc" != 0 ]] || fail "$scenario falsely succeeded"
            expected=$'held\ncompose:true'
            ;;
        validation-failed)
            [[ "$rc" != 0 ]] || fail "$scenario falsely succeeded"
            expected=$'held\ncompose:true\nvalidated'
            ;;
        refresh-failed)
            [[ "$rc" != 0 && "$output" == *'ods restart'* ]] || fail "$scenario hid refresh failure"
            expected=$'held\ncompose:true\nvalidated\nreleased\nsnapshot:false\nagent:false'
            ;;
        mode-changed)
            [[ "$rc" != 0 && "$output" == *'ods restart'* ]] || fail "$scenario ignored the changed mode"
            expected=$'held\ncompose:true\nvalidated\nreleased\nsnapshot:false'
            ;;
    esac
    [[ "$(cat "$install/trace")" == "$expected" ]] || fail "$scenario sequence differs: $(cat "$install/trace")"
    printf '[PASS] native parent mode refresh %s\n' "$scenario"
done
