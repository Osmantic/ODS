#!/usr/bin/env bash
# Run the real mode/restart functions against disposable files and controlled
# service/transport boundaries. No Docker, launchd, systemd or host process use.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="${ODS_CLI_UNDER_TEST:-$ROOT_DIR/ods-cli}"
MAC_CLI="${ODS_MAC_CLI_UNDER_TEST:-$ROOT_DIR/installers/macos/ods-macos.sh}"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

extract() { awk -v name="$2" '$0 == name "() {" { copy=1 } copy { print } copy && /^}$/ { exit }' "$1"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

for platform in linux macos; do
    for scenario in local-hybrid cloud-local unknown-local quoted-port single-quoted-port matching service-only stack-failed agent-failed read-failed configured-unknown old-agent stale-agent config-changed stack-mode-changed; do
        install="$FIXTURE/$platform-$scenario"
        mkdir -p "$install/bin"
        touch "$install/bin/ods-host-agent.py" "$install/ods-cli"
        initial=local; target=local
        case "$scenario" in
            local-hybrid) target=hybrid ;;
            cloud-local) initial=cloud ;;
            unknown-local) initial=unknown ;;
            matching|service-only|stack-failed) ;;
            *) initial=cloud ;;
        esac
        printf '%s\n' "$initial" > "$install/effective"
        printf 'ODS_MODE=%s\nODS_AGENT_KEY=fixture-private-key\nGPU_BACKEND=amd\n' "$initial" > "$install/.env"
        case "$scenario" in
            quoted-port) printf 'ODS_AGENT_PORT="17710"\n' >> "$install/.env" ;;
            single-quoted-port) printf "ODS_AGENT_PORT='17710'\n" >> "$install/.env" ;;
        esac
        : > "$install/trace"
        if output=$(
            exec 2>&1
            INSTALL_DIR="$install" BLUE='' GREEN='' RED='' CYAN='' NC=''
            NATIVE_LIFECYCLE_CHILD=false
            trace() { printf '%s\n' "$*" >> "$INSTALL_DIR/trace"; }
            check_install() { :; }
            test_install() { :; }
            load_env() { source "$INSTALL_DIR/.env"; }
            ensure_llama_cpu_budget() { :; }
            _env_get_raw() { sed -n "s/^$1=//p" "$INSTALL_DIR/.env" | tail -n 1; }
            read_env_value() { _env_get_raw "$2"; }
            _regenerate_compose_flags() { :; }
            get_compose_flags() { printf '%s\n' '-f fixture.yml'; }
            resolve_service() { printf '%s\n' "$1"; }
            _ods_cli_network_access_enabled() { return 1; }
            network_access_is_enabled() { return 1; }
            _ods_cli_repair_rootless_ownership() { return 0; }
            _ods_cli_refresh_soul() { :; }
            _ods_cli_reconcile_bootstrap_swap() { :; }
            _ods_cli_wait_for_bootstrap_compose_safe() { :; }
            _ods_cli_reload_model_env() { load_env; }
            _ods_cli_wait_for_litellm_ready() { :; }
            _ods_cli_wait_for_hermes_ready_with_retry() { :; }
            _ods_cli_maybe_resume_bootstrap_upgrade() { :; }
            macos_wait_for_bootstrap_compose_safe() { :; }
            macos_maybe_resume_bootstrap_upgrade() { :; }
            native_lifecycle_selected() { return 1; }
            dashboard_compose_up() { docker compose "$@"; }
            start_native_llama() { trace native; }
            _compose_run_with_summary() { trace compose; [[ "$scenario" != stack-failed ]] || exit 41; [[ "$scenario" != stack-mode-changed ]] || _env_set ODS_MODE hybrid; }
            docker() { trace compose; [[ "$scenario" != stack-failed ]] || return 1; [[ "$scenario" != stack-mode-changed ]] || _env_set ODS_MODE hybrid; }
            log() { printf '%s\n' "$*"; }
            log_error() { printf '%s\n' "$*" >&2; }
            success() { log "$*"; }
            warn() { log_error "$*"; }
            error() { log_error "$*"; exit 1; }
            ai() { log "$*"; }
            ai_ok() { log "$*"; }
            ai_err() { log_error "$*"; }
            ai_warn() { log_error "$*"; }
            sleep() { :; }
            _agent_probe_host() { printf '127.0.0.1\n'; }
            curl() {
                trace snapshot
                [[ "$*" != *fixture-private-key* ]] || exit 42
                [[ "$(cat)" == 'Authorization: Bearer fixture-private-key' ]] || exit 43
                case "$scenario" in
                    quoted-port|single-quoted-port)
                        [[ "$*" == *'http://127.0.0.1:17710/v1/model/config'* ]] || return 1 ;;
                esac
                [[ "$scenario" != read-failed ]] || return 1
                local configured effective
                configured="$(_env_get_raw ODS_MODE)"
                effective="$(cat "$INSTALL_DIR/effective")"
                [[ "$scenario" != configured-unknown ]] || configured=unknown
                if [[ "$scenario" == old-agent ]]; then
                    printf '{"configuredMode":"%s"}\n' "$configured"
                else
                    printf '{"configuredMode":"%s","effectiveMode":"%s"}\n' "$configured" "$effective"
                fi
            }
            cmd_agent() {
                case "$1" in
                    refresh-mode) _agent_refresh_mode "${2:-}" ;;
                    restart)
                        trace agent-restart
                        [[ "$scenario" != agent-failed ]] || return 1
                        [[ "$scenario" == stale-agent ]] || _env_get_raw ODS_MODE > "$INSTALL_DIR/effective"
                        [[ "$scenario" != config-changed ]] || _env_set ODS_MODE hybrid
                        ;;
                    *) exit 44 ;;
                esac
            }
            bash() {
                [[ "$1 $2 $3" == "$INSTALL_DIR/ods-cli agent refresh-mode" ]] || exit 45
                cmd_agent refresh-mode "${4:-}"
            }
            for function in _env_set cmd_mode _agent_mode_snapshot _agent_refresh_mode; do
                source <(extract "$CLI" "$function")
            done
            cmd_mode "$target" >/dev/null
            if [[ "$platform" == macos ]]; then
                source <(extract "$MAC_CLI" cmd_restart)
            else
                source <(extract "$CLI" cmd_restart)
            fi
            service=''
            [[ "$scenario" != service-only ]] || service=dashboard
            cmd_restart ${service:+"$service"}
        ); then rc=0; else rc=$?; fi
        printf '%s\n' "$output" > "$install/output"
        [[ "$output" != *fixture-private-key* ]] || fail "$platform $scenario leaked the credential"
        case "$scenario" in
            local-hybrid|cloud-local|unknown-local|quoted-port|single-quoted-port)
                [[ "$rc" == 0 ]] || fail "$platform $scenario failed: $output"
                [[ "$(cat "$install/effective")" == "$target" ]] || fail "$platform $scenario kept the old agent mode"
                [[ "$(grep -c '^agent-restart$' "$install/trace")" == 1 ]] || fail "$platform $scenario did not refresh exactly once"
                awk '/^compose$/ { applied=1 } /^agent-restart$/ && !applied { exit 1 }' "$install/trace" || fail "$platform restarted the agent before applying the stack"
                ;;
            matching|service-only)
                [[ "$rc" == 0 ]] || fail "$platform $scenario failed"
                ! grep -q '^agent-restart$' "$install/trace" || fail "$platform $scenario restarted the agent unnecessarily"
                if [[ "$scenario" == service-only ]]; then
                    ! grep -q '^snapshot$' "$install/trace" || fail "$platform service restart queried model mode"
                fi
                ;;
            *)
                [[ "$rc" != 0 ]] || fail "$platform $scenario falsely reported success"
                case "$scenario" in
                    stack-failed|read-failed|configured-unknown|old-agent|stack-mode-changed)
                        ! grep -q '^agent-restart$' "$install/trace" || fail "$platform $scenario restarted without authority"
                        ;;
                esac
                if [[ "$scenario" != stack-failed ]]; then
                    [[ "$output" == *"ods restart"* ]] || fail "$platform $scenario omitted recovery"
                fi
                ;;
        esac
        printf '[PASS] %s %s\n' "$platform" "$scenario"
    done
done

# The full CLIs re-exec Bash 4+ on macOS. Keep these leaf helpers portable
# anyway, and execute them under Apple's /bin/bash 3.2 in the macOS CI job.
{
    printf 'set -euo pipefail\n'
    extract "$CLI" _agent_mode_snapshot
    extract "$CLI" _agent_refresh_mode
    cat <<'HELPER_FIXTURE'
_env_get_raw() {
    case "$1" in
        ODS_AGENT_KEY) printf "'fixture-private-key'\n" ;;
        ODS_AGENT_PORT) printf '%s\n' "$PORT" ;;
    esac
}
_agent_probe_host() { printf '127.0.0.1\n'; }
curl() {
    [[ "$*" != *fixture-private-key* ]] || return 1
    [[ "$(cat)" == 'Authorization: Bearer fixture-private-key' ]] || return 1
    [[ "$*" == *'http://127.0.0.1:17710/v1/model/config'* ]] || return 1
    printf '{"configuredMode":"local","effectiveMode":"%s"}\n' "$EFFECTIVE"
}
log() { :; }
success() { :; }
log_error() { printf '%s\n' "$*" >&2; }
cmd_agent() { [[ "$1" == restart ]] || return 1; EFFECTIVE=local; }
sleep() { :; }
for PORT in '"17710"' "'17710'"; do
    EFFECTIVE=cloud
    _agent_refresh_mode '"LOCAL"'
    [[ "$EFFECTIVE" == local ]]
    _agent_refresh_mode "'LEMONADE'"
done
printf '[PASS] quoted mode and port helpers under Bash %s\n' "$BASH_VERSION"
HELPER_FIXTURE
} > "$FIXTURE/mode-helpers.sh"
helper_shell="$BASH"
[[ ! -x /bin/bash ]] || helper_shell=/bin/bash
"$helper_shell" "$FIXTURE/mode-helpers.sh"
