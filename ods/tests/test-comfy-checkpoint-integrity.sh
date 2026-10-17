#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$root/installers/lib/comfy-checkpoint.sh"

scratch="$(mktemp -d)"
trap 'rm -f -- "$scratch/part" "$scratch/final" "$scratch/link" "$scratch/lock" "$scratch/foreign-lock"; rmdir -- "$scratch"' EXIT
printf 'ODS checkpoint fixture\n' > "$scratch/part"
bytes="$(wc -c < "$scratch/part")"
bytes="${bytes//[[:space:]]/}"
sha="$(sha256sum -- "$scratch/part")"
sha="${sha%% *}"

ods_verify_checkpoint_file "$scratch/part" "$bytes" "$sha"
if ods_acquire_checkpoint_lock "$scratch/missing/lock" 2>/dev/null; then
    echo 'FAIL: lock-file open failure was accepted' >&2; exit 1
fi
flock() { return 2; }
if ods_acquire_checkpoint_lock "$scratch/lock"; then
    echo 'FAIL: flock error was accepted' >&2; exit 1
fi
unset -f flock
ods_acquire_checkpoint_lock "$scratch/lock"
if bash -c '. "$1"; ods_acquire_checkpoint_lock "$2"' _ \
    "$root/installers/lib/comfy-checkpoint.sh" "$scratch/lock"; then
    echo 'FAIL: concurrent checkpoint lock was accepted' >&2; exit 1
fi
if ods_verify_checkpoint_file "$scratch/part" "$((bytes + 1))" "$sha"; then
    echo 'FAIL: wrong checkpoint size was accepted' >&2; exit 1
fi
if ods_verify_checkpoint_file "$scratch/part" "$bytes" "$(printf '0%.0s' {1..64})"; then
    echo 'FAIL: wrong checkpoint hash was accepted' >&2; exit 1
fi
if ods_promote_verified_checkpoint "$scratch/part" "$scratch/final" "$bytes" "$(printf '0%.0s' {1..64})"; then
    echo 'FAIL: unverified checkpoint was promoted' >&2; exit 1
fi
[[ -f "$scratch/part" && ! -e "$scratch/final" ]]

ods_promote_verified_checkpoint "$scratch/part" "$scratch/final" "$bytes" "$sha"
[[ ! -e "$scratch/part" && -f "$scratch/final" ]]
ods_verify_checkpoint_file "$scratch/final" "$bytes" "$sha"

printf 'replacement\n' > "$scratch/part"
if ods_promote_verified_checkpoint "$scratch/part" "$scratch/final" "$bytes" "$sha"; then
    echo 'FAIL: existing checkpoint was overwritten' >&2; exit 1
fi
[[ "$(cat "$scratch/final")" == 'ODS checkpoint fixture' ]]

if ln -s "$scratch/final" "$scratch/link" 2>/dev/null && [[ -L "$scratch/link" ]]; then
    if ods_verify_checkpoint_file "$scratch/link" "$bytes" "$sha"; then
        echo 'FAIL: checkpoint symlink was accepted' >&2; exit 1
    fi
    ln -s "$scratch/final" "$scratch/foreign-lock"
    before="$(cat "$scratch/final")"
    if ods_acquire_checkpoint_lock "$scratch/foreign-lock"; then
        echo 'FAIL: symlink lock was accepted' >&2; exit 1
    fi
    [[ "$(cat "$scratch/final")" == "$before" ]]
elif [[ "$(uname -s)" != MINGW* && "$(uname -s)" != MSYS* ]]; then
    echo 'FAIL: symlink guard could not be tested on this POSIX host' >&2; exit 1
else
    echo 'SKIP: symlink guard requires Windows symlink privilege'
fi

echo 'PASS: checkpoint size, SHA256, symlink and promotion guards'
