#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/preflight-llm-route.sh
source "$ROOT_DIR/lib/preflight-llm-route.sh"

assert_route() {
    local expected="$1" mode="$2" url="$3" native="$4" actual=local
    if (
        export ODS_MODE="$mode" EXTERNAL_LLM_URL="$url" NATIVE_LLM_BASE_URL="$native"
        ods_preflight_uses_litellm
    ); then actual=litellm; fi
    if [[ "$actual" != "$expected" ]]; then
        printf 'FAIL expected %s route for mode=%s url=%s native=%s, got %s\n' \
            "$expected" "$mode" "$url" "$native" "$actual" >&2
        exit 1
    fi
}

assert_route local local '' ''
assert_route litellm local http://model.example:8080 ''
assert_route litellm cloud '' ''
# The Windows Portal's llama-server runs outside the stack; LiteLLM holds its key.
assert_route litellm local '' http://localhost:8080
# A Lemonade-era .env reads as local (the migration rewrites it).
assert_route local lemonade '' ''

assert_skipped() {
    local expected="$1" sid="$2" mode="$3" native="$4" actual=shown
    if (
        export ODS_MODE="$mode" EXTERNAL_LLM_URL='' NATIVE_LLM_BASE_URL="$native"
        ods_status_skips_managed_inference "$sid"
    ); then actual=skipped; fi
    [[ "$actual" == "$expected" ]] || {
        printf 'FAIL status for %s (mode=%s native=%s) should be %s, got %s\n' \
            "$sid" "$mode" "$native" "$expected" "$actual" >&2
        exit 1
    }
}
assert_skipped shown llama-server local ''
assert_skipped skipped llama-server cloud ''
assert_skipped skipped model-router cloud ''
assert_skipped skipped llama-server local http://localhost:8080
assert_skipped shown model-router local http://localhost:8080

grep -Fq 'if ods_preflight_uses_litellm; then' "$ROOT_DIR/ods-preflight.sh" || {
    printf 'FAIL preflight did not use the shared route selector\n' >&2
    exit 1
}
grep -Fq 'if ods_preflight_uses_litellm; then' "$ROOT_DIR/scripts/ods-preflight.sh" || {
    printf 'FAIL quick preflight did not use the shared route selector\n' >&2
    exit 1
}
grep -Fq 'LLM_CONTAINER="ods-litellm"' "$ROOT_DIR/scripts/ods-preflight.sh" || {
    printf 'FAIL quick preflight did not check the external model gateway\n' >&2
    exit 1
}
if [[ "$(grep -Fc 'if ods_status_skips_managed_inference "$sid"; then' "$ROOT_DIR/ods-cli")" != 2 ]]; then
    printf 'FAIL text and JSON status must omit disabled managed inference\n' >&2
    exit 1
fi
printf 'ODS preflight LLM route tests passed\n'
