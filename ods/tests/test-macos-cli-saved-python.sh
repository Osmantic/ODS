#!/usr/bin/env bash
# Exercise the actual documented CLI entry point from a fresh shell. Only
# external Docker/Python boundaries are fixtures; no CLI functions are replaced.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="${ODS_INGRESS_CLI_SOURCE_ROOT:-$ROOT}/installers/macos/ods-macos.sh"
TMP_PARENT="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
FIXTURE="$(mktemp -d "$TMP_PARENT/ods-cli-saved-python.XXXXXX")"
cleanup() {
    local resolved
    [[ -d "$FIXTURE" && ! -L "$FIXTURE" ]] || return 0
    resolved="$(cd "$FIXTURE" && pwd -P)" || return 1
    if [[ "$resolved" != "$FIXTURE" || "$resolved" != "$TMP_PARENT"/ods-cli-saved-python.* ]]; then
        echo 'Refusing cleanup outside the owned fixture directory' >&2
        return 1
    fi
    rm -rf -- "$resolved"
}
trap cleanup EXIT
mkdir -p "$FIXTURE/install" "$FIXTURE/bin" "$FIXTURE/saved runtime" "$FIXTURE/home"
INSTALL="$FIXTURE/install"
EVENT_LOG="$FIXTURE/events"
# Compose selection runs before ingress dispatch; provide its shipped helpers
# and recipes while keeping only the selected interpreter boundary simulated.
mkdir -p "$INSTALL/installers/macos/lib"
cp "$ROOT/installers/macos/lib/"dashboard-private-state{,-worker}.py "$INSTALL/installers/macos/lib/"
cp "$ROOT/installers/macos/dashboard-private-state.compose.yaml" "$INSTALL/installers/macos/"
cp "$ROOT/installers/macos/pixel-native.compose.yaml.disabled" "$INSTALL/installers/macos/"

cat > "$FIXTURE/bin/docker" <<'EOF'
#!/bin/bash
if [[ "$*" == 'info' ]]; then exit 0; fi
if [[ "$*" == 'info --format {{.NCPU}}' ]]; then echo 16; exit 0; fi
printf 'unexpected-docker:%s\n' "$*" >> "$EVENT_LOG"
exit 29
EOF
for selector in 'saved runtime/python' bin/override-python bin/python3; do
    cat > "$FIXTURE/$selector" <<'EOF'
#!/bin/bash
set -euo pipefail
[[ "$#" == 10 && "$1" == "$ODS_HOME/installers/macos/lib/pixel-native-ingress-refresh.py" ]]
[[ "$2" == --install-dir && "$3" == "$ODS_HOME" && "$4" == --start-stopped-edge ]]
[[ "$5" == --lifecycle-action && "$6" == "$EXPECTED_ACTION" ]]
[[ "$7" == --lifecycle-service && "$8" == pixel-native-ingress && "$9" == --lifecycle-bash ]]
# An unrelated .env key must not be imported by Python-selector preparation.
[[ -z "${ENV_UNRELATED_FIXTURE+x}" ]]
printf '%s\n' "$0" >> "$EVENT_LOG"
EOF
    chmod +x "$FIXTURE/$selector"
done
chmod +x "$FIXTURE/bin/docker"
printf 'services: {}\n' > "$INSTALL/docker-compose.base.yml"
printf '%s\n' '-f installers/macos/pixel-native.compose.yaml.disabled' > "$INSTALL/.compose-flags"

write_env() {
    cat > "$INSTALL/.env" <<'EOF'
HERMES_DASHBOARD_SESSION_TOKEN=fixture
GPU_BACKEND=apple
LLAMA_CPU_LIMIT=8.0
LLAMA_CPU_RESERVATION=2.0
TTS_CPU_LIMIT=8.0
TTS_CPU_RESERVATION=2.0
WHISPER_CPU_LIMIT=4.0
WHISPER_CPU_RESERVATION=1.0
HERMES_CPU_LIMIT=4.0
HERMES_CPU_RESERVATION=0.5
COMFYUI_CPU_LIMIT=16.0
COMFYUI_CPU_RESERVATION=2.0
UNRELATED_FIXTURE=must-not-be-imported
EOF
    [[ $# == 0 ]] || printf 'ODS_PYTHON_CMD=%s\n' "$1" >> "$INSTALL/.env"
}

run_cli() {
    local action="$1"
    shift
    : > "$EVENT_LOG"
    # A stale prefixed value must never substitute for a missing saved value.
    env -i HOME="$FIXTURE/home" PATH="$FIXTURE/bin:/usr/bin:/bin" \
        ODS_HOME="$INSTALL" EVENT_LOG="$EVENT_LOG" EXPECTED_ACTION="$action" \
        ENV_ODS_PYTHON_CMD=/stale-prefixed-python "$@" \
        "$BASH" "$CLI" "$action" pixel-native-ingress > "$FIXTURE/stdout" 2> "$FIXTURE/stderr"
}

expect_selector() {
    [[ "$(cat "$EVENT_LOG")" == "$1" ]] || {
        cat "$FIXTURE/stdout" "$FIXTURE/stderr" "$EVENT_LOG" >&2
        echo "Unexpected interpreter selection; wanted $1" >&2
        exit 1
    }
}

for action in start restart; do
    write_env "\"$FIXTURE/saved runtime/python\" # installer runtime"
    cp "$INSTALL/.env" "$FIXTURE/env-before"
    run_cli "$action"
    expect_selector "$FIXTURE/saved runtime/python"
    cmp "$INSTALL/.env" "$FIXTURE/env-before"
done
echo 'PASS: saved quoted interpreter drives direct start/restart without env edits'

write_env "'$FIXTURE/saved runtime/python'"
run_cli start ODS_PYTHON_CMD="$FIXTURE/bin/override-python"
expect_selector "$FIXTURE/bin/override-python"
echo 'PASS: explicit environment override wins'

for value in '""' absent; do
    if [[ "$value" == absent ]]; then write_env; else write_env "$value"; fi
    run_cli start
    expect_selector "$FIXTURE/bin/python3"
done
echo 'PASS: missing/empty saved selector retains python3 default and ignores stale prefixed env'

# The parser and invocation must treat shell text as a literal executable name.
# Neither an invalid saved selector nor an invalid explicit override may silently
# fall back to another interpreter or run the embedded command substitution.
invalid='$(touch selector-was-evaluated)'
write_env "'$invalid'"
if run_cli start; then echo 'Invalid saved selector unexpectedly succeeded' >&2; exit 1; fi
[[ ! -s "$EVENT_LOG" && ! -e "$INSTALL/selector-was-evaluated" ]]
write_env "\"$FIXTURE/saved runtime/python\""
if run_cli start ODS_PYTHON_CMD="$invalid"; then echo 'Invalid override unexpectedly succeeded' >&2; exit 1; fi
[[ ! -s "$EVENT_LOG" && ! -e "$INSTALL/selector-was-evaluated" ]]
echo 'PASS: invalid selectors fail literally without evaluation or fallback'
