#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-link.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

pass() {
    printf '[PASS] %s\n' "$1"
}

ods_sudo() {
    "$@"
}

ods_sudo_available() {
    [[ "${TEST_SUDO_AVAILABLE:-true}" == "true" ]]
}

# shellcheck source=../installers/lib/cli-link.sh
source "$ROOT_DIR/installers/lib/cli-link.sh"

INSTALL_DIR="$TMP_ROOT/current"
OLD_DIR="$TMP_ROOT/old"
OWNER_HOME="$TMP_ROOT/home"
SYSTEM_LINK="$TMP_ROOT/system/ods"
mkdir -p "$INSTALL_DIR" "$OLD_DIR" "$OWNER_HOME" "${SYSTEM_LINK%/*}"
printf '#!/usr/bin/env bash\n' >"$INSTALL_DIR/ods-cli"
printf '#!/usr/bin/env bash\n' >"$OLD_DIR/ods-cli"
chmod 0755 "$INSTALL_DIR/ods-cli" "$OLD_DIR/ods-cli"

ln -s "$OLD_DIR/ods-cli" "$SYSTEM_LINK"
result="$(ODS_CLI_SYSTEM_LINK="$SYSTEM_LINK" ods_bind_cli_command "$INSTALL_DIR" "$OWNER_HOME")"
[[ "$result" == "system:$SYSTEM_LINK" ]] || fail "stale system link was not reported as refreshed"
ods_cli_path_matches_install "$SYSTEM_LINK" "$INSTALL_DIR/ods-cli" \
    || fail "stale system link was not rebound to the current install"
pass "stale system link is rebound to the current install"

rm "$SYSTEM_LINK"
result="$(
    TEST_SUDO_AVAILABLE=false ODS_CLI_SYSTEM_LINK="$SYSTEM_LINK" \
        ods_bind_cli_command "$INSTALL_DIR" "$OWNER_HOME"
)"
[[ "$result" == "user:$OWNER_HOME/.local/bin/ods" ]] \
    || fail "sudo-unavailable install did not use the rootless fallback"
ods_cli_path_matches_install "$OWNER_HOME/.local/bin/ods" "$INSTALL_DIR/ods-cli" \
    || fail "sudo-unavailable fallback does not target the current install"
pass "sudo-unavailable install uses the exact rootless fallback"

rm "$OWNER_HOME/.local/bin/ods"
printf '#!/usr/bin/env bash\n' >"$SYSTEM_LINK"
chmod 0755 "$SYSTEM_LINK"
result="$(ODS_CLI_SYSTEM_LINK="$SYSTEM_LINK" ods_bind_cli_command "$INSTALL_DIR" "$OWNER_HOME")"
[[ "$result" == "user:$OWNER_HOME/.local/bin/ods" ]] \
    || fail "regular system command did not use the rootless fallback"
[[ -f "$SYSTEM_LINK" && ! -L "$SYSTEM_LINK" ]] \
    || fail "regular system command was overwritten"
ods_cli_path_matches_install "$OWNER_HOME/.local/bin/ods" "$INSTALL_DIR/ods-cli" \
    || fail "rootless fallback does not target the current install"
pass "regular system command is preserved and rootless fallback is exact"

rm "$OWNER_HOME/.local/bin/ods"
printf '#!/usr/bin/env bash\n' >"$OWNER_HOME/.local/bin/ods"
chmod 0755 "$OWNER_HOME/.local/bin/ods"
if ODS_CLI_SYSTEM_LINK="$SYSTEM_LINK" ods_bind_cli_command "$INSTALL_DIR" "$OWNER_HOME" >/dev/null 2>&1; then
    fail "unsafe regular user command was overwritten"
fi
[[ -f "$OWNER_HOME/.local/bin/ods" && ! -L "$OWNER_HOME/.local/bin/ods" ]] \
    || fail "unsafe regular user command changed"
pass "unmanaged regular command paths fail closed"

printf 'CLI link refresh tests passed.\n'
