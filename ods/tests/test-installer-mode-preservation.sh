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

# An API connected in Settings marks the install ODS_MODE=cloud while it is
# active; its activation record keeps the mode it replaced (fleet, laptop
# 2026-10-05: an upgrade took the marker for a --cloud install).
rm -f "$env_file"
printf 'ODS_MODE=cloud\nLLM_API_URL=http://litellm:4000\n' >"$env_file"
chmod 0600 "$env_file"
record_dir="$TMP_ROOT/data/remote-provider"
mkdir -p "$record_dir"
write_record() {
    printf '{"schema":"ods.remote-provider-activation-state.v1","phase":"%s","previous":{"odsMode":"%s","llmApiUrl":"http://llama-server:8080"}}\n' \
        "$1" "$2" >"$record_dir/activation-state.json"
}
write_record active local
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "local" ]] || fail "an active API route's cloud marker was taken for the install mode ($result)"
result="$(ods_preserve_existing_install_mode cloud true "$env_file")"
[[ "$result" == "cloud" ]] || fail "an explicit --cloud lost to the API route record"
write_record staging hybrid
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "hybrid" ]] || fail "a staging API route did not keep the previous hybrid mode"
write_record active cloud
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "cloud" ]] || fail "an API route over a real cloud install changed its mode"
printf 'not json' >"$record_dir/activation-state.json"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "cloud" ]] || fail "an unreadable activation record changed the mode"
rm -f "$record_dir/activation-state.json"
result="$(ods_preserve_existing_install_mode local false "$env_file")"
[[ "$result" == "cloud" ]] || fail "a cloud install without an API route was not preserved"
pass "an API route's cloud marker resolves to the mode it replaced; real cloud installs stay cloud"

# Pausing an API route restores the LLM_API_URL it replaced, only for the mode
# it replaced (fleet, laptop: http://litellm:4000 stayed and Portal chat failed).
write_record active local
url="$(ods_remote_route_previous_api_url "$TMP_ROOT" local)" || fail "an active API route was not reported"
[[ "$url" == "http://llama-server:8080" ]] || fail "the replaced URL was not returned ($url)"
url="$(ods_remote_route_previous_api_url "$TMP_ROOT" hybrid)" || fail "an active API route was not reported for another mode"
[[ -z "$url" ]] || fail "a URL saved for local mode was returned for hybrid ($url)"
write_record staging local
ods_remote_route_previous_api_url "$TMP_ROOT" local >/dev/null || fail "a staging API route was not reported"
printf '{"phase":"active","previous":{"odsMode":"local","llmApiUrl":"file:///etc/passwd"}}\n' >"$record_dir/activation-state.json"
url="$(ods_remote_route_previous_api_url "$TMP_ROOT" local)" || fail "an active record with a non-HTTP URL was not reported"
[[ -z "$url" ]] || fail "a non-HTTP URL was returned ($url)"
write_record released local
! ods_remote_route_previous_api_url "$TMP_ROOT" local >/dev/null || fail "an inactive API route was reported"
rm -f "$record_dir/activation-state.json"
! ods_remote_route_previous_api_url "$TMP_ROOT" local >/dev/null || fail "a missing record was reported"
pass "a paused API route gives back the URL it replaced, only for the mode it replaced"

printf 'Installer mode preservation tests passed.\n'
