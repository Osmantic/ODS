#!/usr/bin/env bash
# Exercise the feature phase's actual shared-service selection block.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
block="$(sed -n '/^    _pixel_support_services=/,/^    unset _pixel_support_services$/p' "$root/installers/phases/03-features.sh")"
[[ -n "$block" ]] || { echo 'FAIL: missing shared-service selection block'; exit 1; }

flags=(ENABLE_RECOMMENDED ENABLE_PIXEL_RUNTIME ENABLE_PERPLEXICA ENABLE_HERMES ENABLE_OPENCLAW)
for ((mask=0; mask<64; mask++)); do
    (
        EXTERNAL_LLM_URL=""
        if ((mask & 32)); then EXTERNAL_LLM_URL=http://10.0.2.2:18080; fi
        for index in "${!flags[@]}"; do
            value=false
            if ((mask & (1 << index))); then value=true; fi
            printf -v "${flags[index]}" '%s' "$value"
        done
        declare -A selected=()
        _sync_extension_compose() { selected["$2"]="$1"; }
        # The block is trusted repository source; only compose selection is mocked.
        source /dev/stdin <<< "$block"

        expected_gateway=false
        expected_search=false
        if ((mask & 35)); then expected_gateway=true; fi
        if ((mask & 31)); then expected_search=true; fi
        [[ "${selected[litellm]:-missing}" == "$expected_gateway" ]] || {
            echo "FAIL: LiteLLM selection for mask $mask"; exit 1;
        }
        [[ "${selected[searxng]:-missing}" == "$expected_search" &&
           "$ENABLE_SEARXNG" == "$expected_search" &&
           "$ENABLE_WEB_SEARCH" == "$expected_search" ]] || {
            echo "FAIL: search selection for mask $mask"; exit 1;
        }
        [[ "${selected[token-spy]:-missing}" == "$ENABLE_RECOMMENDED" ]] || {
            echo "FAIL: Token Spy selection for mask $mask"; exit 1;
        }
    )
done
echo 'PASS: all 64 Pixel/external/shared-service consumer combinations'
