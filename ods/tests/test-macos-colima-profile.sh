#!/usr/bin/env bash
# Behavioral regression: private-network repair must keep the Docker-selected
# Colima profile instead of silently creating and activating the default VM.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

extract_installer_function() {
    local function_name="$1"
    awk -v signature="^${function_name}[(][)]" '
        $0 ~ signature { capture=1 }
        capture { print }
        capture && /^}/ { exit }
    ' "$INSTALLER"
}

for function_name in \
    _resolve_active_colima_profile \
    _active_colima \
    _active_colima_hint_args; do
    function_body="$(extract_installer_function "$function_name")"
    [[ -n "$function_body" ]] || fail "could not extract $function_name"
    eval "$function_body"
done

export ODS_LOG_FILE="$TMP_DIR/install.log"
export MOCK_COLIMA_CALLS="$TMP_DIR/colima-calls.log"
export PATH="$TMP_DIR:$PATH"
MOCK_CONTEXT=""
MOCK_ENDPOINT=""

cat > "$TMP_DIR/colima" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_COLIMA_CALLS"
MOCK
chmod +x "$TMP_DIR/colima"

docker() {
    if [[ "$1" == "context" && "$2" == "show" ]]; then
        printf '%s\n' "$MOCK_CONTEXT"
        return 0
    fi
    if [[ "$1" == "context" && "$2" == "inspect" ]]; then
        printf '%s\n' "$MOCK_ENDPOINT"
        return 0
    fi
    fail "unexpected docker invocation: $*"
}

reset_case() {
    COLIMA_PROFILE=""
    MOCK_CONTEXT="$1"
    MOCK_ENDPOINT="${2:-}"
    : > "$MOCK_COLIMA_CALLS"
}

reset_case "colima-ods-native"
_resolve_active_colima_profile || fail "named Colima context was not resolved"
[[ "$COLIMA_PROFILE" == "ods-native" ]] \
    || fail "named context resolved to '$COLIMA_PROFILE'"
_active_colima status --json
[[ "$(cat "$MOCK_COLIMA_CALLS")" == "status --profile ods-native --json" ]] \
    || fail "named profile was not forwarded to Colima"
[[ "$(_active_colima_hint_args)" == " --profile ods-native" ]] \
    || fail "named profile hint is incorrect"
pass "named Docker context remains on its Colima profile"

reset_case "colima"
_resolve_active_colima_profile || fail "default Colima context was not resolved"
[[ "$COLIMA_PROFILE" == "default" ]] \
    || fail "default context resolved to '$COLIMA_PROFILE'"
_active_colima stop
[[ "$(cat "$MOCK_COLIMA_CALLS")" == "stop" ]] \
    || fail "default profile should not add a profile argument"
[[ -z "$(_active_colima_hint_args)" ]] \
    || fail "default profile hint should be empty"
pass "default Colima context keeps legacy CLI behavior"

COLIMA_PROFILE=""
[[ -z "$(_active_colima_hint_args)" ]] \
    || fail "unresolved profile hint should be empty"
pass "unresolved profile cannot produce a malformed recovery command"

reset_case "custom-context" "unix:///Users/test/.colima/qa_blue/docker.sock"
_resolve_active_colima_profile || fail "endpoint-backed Colima profile was not resolved"
[[ "$COLIMA_PROFILE" == "qa_blue" ]] \
    || fail "endpoint profile resolved to '$COLIMA_PROFILE'"
pass "Colima socket endpoint provides a safe fallback profile"

reset_case "colima-../../unsafe"
if _resolve_active_colima_profile; then
    fail "unsafe context-derived profile was accepted"
fi
[[ -z "$COLIMA_PROFILE" ]] || fail "unsafe profile changed active custody"

reset_case "desktop-linux" "unix:///Users/test/.docker/run/docker.sock"
if _resolve_active_colima_profile; then
    fail "non-Colima endpoint was accepted"
fi
[[ -z "$COLIMA_PROFILE" ]] || fail "unrelated context changed active custody"
pass "unsafe and unrelated profiles fail closed"

grep -qF 'colima_config="${COLIMA_HOME:-$HOME/.colima}/${COLIMA_PROFILE}/colima.yaml"' "$INSTALLER" \
    || fail "private-route verification does not inspect the selected profile config"
grep -qF '_active_colima stop' "$INSTALLER" \
    || fail "private-route repair does not stop the selected profile"
grep -qF '_active_colima start --network-address --network-preferred-route' "$INSTALLER" \
    || fail "private-route repair does not restart the selected profile"
grep -qF 'colima start${profile_args} --cpu ${min_cpus}' "$INSTALLER" \
    || fail "CPU-budget recovery hint does not preserve the selected profile"
pass "private-route repair is profile-scoped end to end"

echo "[OK] macOS Colima profile custody contract holds"
