#!/usr/bin/env bash
# The macOS installer has no API mode yet. Its API-mode flags must say so,
# name the way to connect an API after installing, point at help, and stop
# during option parsing, before anything is installed or changed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

for flag in --external-llm-url --external-llm-key-file --external-llm-key-env --no-external-llm; do
    if output="$(HOME="$fixture" bash "$ROOT/installers/macos/install-macos.sh" "$flag" value 2>&1)"; then
        fail "$flag was accepted"
    fi
    grep -qF "API mode ($flag) is not in the macOS installer yet. Nothing was changed." <<<"$output" \
        || fail "$flag did not say API mode is unavailable: $output"
    grep -qF "ods remote-provider configure --base-url URL --model MODEL --api-key-file FILE" <<<"$output" \
        || fail "$flag did not name how to connect an API after installing: $output"
    grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "$flag did not point at help: $output"
done
[[ -z "$(ls -A "$fixture")" ]] || fail "the refused flags changed files: $(ls -A "$fixture")"

echo 'PASS: macOS API-mode flags explain the alternative and change nothing'
