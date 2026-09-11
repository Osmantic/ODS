#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ods-install-mode.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

pass() {
    printf '[PASS] %s\n' "$1"
}

# shellcheck source=../installers/lib/install-mode.sh
source "$ROOT_DIR/installers/lib/install-mode.sh"

env_file="$TMP_ROOT/.env"
printf 'ODS_MODE=cloud\nLLM_API_URL=http://litellm:4000\n' >"$env_file"

result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "cloud" ]] || fail "implicit rerun did not preserve cloud mode"
pass "implicit rerun preserves the installed cloud mode"

result="$(ods_preserve_existing_install_mode local true "$env_file")"
[[ "$result" == "local" ]] || fail "explicit mode did not override installed mode"
pass "explicit mode overrides the installed mode"

printf 'ODS_MODE=hybrid\n' >"$env_file"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "hybrid" ]] || fail "hybrid mode was not preserved"
pass "all supported persisted modes are accepted"

printf 'ODS_MODE=cloud\nODS_MODE=local\n' >"$env_file"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "local" ]] || fail "duplicate mode entries were trusted"
pass "duplicate mode entries fail closed"

printf 'ODS_MODE=cloud;touch /tmp/unsafe\n' >"$env_file"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "local" ]] || fail "malformed mode was trusted"
pass "malformed mode values fail closed without evaluation"

printf 'ODS_MODE=cloud\n' >"$env_file"
result="$(ods_preserve_existing_install_mode local false "$env_file" 2>/dev/null || true)"
[[ "$result" == "cloud" ]] || fail "owner-controlled mode was not readable"
if ods_existing_install_mode "$env_file" 2147483647 >/dev/null 2>&1; then
    fail "unexpected-owner mode file was trusted"
fi
pass "mode preservation requires the expected file owner"

chmod 0666 "$env_file"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "local" ]] || fail "writable-by-others mode file was trusted"
chmod 0600 "$env_file"
pass "writable-by-others mode files fail closed"

target="$TMP_ROOT/target.env"
printf 'ODS_MODE=cloud\n' >"$target"
rm "$env_file"
ln -s "$target" "$env_file"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "local" ]] || fail "symlinked mode file was trusted"
pass "symlinked mode files fail closed"

printf 'Installer mode preservation tests passed.\n'
