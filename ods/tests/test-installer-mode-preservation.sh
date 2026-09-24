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

# ---------------------------------------------------------------------------
# Offline mode preservation across reruns.
#
# Phase 09 only records an offline install two ways: OFFLINE_MODE=true appended
# to .env and the .offline-mode marker created after the embedded assets are
# validated. A flagless rerun must keep that selection instead of regenerating
# an online .env and re-enabling web search/update checks on an air-gapped
# host. Deleting the marker is the supported escape back to online mode.
# ---------------------------------------------------------------------------

rm -f "$env_file"
install_dir="$TMP_ROOT/install"
marker="$install_dir/.offline-mode"
mkdir -p "$install_dir"
env_file="$install_dir/.env"

# Fresh install: no .env, no marker -> caller default kept.
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "fresh install produced an offline record"
pass "fresh installs keep the caller default"

# Recorded offline install: .env true + marker present -> restored.
printf 'OFFLINE_MODE=true\nWEB_SEARCH_ENABLED=false\n' >"$env_file"
: >"$marker"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "true" ]] || fail "recorded offline install was not preserved"
pass "recorded offline install preserves OFFLINE_MODE=true"

# Explicit --offline stays authoritative even without a prior record.
rm -f "$env_file" "$marker"
result="$(ods_preserve_existing_offline_mode true true "$env_file" "$marker")"
[[ "$result" == "true" ]] || fail "explicit --offline was overridden"
pass "explicit --offline remains authoritative"

# Escape hatch: marker deleted means the operator opted back to online mode
# even though .env still carries the stale OFFLINE_MODE=true line.
printf 'OFFLINE_MODE=true\n' >"$env_file"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "stale .env was trusted without the marker"
pass "deleting the marker restores online mode"

# Marker alone is not enough: a .env that explicitly says false means the
# operator already migrated the config back online.
: >"$marker"
printf 'OFFLINE_MODE=false\n' >"$env_file"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "marker overrode an explicit .env false"
pass "explicit .env false wins over a stale marker"

# Malformed/duplicated .env values fail closed to the caller default.
printf 'OFFLINE_MODE=true;touch /tmp/unsafe\n' >"$env_file"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "malformed offline value was trusted"
pass "malformed offline values fail closed without evaluation"

printf 'OFFLINE_MODE=true\nOFFLINE_MODE=false\n' >"$env_file"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "duplicate offline entries were trusted"
pass "duplicate offline entries fail closed"

# Untrusted .env files fail closed like the mode reader.
chmod 0666 "$env_file"
printf 'OFFLINE_MODE=true\n' >"$env_file"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "writable-by-others .env was trusted"
pass "writable-by-others .env files fail closed"
chmod 0600 "$env_file"

rm -f "$env_file"
ln -s "$TMP_ROOT/some-target" "$env_file"
result="$(ods_preserve_existing_offline_mode false false "$env_file" "$marker")"
[[ "$result" == "false" ]] || fail "symlinked .env was trusted"
pass "symlinked .env files fail closed"

printf 'Installer mode preservation tests passed.\n'
