#!/usr/bin/env bash
# Authentication is read from a private file without exposing the key in curl argv.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/external-services.sh"
test_dir="$(mktemp -d)"
trap 'rm -f -- "$test_dir/key" "$test_dir/link"; rmdir -- "$test_dir"' EXIT
key_file="$test_dir/key"
printf 'test-secret-123\n' >"$key_file"
chmod 600 "$key_file"

[[ "$(external_llm_read_api_key "$key_file")" == 'test-secret-123' ]]
chmod 644 "$key_file"
if external_llm_read_api_key "$key_file" >/dev/null 2>&1; then
    echo 'FAIL: world-readable key accepted' >&2; exit 1
fi
chmod 600 "$key_file"
ln -s "$key_file" "$test_dir/link"
if external_llm_read_api_key "$test_dir/link" >/dev/null 2>&1; then
    echo 'FAIL: symlink key accepted' >&2; exit 1
fi
printf 'first\nsecond\n' >"$key_file"
if external_llm_read_api_key "$key_file" >/dev/null 2>&1; then
    echo 'FAIL: multi-line key accepted' >&2; exit 1
fi
printf 'test-secret-123\n' >"$key_file"

curl() {
    local arg header_file='' url="${*: -1}" header
    for arg in "$@"; do
        [[ "$arg" != *test-secret-123* ]] || { echo 'FAIL: key leaked into curl argv' >&2; return 1; }
    done
    [[ "$1" == '-H' && "$2" == @/dev/fd/* ]] || { echo 'FAIL: missing private header stream' >&2; return 1; }
    header_file="${2#@}"
    IFS= read -r header <"$header_file"
    [[ "$header" == 'Authorization: Bearer test-secret-123' ]] || return 1
    case "$url" in
        */v1/models) printf '{"data":[{"id":"test-model"}]}' ;;
        */v1/chat/completions) : ;;
        *) return 1 ;;
    esac
}

EXTERNAL_LLM_API_KEY_FILE="$key_file"
[[ "$(external_llm_models openai-compatible http://127.0.0.1:18080)" == test-model ]]
external_llm_probe_completion http://127.0.0.1:18080 test-model
echo 'PASS: external model discovery and completion use private key stream'
