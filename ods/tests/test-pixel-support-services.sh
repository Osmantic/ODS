#!/usr/bin/env bash
# Exercise the feature phase's actual shared-service selection block.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
block="$(sed -n '/^    _pixel_support_services=/,/^    unset _pixel_support_services$/p' "$root/installers/phases/03-features.sh")"
[[ -n "$block" ]] || { echo 'FAIL: missing shared-service selection block'; exit 1; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# How the model switchboard mode reaches the installer: nothing set, the
# caller's ODS_MODEL_SWITCHBOARD, an existing .env line, or both. Phase 06
# lets an existing .env value win, then the caller, then "enabled"; any value
# other than legacy or observe is treated as enabled.
switchboard_cases=(
    "none"
    "caller:enabled"
    "caller:legacy"
    "caller:observe"
    "caller:unrecognized"
    "env:legacy"
    "env:observe"
    "env:enabled"
    "env:'observe'"
    "env:legacy # rollback"
    "both:legacy:enabled"
    "both:enabled:legacy"
)

expected_mode() {
    local case="$1" env_value="" caller_value="" mode
    case "$case" in
        none) ;;
        caller:*) caller_value="${case#caller:}" ;;
        env:*) env_value="${case#env:}" ;;
        both:*) env_value="${case#both:}"; caller_value="${env_value#*:}"; env_value="${env_value%%:*}" ;;
    esac
    env_value="${env_value//\'/}"
    env_value="${env_value%% #*}"
    mode="${env_value:-${caller_value:-enabled}}"
    [[ "$mode" == legacy || "$mode" == observe ]] || mode=enabled
    printf '%s\n' "$mode"
}

flags=(ENABLE_RECOMMENDED ENABLE_PIXEL_RUNTIME ENABLE_PERPLEXICA ENABLE_HERMES ENABLE_OPENCLAW)
checked=0
for switchboard_case in "${switchboard_cases[@]}"; do
    install_dir="$tmp_dir/install-${checked}"
    mkdir -p "$install_dir"
    caller_value=""
    case "$switchboard_case" in
        caller:*) caller_value="${switchboard_case#caller:}" ;;
        env:*) printf 'ODS_MODEL_SWITCHBOARD=%s\n' "${switchboard_case#env:}" > "$install_dir/.env" ;;
        both:*)
            both="${switchboard_case#both:}"
            printf 'ODS_MODEL_SWITCHBOARD=%s\n' "${both%%:*}" > "$install_dir/.env"
            caller_value="${both#*:}"
            ;;
    esac
    mode="$(expected_mode "$switchboard_case")"
    for ((mask=0; mask<64; mask++)); do
        (
            INSTALL_DIR="$install_dir"
            unset ODS_MODEL_SWITCHBOARD
            [[ -z "$caller_value" ]] || ODS_MODEL_SWITCHBOARD="$caller_value"
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

            # LiteLLM is required by Pixel, an external LLM, recommended
            # services, and every consumer the enabled switchboard routes.
            expected_gateway=false
            expected_search=false
            if ((mask & 35)) || [[ "$mode" == enabled ]]; then expected_gateway=true; fi
            if ((mask & 31)); then expected_search=true; fi
            [[ "${selected[litellm]:-missing}" == "$expected_gateway" ]] || {
                echo "FAIL: LiteLLM selection for mask $mask with switchboard $switchboard_case ($mode)"; exit 1;
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
    checked=$((checked + 1))
done
# Literal whitespace and hashes inside quotes are not valid routing modes.
# These fixed expectations deliberately do not reuse expected_mode's parser.
for literal in "'legacy # literal'" '"observe # literal"' "' legacy '" '"ob serve"'; do
    (
        INSTALL_DIR="$tmp_dir/literal"
        mkdir -p "$INSTALL_DIR"
        printf 'ODS_MODEL_SWITCHBOARD=%s\n' "$literal" > "$INSTALL_DIR/.env"
        ODS_MODEL_SWITCHBOARD=legacy
        EXTERNAL_LLM_URL=""
        for flag in "${flags[@]}"; do printf -v "$flag" '%s' false; done
        declare -A selected=()
        _sync_extension_compose() { selected["$2"]="$1"; }
        source /dev/stdin <<< "$block"
        [[ "${selected[litellm]:-missing}" == true ]] || {
            echo "FAIL: invalid literal $literal must keep the enabled gateway"; exit 1;
        }
    )
done

echo "PASS: all 64 Pixel/external/shared-service consumer combinations under ${checked} switchboard configurations and quoted literal regressions"
