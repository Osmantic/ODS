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
    fn_src="$work/fn_$name.sh"
    sed -n "/^${name}() {/,/^}/p" "$root/ods-cli" > "$fn_src"
    source "$fn_src"
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

# macOS CLI (ods-macos.sh): stopping native llama-server or llama must work
# when Docker Desktop is down. Other services and whole-stack stop keep the
# Docker gate.
macos_fixture="$work/macos_fixture"
macos_bin="$macos_fixture/bin"
macos_install="$macos_fixture/install"
mkdir -p "$macos_bin" "$macos_install/installers/macos/lib" "$macos_install/data"
touch "$macos_install/docker-compose.base.yml"
printf '%s\n' 'GPU_BACKEND=apple' "ODS_INSTALL_DIR=$macos_install" > "$macos_install/.env"

# Hermetic shims: docker info fails (Docker down); launchctl reports no active
# job; native-llama-service.sh stop records invocations and succeeds.
cat > "$macos_bin/docker" <<'EOF'
#!/bin/sh
if [ "$1" = "info" ] || [ "$1" = "compose" ]; then
    echo "Cannot connect to the Docker daemon" >&2
    exit 1
fi
exit 0
EOF
chmod +x "$macos_bin/docker"

cat > "$macos_bin/launchctl" <<'EOF'
#!/bin/sh
exit 113
EOF
chmod +x "$macos_bin/launchctl"

native_log="$macos_fixture/native.log"
cat > "$macos_install/installers/macos/lib/native-llama-service.sh" <<EOF
#!/bin/sh
printf '%s\n' "native-llama-service \$1" >> '$native_log'
if [ "\$1" = "start" ]; then
    printf '%s\n' "\$\$" > "\$3"
fi
exit 0
EOF
chmod +x "$macos_install/installers/macos/lib/native-llama-service.sh"

mkdir -p "$macos_install/data/models" "$macos_install/bin"
touch "$macos_install/data/models/Qwen3.5-9B-Q4_K_M.gguf"
touch "$macos_install/bin/llama-server"
chmod +x "$macos_install/bin/llama-server"

cat > "$macos_bin/curl" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$macos_bin/curl"

run_macos_cmd() {
    local cmd="$1" target="${2:-}"
    local args=("$cmd")
    [[ -z "$target" ]] || args+=("$target")
    PATH="$macos_bin:$PATH" INSTALL_DIR="$macos_install" ODS_INSTALL_DIR="$macos_install" ODS_HOME="$macos_install" \
        bash "$root/installers/macos/ods-macos.sh" "${args[@]}"
}

# Native stop, start, and restart succeed and trigger native service without Docker.
for action in stop start restart; do
    for target in llama-server llama; do
        : > "$native_log"
        rm -f "$macos_install/data/.llama-server.pid"
        run_macos_cmd "$action" "$target" 2>"$work/err" || fail "ods-macos.sh $action $target failed with Docker down: $(cat "$work/err")"
        if [ "$action" = "restart" ]; then
            grep -qF 'native-llama-service stop' "$native_log" || fail "ods-macos.sh restart $target did not stop native service"
            grep -qF 'native-llama-service start' "$native_log" || fail "ods-macos.sh restart $target did not start native service"
        else
            grep -qF "native-llama-service $action" "$native_log" || fail "ods-macos.sh $action $target did not run native $action"
        fi
    done
done

# Other services (open-webui, ods-proxy) and whole-stack still require Docker when down.
for action in stop start restart; do
    for target in open-webui ods-proxy ""; do
        rc=0; run_macos_cmd "$action" "$target" >"$work/out" 2>&1 || rc=$?
        [[ "$rc" -eq 1 ]] || fail "ods-macos.sh $action '$target' did not fail when Docker is down (rc $rc)"
        grep -qF 'Docker Desktop is not running' "$work/out" || fail "ods-macos.sh $action '$target' did not enforce Docker gate: $(cat "$work/out")"
    done
done

# Missing install directory is rejected.
for action in stop start restart; do
    rc=0; PATH="$macos_bin:$PATH" INSTALL_DIR="$macos_fixture/missing" ODS_INSTALL_DIR="$macos_fixture/missing" ODS_HOME="$macos_fixture/missing" \
        bash "$root/installers/macos/ods-macos.sh" "$action" llama-server >"$work/out" 2>&1 || rc=$?
    [[ "$rc" -eq 1 ]] || fail "ods-macos.sh $action llama-server with missing install dir returned rc $rc"
    grep -qF 'ODS not found at' "$work/out" || fail "missing install dir did not report ODS not found: $(cat "$work/out")"
done

# Docker UP behavior remains unchanged: native service operations and Compose operations succeed.
cat > "$macos_bin/docker" <<'EOF'
#!/bin/sh
if [ "$1" = "info" ]; then
    echo "Server Version: 25.0.0"
    exit 0
fi
if [ "$1" = "compose" ]; then
    exit 0
fi
exit 0
EOF
chmod +x "$macos_bin/docker"

for action in stop start restart; do
    : > "$native_log"
    rm -f "$macos_install/data/.llama-server.pid"
    run_macos_cmd "$action" llama-server 2>"$work/err" || fail "ods-macos.sh $action llama-server failed with Docker up: $(cat "$work/err")"
    if [ "$action" = "restart" ]; then
        grep -qF 'native-llama-service stop' "$native_log" || fail "ods-macos.sh restart llama-server (Docker up) did not stop native service"
        grep -qF 'native-llama-service start' "$native_log" || fail "ods-macos.sh restart llama-server (Docker up) did not start native service"
    else
        grep -qF "native-llama-service $action" "$native_log" || fail "ods-macos.sh $action llama-server (Docker up) did not run native $action"
    fi
done

printf '%s\n' 'PASS: macOS llama-server lifecycle goes to the native service; other paths keep Compose'
