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

# Assistant First requires search on first boot. The bundled SearXNG provider
# is the default, while an explicit or saved native-provider selection remains
# a valid one-provider alternative on rerun.
fixture_root="$(mktemp -d)"
trap 'rm -rf -- "$fixture_root"' EXIT

check_assistant_search() (
    local requested="$1" saved="$2" expected="$3"
    INSTALL_DIR="$fixture_root"
    ODS_INSTALL_PROFILE=assistant-first
    PIXEL_WEB_SEARCH_PROVIDER="$requested"
    if [[ "$saved" == old-no-provider ]]; then
        printf 'ODS_INSTALL_PROFILE=assistant-first\n' > "$fixture_root/.env"
    elif [[ -n "$saved" ]]; then
        printf 'PIXEL_WEB_SEARCH_PROVIDER=%s\n' "$saved" > "$fixture_root/.env"
    else
        rm -f -- "$fixture_root/.env"
    fi
    declare -A selected=()
    _sync_extension_compose() { selected["$2"]="$1"; }
    ai_bad() { echo "FAIL: $*" >&2; }
    source /dev/stdin <<< "$block"
    [[ "${selected[searxng]:-missing}" == "$expected" &&
       "$ENABLE_SEARXNG" == "$expected" &&
       "$ENABLE_WEB_SEARCH" == "$expected" ]] || {
        echo "FAIL: Assistant First search selection ($requested/$saved)" >&2
        exit 1
    }
    if [[ "$expected" == true ]]; then
        [[ "$PIXEL_WEB_SEARCH_PROVIDER" == searxng ]]
    else
        [[ "$PIXEL_WEB_SEARCH_PROVIDER" == parallel-free ]]
    fi
)

check_assistant_search '' '' true
check_assistant_search parallel-free '' false
check_assistant_search '' old-no-provider false
check_assistant_search '' parallel-free false
check_assistant_search searxng parallel-free true

# Persisted provider ambiguity or a symlink must fail instead of silently
# selecting a different provider from the post-install resolver.
check_rejected_saved_search() (
    INSTALL_DIR="$fixture_root"
    ODS_INSTALL_PROFILE=assistant-first
    PIXEL_WEB_SEARCH_PROVIDER=""
    declare -A selected=()
    _sync_extension_compose() { selected["$2"]="$1"; }
    ai_bad() { :; }
    if source /dev/stdin <<< "$block"; then
        echo "FAIL: unsafe persisted Assistant First search provider accepted" >&2
        exit 1
    fi
)
printf 'PIXEL_WEB_SEARCH_PROVIDER=searxng\nPIXEL_WEB_SEARCH_PROVIDER=parallel-free\n' > "$fixture_root/.env"
check_rejected_saved_search
mv -- "$fixture_root/.env" "$fixture_root/.env-target"
ln -s -- "$fixture_root/.env-target" "$fixture_root/.env"
check_rejected_saved_search

echo 'PASS: 64 legacy combinations and Assistant First search-provider selection'
