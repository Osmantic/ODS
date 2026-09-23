#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/installers/lib/verified-download.sh"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
printf 'printf executed > "%s"\n' "$stage/executed" > "$stage/source"
if command -v sha256sum >/dev/null; then digest=$(sha256sum "$stage/source")
else digest=$(shasum -a 256 "$stage/source"); fi
digest=${digest%% *}
curl() {
    printf 'called\n' >> "$stage/network"
    local output=''
    while (( $# )); do
        if [[ "$1" == --output ]]; then output="$2"; shift; fi
        shift
    done
    [[ -n "$output" ]] || return 1
    cp "$stage/source" "$output"
}
touch "$stage/download"
ods_download_verified 'https://example.invalid/pinned' "$digest" "$stage/download" >/dev/null \
    || fail 'valid bytes were rejected'
cmp "$stage/source" "$stage/download" || fail 'download changed'
bash "$stage/download"
[[ -f "$stage/executed" ]] || fail 'valid fixture did not execute'
rm "$stage/executed"
printf 'tampered\n' >> "$stage/source"
if ods_download_verified 'https://example.invalid/pinned' "$digest" "$stage/download" 2>/dev/null; then
    bash "$stage/download"
    fail 'tampered download was accepted'
fi
[[ ! -f "$stage/executed" ]] || fail 'tampered artifact executed'
count=$(wc -l < "$stage/network")
if ods_download_verified 'http://example.invalid/pinned' "$digest" "$stage/download"; then
    fail 'plaintext download accepted'
fi
if ods_download_verified 'https://example.invalid/pinned' 'not-a-digest' "$stage/download"; then
    fail 'invalid digest accepted'
fi
ln -s "$stage/download" "$stage/link"
if ods_download_verified 'https://example.invalid/pinned' "$digest" "$stage/link"; then
    fail 'symlink destination accepted'
fi
[[ "$(wc -l < "$stage/network")" == "$count" ]] || fail 'invalid request reached network'
printf '%s\n' '[PASS] Verified downloads reject changed bytes, insecure URLs, malformed digests and symlinks before use'
