#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/preflight-llm-route.sh
source "$ROOT_DIR/lib/preflight-llm-route.sh"

assert_route() {
    local expected="$1" mode="$2" url="$3" external="$4" managed="$5" actual=local
    if (
        export ODS_MODE="$mode" EXTERNAL_LLM_URL="$url"
        export LEMONADE_EXTERNAL="$external" AMD_INFERENCE_MANAGED="$managed"
        ods_preflight_uses_litellm
    ); then actual=litellm; fi
    if [[ "$actual" != "$expected" ]]; then
        printf 'FAIL expected %s route, got %s\n' "$expected" "$actual" >&2
        exit 1
    fi
}

assert_route local local '' false true
assert_route litellm local http://model.example:8080 false true
assert_route litellm cloud '' false true
assert_route litellm lemonade '' false false
assert_route litellm local '' true true

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
if [[ "$(grep -Fc '[[ "$sid" == "llama-server" || "$sid" == "model-router" ]] && ods_preflight_uses_litellm' "$ROOT_DIR/ods-cli")" != 2 ]]; then
    printf 'FAIL text and JSON status must omit disabled managed inference\n' >&2
    exit 1
fi
printf 'ODS preflight LLM route tests passed\n'
