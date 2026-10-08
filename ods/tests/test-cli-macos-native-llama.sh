#!/usr/bin/env bash
# On macOS the model runs as the native launchd job com.ods.llama-server and
# the Compose llama-server is a replicas-0 placeholder. `ods stop|start|restart
# llama-server` must go to ods-macos.sh, which owns that job; Compose would
# report success without touching the running model. Every other target keeps
# its Compose path.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'cd / && rm -rf -- "$work"' EXIT
export INSTALL_DIR="$work/install"
mkdir -p "$INSTALL_DIR"
macos_log="$work/macos.log"
compose_log="$work/compose.log"

# Exercise the actual functions without dispatching the full CLI.
for name in _ods_cli_macos_native_llama _ods_cli_macos_llama cmd_stop cmd_start cmd_restart; do
    source <(sed -n "/^${name}() {/,/^}/p" "$root/ods-cli")
done
RED=""
NC=""
check_install() { :; }
load_env() { :; }
ensure_llama_cpu_budget() { :; }
sr_load() { :; }
error() { echo "ERROR: $1" >&2; exit 1; }
resolve_service() {
    case "$1" in
        llm|ods-llama-server) printf '%s\n' llama-server ;;
        *) printf '%s\n' "$1" ;;
    esac
}
uname() { printf '%s\n' "$FAKE_UNAME"; }
get_compose_flags() {
    printf '%s\n' 'compose-flags' >> "$compose_log"
    # A start or restart that reaches Compose stops here; stop continues.
    [[ "${FAKE_COMPOSE_STOPS:-false}" != true ]] || exit 3
    printf '%s\n' '-f docker-compose.base.yml'
}
_compose_run_with_summary() { printf '%s\n' "$*" >> "$compose_log"; }

macos_cli_stub() {
    printf '%s\n' '#!/usr/bin/env bash' \
        "printf '%s %s\n' \"INSTALL_DIR=\$INSTALL_DIR\" \"\$*\" >> '$macos_log'" \
        'exit "${FAKE_MACOS_RC:-0}"' > "$INSTALL_DIR/ods-macos.sh"
}

fail() { printf 'FAIL: %s\n' "$1"; exit 1; }

reset_logs() { : > "$macos_log"; : > "$compose_log"; }

expect_macos() {
    local label="$1" want="$2"
    [[ "$(cat "$macos_log")" == "INSTALL_DIR=$INSTALL_DIR $want" ]] \
        || fail "$label: macOS CLI got '$(cat "$macos_log")', want '$want'"
    [[ ! -s "$compose_log" ]] || fail "$label: Compose was used: $(cat "$compose_log")"
}

expect_compose() {
    local label="$1" want="$2"
    [[ ! -s "$macos_log" ]] || fail "$label: macOS CLI was used: $(cat "$macos_log")"
    grep -qxF -- "$want" "$compose_log" || fail "$label: Compose log lacks '$want': $(cat "$compose_log")"
}

macos_cli_stub

# macOS (Apple Silicon native model): llama-server and its aliases go to the
# macOS CLI with the same install directory.
FAKE_UNAME=Darwin GPU_BACKEND=apple
for spelling in llama-server llm ods-llama-server; do
    reset_logs; cmd_stop "$spelling"
    expect_macos "macOS stop $spelling" "stop llama-server"
done
reset_logs; cmd_start llama-server
expect_macos "macOS start" "start llama-server"
reset_logs; cmd_restart llm
expect_macos "macOS restart" "restart llama-server"

# Other macOS targets keep Compose; a whole-stack stop is unchanged.
reset_logs; cmd_stop open-webui
expect_compose "macOS stop open-webui" "Stopping open-webui -f docker-compose.base.yml stop open-webui"
reset_logs; cmd_stop
expect_compose "macOS whole-stack stop" "Stopping all services -f docker-compose.base.yml stop"

# Linux, and a Mac without the Apple backend, keep the Compose llama-server.
for platform in "Linux nvidia" "Darwin cpu"; do
    read -r FAKE_UNAME GPU_BACKEND <<< "$platform"
    reset_logs; cmd_stop llama-server
    expect_compose "$platform stop" "Stopping llama-server -f docker-compose.base.yml stop llama-server"
    for command in cmd_start cmd_restart; do
        reset_logs
        # Not on the left of || : errexit must stay live in the subshell so
        # the stub's failure at get_compose_flags ends the command there.
        set +e
        ( set -e; FAKE_COMPOSE_STOPS=true "$command" llama-server )
        rc=$?
        set -e
        [[ "$rc" -eq 3 ]] || fail "$platform $command: expected the Compose path (rc 3), got rc $rc"
        expect_compose "$platform $command" "compose-flags"
    done
done

# Failures say what happened and where to get help.
FAKE_UNAME=Darwin GPU_BACKEND=apple
reset_logs
rc=0; ( FAKE_MACOS_RC=1 cmd_stop llama-server ) 2> "$work/err" || rc=$?
[[ "$rc" -eq 1 ]] || fail "a failed macOS stop returned rc $rc"
grep -qF 'discord.gg/' "$work/err" || fail "a failed macOS stop gave no help link"

rm -f -- "$INSTALL_DIR/ods-macos.sh"
reset_logs
rc=0; ( cmd_stop llama-server ) 2> "$work/err" || rc=$?
[[ "$rc" -eq 1 ]] || fail "a missing macOS CLI returned rc $rc"
grep -qF 'The macOS service CLI is missing' "$work/err" || fail "a missing macOS CLI was not named: $(cat "$work/err")"
grep -qF 'Re-run the installer' "$work/err" || fail "a missing macOS CLI gave no recovery step"
grep -qF 'discord.gg/' "$work/err" || fail "a missing macOS CLI gave no help link"
[[ ! -s "$compose_log" ]] || fail "a missing macOS CLI fell back to Compose"

printf '%s\n' 'PASS: macOS llama-server lifecycle goes to the native service; other paths keep Compose'
