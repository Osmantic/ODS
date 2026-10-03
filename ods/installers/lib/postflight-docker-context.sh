#!/usr/bin/env bash
# Run post-install validation with the invoking user's freshly granted docker
# group when the original installer shell still has its old supplementary set.
# This never elevates privileges or substitutes a successful daemon check.

ods_postflight_needs_docker_group_refresh() {
    local account current_groups account_groups
    command -v sg >/dev/null 2>&1 || return 1
    account="$(id -un 2>/dev/null)" || return 1
    current_groups=" $(id -nG 2>/dev/null) " || return 1
    account_groups=" $(id -nG "$account" 2>/dev/null) " || return 1
    [[ "$current_groups" != *" docker "* && "$account_groups" == *" docker "* ]]
}

ods_postflight_run_docker_check() {
    local script="$1"
    shift
    if ods_postflight_needs_docker_group_refresh; then
        # Current callers pass zero or one path argument. Environment variables
        # avoid evaluating installer-controlled paths as shell source text.
        if [[ $# -eq 0 ]]; then
            ODS_POSTFLIGHT_SCRIPT_PATH="$script" \
                sg docker -c 'exec bash "$ODS_POSTFLIGHT_SCRIPT_PATH"'
        elif [[ $# -eq 1 ]]; then
            ODS_POSTFLIGHT_SCRIPT_PATH="$script" ODS_POSTFLIGHT_SCRIPT_ARG="$1" \
                sg docker -c 'exec bash "$ODS_POSTFLIGHT_SCRIPT_PATH" "$ODS_POSTFLIGHT_SCRIPT_ARG"'
        else
            return 2
        fi
    else
        bash "$script" "$@"
    fi
}
