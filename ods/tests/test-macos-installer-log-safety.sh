#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
    printf '[SKIP] macOS installer log safety requires Darwin\n'
    exit 0
fi

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root_dir/installers/lib/secure-log.sh"
tmp_dir="$(mktemp -d /tmp/ods-mac-log-safety.XXXXXX)"
trap 'rm -rf -- "$tmp_dir"' EXIT

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

# /tmp is a symlink to /private/tmp on macOS. The helper must resolve it,
# retain sticky-directory protection, and never follow a log-file symlink.
( umask 0002; ods_prepare_install_log "$tmp_dir/fresh.log" ) || fail 'fresh log creation failed'
[[ "$(stat -f '%Lp' "$tmp_dir/fresh.log")" == 600 ]] || fail 'fresh log is not 0600'
pass 'fresh macOS log is 0600 under umask 0002'

printf 'keep prior diagnostics\n' >"$tmp_dir/existing.log"
chmod 0644 "$tmp_dir/existing.log"
ods_prepare_install_log "$tmp_dir/existing.log" || fail 'existing log protection failed'
[[ "$(stat -f '%Lp' "$tmp_dir/existing.log")" == 600 ]] || fail 'existing log is not 0600'
[[ "$(cat "$tmp_dir/existing.log")" == 'keep prior diagnostics' ]] || fail 'existing log was truncated'
pass 'existing macOS log is privatized without truncation'

printf 'unmodified\n' >"$tmp_dir/target.txt"
ln -s "$tmp_dir/target.txt" "$tmp_dir/link.log"
if ods_prepare_install_log "$tmp_dir/link.log" 2>/dev/null; then
    fail 'symlink log was accepted'
fi
[[ "$(cat "$tmp_dir/target.txt")" == unmodified ]] || fail 'symlink target was modified'
pass 'macOS log symlink is rejected'

mkdir "$tmp_dir/unsafe"
chmod 0777 "$tmp_dir/unsafe"
if ods_prepare_install_log "$tmp_dir/unsafe/new.log" 2>/dev/null; then
    fail 'non-sticky writable parent was accepted'
fi
pass 'unsafe macOS log parent is rejected'

# shellcheck disable=SC2016 # Match literal source code, not shell expansion.
grep -Fq 'source "${SOURCE_ROOT}/installers/lib/secure-log.sh"' \
    "$root_dir/installers/macos/install-macos.sh" || fail 'macOS installer does not source guard'
# shellcheck disable=SC2016 # Match literal source code, not shell expansion.
grep -Fq 'ods_prepare_install_log "$ODS_LOG_FILE" || exit 1' \
    "$root_dir/installers/macos/install-macos.sh" || fail 'macOS installer does not guard its log'
pass 'macOS installer uses the shared guard before Phase 1'
